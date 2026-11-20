"""오브젝트 스토리지 어댑터. 클라이언트 하나와 서명 방식 하나만 쓴다.

NCP Object Storage는 내부·외부 주소 구분이 없어 어디서 서명하든 URL이 같으므로
클라이언트와 presign을 하나로 둔다.
주의: 호출 위치별로 엔드포인트가 다른 클라이언트를 두지 않는다. SigV4는 host를 서명에 넣으므로
호스트가 둘인 스토리지에서는 잘못 고른 클라이언트의 서명을 브라우저가 403으로 거절한다.
대용량 전송용 클라이언트는 시한과 재시도 횟수만 다르다(`_config`).
테스트에서는 moto(ThreadedMotoServer)를 가리키는 저장소 연결이 NCP를 대신한다(`tests/_storage.py`).

프로젝트가 저장소 연결과 버킷을 고른다(`platform_credentials.StorageTarget`).
쓰는 경로(업로드 서명·멀티파트·산출물 저장)는 프로젝트나 영상의 저장소 값(`Store`)을 반드시 받는다.
URI에서 버킷만 알 수 있는 경로는 그 버킷으로 연결을 찾는다
(`platform_credentials.storage_for_bucket`).
기본 저장소는 없다. 영상 행을 만들 때 저장소를 `extra.storage`에 고정 기록하고(`pin_for`)
그 영상의 산출물은 기록된 버킷에 둔다(`bucket_of`).

지키는 규칙은 다음과 같다.
- key는 호출자가 보낸 값을 쓰지 않고 항상 서버가 정한다.
- HEAD로 실제 업로드 여부와 크기를 확인한다. 클라이언트가 보낸 크기는 쓰지 않는다.
"""
import logging
import threading
from typing import Final

import boto3
from botocore.client import Config

from api_gateway.config import settings
from api_gateway.services import platform_credentials

log = logging.getLogger(__name__)

#: 저장소 클라이언트를 만드는 데 필요한 값(버킷 포함).
Store = platform_credentials.StorageCredentials

#: 영상 행 `extra` 안에서 그 영상의 저장소 고정 기록(`{connection_id, bucket}`)을 담는 키.
PIN_KEY: Final = "storage"
#: (연결 id, 대용량 여부)별로 마지막에 만든 클라이언트와 그 자격증명 지문.
_clients: dict[tuple[str, bool], tuple[tuple[str, str, str, str], object]] = {}
_cache_lock = threading.Lock()

#: 연결 시한(초). 정상 엔드포인트는 1초 안에 연결하므로 응답 없는 엔드포인트를 빨리 실패로 돌린다.
CONNECT_TIMEOUT_S: Final = 5
#: 일반 호출의 읽기 시한(초). HTTP 요청 안에서 부르는 HEAD·목록·작은 객체 읽기와 쓰기가 쓴다.
READ_TIMEOUT_S: Final = 60
#: 대용량 전송의 읽기 시한(초). 서버 복사와 멀티파트 완료는 객체가 클수록 응답이 늦게 온다.
BULK_READ_TIMEOUT_S: Final = 600
#: 일반 호출이 첫 시도 뒤 다시 보내는 횟수. 연결할 수 없으면 최악 약 20초 안에 실패한다.
RETRIES: Final = 2
#: 대용량 전송이 첫 시도 뒤 다시 보내는 횟수. 워커에서만 돌아 화면을 붙잡지 않는다.
BULK_RETRIES: Final = 4


def _config(*, bulk: bool) -> Config:
    """클라이언트 설정을 만든다. 시한과 재시도 횟수만 `bulk`에 따라 다르다.

    botocore 기본값(연결 60초, legacy 재시도 4회)이면 응답 없는 엔드포인트 하나가 HTTP 요청을
    몇 분씩 붙잡는다. standard 재시도는 429·5xx·연결 오류만 지수 백오프로 다시 보낸다.

    주의: `request_checksum_calculation="when_required"`를 빼지 않는다.
    botocore 1.36 이상은 PutObject에 CRC32 체크섬을 기본으로 붙이는데,
    NCP는 체크섬이 붙은 요청을 권한 오류처럼 AccessDenied로 거부한다.
    """
    return Config(
        signature_version="s3v4",
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        connect_timeout=CONNECT_TIMEOUT_S,
        read_timeout=BULK_READ_TIMEOUT_S if bulk else READ_TIMEOUT_S,
        retries={"max_attempts": BULK_RETRIES if bulk else RETRIES, "mode": "standard"},
    )


def _client(store: Store, *, bulk: bool = False):
    """저장소 값의 boto3 S3 클라이언트를 돌려준다. 모든 환경이 함께 쓴다(NCP OSS, 테스트는 moto).

    클라이언트는 연결과 용도마다 하나를 캐시하고, 키·엔드포인트가 바뀌면 다음 호출에서 다시 만든다.
    `bulk`가 참이면 대용량 전송용 시한(`BULK_READ_TIMEOUT_S`)을 쓴다. 서버 복사·멀티파트 완료·
    패키지 업로드·본문 스트림 읽기가 그 용도다.
    """
    creds = store
    slot, key = (creds.connection_id, bulk), creds.client_key
    cached = _clients.get(slot)
    if cached is not None and cached[0] == key:
        return cached[1]
    with _cache_lock:
        cached = _clients.get(slot)
        if cached is None or cached[0] != key:
            _clients[slot] = (key, boto3.client(
                "s3",
                endpoint_url=creds.endpoint,
                aws_access_key_id=creds.access_key,
                aws_secret_access_key=creds.secret_key,
                region_name=creds.region,
                config=_config(bulk=bulk),
            ))
        return _clients[slot][1]


def _for_bucket(bucket: str) -> Store:
    """버킷을 읽을 저장소 값을 찾는다. URI에서 버킷만 알 수 있는 경로가 쓴다.

    Raises:
        platform_credentials.CredentialsMissingError: 쓸 수 있는 저장소 자격증명이 없는 경우.
    """
    return platform_credentials.storage_for_bucket(bucket)


def for_project(project_settings: dict | None) -> Store:
    """프로젝트가 고른 저장소 값을 돌려준다.

    Raises:
        platform_credentials.CredentialsMissingError: 저장소를 고르지 않았거나 고른 연결을 읽지
            못한 경우.
    """
    target = platform_credentials.storage_target_of(project_settings)
    return platform_credentials.storage_for(target)


def bucket_for_project(project_settings: dict | None) -> str | None:
    """프로젝트가 고른 버킷 이름을 돌려준다. 고르지 않았으면 `None`이다."""
    return platform_credentials.project_bucket(project_settings) or None


def pin_for(project_settings: dict | None) -> dict | None:
    """영상 행을 만들 때 `extra[PIN_KEY]`에 넣을 저장소 고정 기록을 만든다.

    프로젝트가 저장소를 고르지 않았으면 `None`이다.
    고정 기록이 없는 영상은 저장소를 쓰는 경로에서 409로 실패한다.
    """
    target = platform_credentials.storage_target_of(project_settings)
    return target.as_dict() if target is not None else None


def pin_of(extra: dict | None) -> platform_credentials.StorageTarget | None:
    """영상 행 `extra`의 저장소 고정 기록을 읽는다. 기록이 없으면 `None`이다."""
    raw = (extra or {}).get(PIN_KEY)
    if not isinstance(raw, dict):
        return None
    target = platform_credentials.StorageTarget(
        connection_id=str(raw.get("connection_id") or "").strip() or None,
        bucket=str(raw.get("bucket") or "").strip() or None)
    return None if target.empty else target


async def ensure_loaded(db, extra: dict | None) -> None:
    """영상에 기록한 저장소 연결·버킷이 이 프로세스 메모리에 없으면 다시 읽는다.

    워커와 다른 api 프로세스의 메모리에는 다음 갱신 주기까지 방금 고른 연결과 버킷이 없어
    그대로 서명하면 409로 실패한다. 영상의 저장소를 쓰기 전에 부른다.
    """
    await platform_credentials.ensure_storage_loaded(db, pin_of(extra))


def for_video(extra: dict | None) -> Store:
    """영상에 고정 기록한 저장소 값을 돌려준다.

    Raises:
        platform_credentials.CredentialsMissingError: 기록이 없거나 기록된 연결을 읽지 못한 경우.
    """
    return platform_credentials.storage_for(pin_of(extra))


def for_video_or_none(extra: dict | None) -> Store | None:
    """영상의 저장소 값을 돌려준다. 쓸 수 있는 값이 없으면 `None`이다. 정리 같은 경로가 쓴다."""
    return platform_credentials.storage_for_or_none(pin_of(extra))


def bucket_of(extra: dict | None) -> str:
    """영상의 산출을 둘 버킷 이름을 돌려준다. 영상에 고정 기록한 버킷이다.

    Raises:
        platform_credentials.CredentialsMissingError: 고정 기록에 버킷이 없는 경우.
    """
    pin = pin_of(extra)
    if pin is not None and pin.bucket:
        return pin.bucket
    raise platform_credentials.CredentialsMissingError(
        platform_credentials.STORAGE_NOT_CONFIGURED)


def _adapter(store: Store, *, bulk: bool = False):
    """스토리지 계약을 구현한 백엔드 어댑터를 돌려준다.

    이 모듈이 호출부의 단일 진입점이라서 GCS 같은 다른 백엔드는 이 함수가 다른 어댑터를
    돌려주게 하면 붙일 수 있다.

    주의: 클라이언트는 `_client()`가 만든 것을 그대로 넘긴다. 새로 만들면 `_config()`의
    체크섬 설정과 시한이 빠져 NCP에서만 AccessDenied가 난다.
    """
    from api_gateway.services.stacks.storage_adapters.ncp import NcpStorage

    return NcpStorage(_client(store, bulk=bulk), ttl_seconds=settings.s3_presign_ttl_seconds)


def _ref(bucket: str, key: str):
    """bucket과 key를 스토리지 계약의 `ObjectRef`로 묶는다."""
    from reborn_contracts.storage_backend import ObjectRef

    return ObjectRef(bucket=bucket, key=key)


def upload_bucket(store: Store) -> str:
    """저장소 값의 버킷 이름을 돌려준다. 원본 영상과 서버가 만든 산출물을 올리는 버킷이다.

    프로젝트가 고른 버킷이 MI 워크스페이스 연동 버킷과 같은지는 저장할 때
    `stacks.project_storage`가 확인한다. 연동 버킷은 설정값이 아니라 워크스페이스 API 응답으로
    정한다(`api/v1/storage.list_storage_buckets`).
    주의: 환경별로 버킷을 가르는 분기를 두지 않는다. 그런 분기는 테스트 환경에서 검증되지 않는다.
    """
    return store.bucket


# ── 멀티파트 업로드 ─────────────────────────────────────────────────────────
# 단일 POST는 전송이 끊기면(예: 3GB 파일이 90%에서) 처음부터 다시 올려야 하므로 멀티파트를 쓴다.
# 멀티파트는 파트 단위 재시도·병렬 전송·이어받기를 표준으로 제공한다.
# 파트 URL은 필요한 번호만 그때그때 서명한다. 파트 수백 개를 한 번에 서명하면
# 느린 회선에서는 마지막 파트를 올리기 전에 서명이 만료된다.


def create_multipart(
    key: str, content_type: str = "video/mp4", *, store: Store,
) -> str:
    """멀티파트 업로드를 시작하고 upload_id를 돌려준다."""
    r = _client(store).create_multipart_upload(
        Bucket=upload_bucket(store), Key=key, ContentType=content_type)
    return r["UploadId"]


def presign_parts(
    key: str, upload_id: str, part_numbers: list[int], *, store: Store,
) -> dict[int, str]:
    """파트별 presigned PUT URL을 만든다. 만료를 피하도록 요청한 번호만 서명한다."""
    c, bucket = _client(store), upload_bucket(store)
    return {
        n: c.generate_presigned_url(
            "upload_part",
            Params={"Bucket": bucket, "Key": key,
                    "UploadId": upload_id, "PartNumber": n},
            ExpiresIn=settings.s3_presign_ttl_seconds,
        )
        for n in part_numbers
    }


def list_parts(key: str, upload_id: str, *, store: Store) -> list[dict]:
    """이미 올라간 파트 목록을 돌려준다. 이어받기와 완료에 쓴다.

    클라이언트는 ETag를 보관하지 않는다. 다시 접속한 브라우저는 이 목록으로 올라간 파트 번호를
    확인하고, 완료에 쓰는 파트 목록도 서버가 이 함수로 만들어 클라이언트 상태와 어긋나지 않게 한다.
    """
    c, bucket, out, marker = _client(store), upload_bucket(store), [], 0
    while True:
        r = c.list_parts(Bucket=bucket, Key=key, UploadId=upload_id,
                         PartNumberMarker=marker)
        out.extend(r.get("Parts") or [])
        if not r.get("IsTruncated"):
            return out
        marker = r["NextPartNumberMarker"]


def complete_multipart(
    key: str, upload_id: str, parts: list[dict], *, store: Store,
) -> None:
    """파트를 하나의 객체로 합친다.

    `parts`는 [{"PartNumber", "ETag"}] 형식이고 번호 오름차순으로 정렬해 보낸다.
    파트가 많으면 합치는 동안 응답이 늦으므로 대용량 시한을 쓴다.
    일반 시한으로 끊고 다시 보내면 이미 합친 업로드에 `NoSuchUpload`가 돌아온다.
    """
    _client(store, bulk=True).complete_multipart_upload(
        Bucket=upload_bucket(store), Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [
            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]}
            for p in sorted(parts, key=lambda x: x["PartNumber"])
        ]},
    )


def abort_multipart(key: str, upload_id: str, *, store: Store) -> None:
    """완료되지 않은 멀티파트 업로드를 중단하고 올라간 파트를 정리한다.

    올라간 파트는 완료 전까지 객체 목록에 보이지 않지만 과금된다.
    업로드가 끝나지 않은 영상을 삭제할 때(`video.soft_delete`, 등록 시트의 `큐에서 제거`·`중단`)
    부른다. 탭을 닫아 방치된 멀티파트는 앱이 정리하지 못하고 버킷 라이프사이클 규칙으로만
    정리할 수 있다.
    """
    try:
        _client(store).abort_multipart_upload(
            Bucket=upload_bucket(store), Key=key, UploadId=upload_id)
    except Exception as e:  # noqa: BLE001 — 정리 실패가 취소를 막으면 안 된다
        log.warning("멀티파트 중단 실패 key=%s upload_id=%s: %s", key, upload_id, e)


def presign_get(key: str, *, bucket: str, filename: str | None = None) -> str:
    """앱이 만든 객체의 presigned GET URL을 만든다.

    썸네일·납품 패키지 같은 파생물에 쓴다. `bucket`은 그 산출물과 함께 기록한 버킷이다.
    `filename`을 주면 내려받을 파일 이름을 서명에 넣는다(`response-content-disposition`).
    이름이 없으면 모든 납품 패키지가 객체 키 끝의 `package.zip`으로 저장되어
    전량본·샘플·재전달분을 구별할 수 없다.

    어댑터 계약(`presign_get(ref, ttl)`)에는 응답 헤더 인자가 없어 파일 이름을 줄 때만
    클라이언트를 직접 부른다. `response-content-disposition`은 S3 호환 스토리지의 표준 쿼리다.
    """
    if not filename:
        return presign_get_for(bucket=bucket, key=key)
    from urllib.parse import quote

    # RFC 6266에 따라 ASCII 대체 이름과 UTF-8 원래 이름을 함께 넣는다.
    # 지금 쓰는 이름은 slug와 숫자로만 이뤄져 두 값이 같다.
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return _client(_for_bucket(bucket)).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket, "Key": key,
            "ResponseContentDisposition": disposition,
        },
        ExpiresIn=settings.s3_presign_ttl_seconds,
    )


def presign_get_or_none(key: str, *, bucket: str | None) -> str | None:
    """앱이 만든 객체의 presigned GET URL을 만든다. 버킷 기록이나 읽을 연결이 없으면 `None`이다.

    목록 응답의 썸네일·첨부 미리보기처럼 주소가 없어도 화면을 그릴 수 있는 경우에 쓴다.
    예외를 던지면 썸네일 하나 때문에 목록 전체가 409로 실패한다.
    """
    if not bucket or platform_credentials.storage_for_bucket_or_none(bucket) is None:
        return None
    return presign_get(key, bucket=bucket)


def presign_put_for(
    *, bucket: str, key: str, content_type: str = "video/mp4", ttl_seconds: int | None = None,
    store: Store | None = None,
) -> str:
    """임의 bucket/key의 presigned PUT URL을 만든다.

    잡 페이로드에 담아 DB에 붙지 않는 워커가 자격 증명 없이 산출물을 올리게 한다.
    Content-Type이 서명에 포함되므로 워커는 같은 헤더로 PUT해야 한다.
    ttl은 인코딩 시간보다 길어야 하므로 잡을 만드는 `build_jobs`가 정한다.
    """
    return _adapter(store or _for_bucket(bucket)).presign_put(
        _ref(bucket, key), ttl_seconds=ttl_seconds, content_type=content_type)


def presign_get_for(*, bucket: str, key: str, ttl_seconds: int | None = None) -> str:
    """임의 bucket/key의 presigned GET URL을 만든다. 브라우저와 서버가 함께 쓴다.

    엔드포인트가 하나라서 호출 위치를 구분하지 않는다.
    """
    return _adapter(_for_bucket(bucket)).presign_get(_ref(bucket, key), ttl_seconds=ttl_seconds)


def head(key: str, *, store: Store):
    """업로드 대상 버킷의 객체 메타를 조회해 업로드 완료를 확인한다. 객체가 없으면 `None`이다.

    boto3 응답 dict 대신 `ObjectInfo`를 돌려준다.
    호출부가 `ContentLength` 같은 벤더 키를 읽으면 백엔드를 바꿀 수 없다.
    """
    return _adapter(store).head(_ref(upload_bucket(store), key))


def head_in(*, bucket: str, key: str):
    """임의 버킷의 객체 메타를 조회한다. 객체가 없으면 `None`이다.

    `head`는 저장소 값의 버킷만 조회하므로 원본이 다른 버킷에 있는 영상(버킷 등록 등)은
    이 함수로 확인한다. 분석본 정리와 비식별화 산출 확인이 쓴다.

    404만 `None`으로 처리한다. 403·순단은 예외를 던져 호출자가 없는 경우와 확인하지 못한 경우를
    구분하게 한다. `ObjectInfo`를 돌려준다.
    """
    return _adapter(_for_bucket(bucket)).head(_ref(bucket, key))


def put_bytes(
    key: str, body: bytes, content_type: str = "application/octet-stream",
    *, store: Store,
) -> str:
    """bytes를 저장소 버킷의 key에 바로 올리고 그 버킷 이름을 돌려준다.

    썸네일·문의 첨부처럼 서버가 만든 파일에 쓴다. 호출부는 돌려받은 버킷을 키와 함께 기록한다.
    """
    bucket = upload_bucket(store)
    _adapter(store).put_bytes(_ref(bucket, key), body, content_type=content_type)
    return bucket


def get_object_body(*, bucket: str, key: str):
    """객체 본문 스트림을 돌려준다. 체크섬 계산처럼 디스크에 내려받지 않고 읽을 때 쓴다.

    읽는 도중 응답이 잠깐 멈출 수 있어 대용량 시한을 쓴다.
    """
    return _client(_for_bucket(bucket), bulk=True).get_object(Bucket=bucket, Key=key)["Body"]


def upload_file(
    local_path, key: str, content_type: str = "video/mp4", *, store: Store,
) -> str:
    """로컬 파일을 저장소 버킷에 올리고 그 버킷 이름을 돌려준다. 납품 패키지를 저장할 때 쓴다.

    대용량 파일은 boto3가 자동으로 멀티파트 업로드로 바꾸고(ETag에 `-N` 접미사가 붙는다)
    대용량 시한을 쓴다.
    """
    bucket = upload_bucket(store)
    _client(store, bulk=True).upload_file(
        Filename=str(local_path), Bucket=bucket, Key=key,
        ExtraArgs={"ContentType": content_type},
    )
    return bucket


def s3_uri(key: str, *, store: Store) -> str:
    """key를 업로드 대상 버킷 기준의 s3://bucket/key URI로 바꾼다."""
    return f"s3://{upload_bucket(store)}/{key}"


def copy_object(*, bucket: str, src_key: str, dst_key: str) -> None:
    """같은 버킷 안에서 객체를 서버 측 복사(server-side copy)한다.

    마스터를 스테이징에서 최종 키로 옮길 때 쓴다. 데이터가 이 프로세스를 거치지 않으므로
    내려받았다 다시 올리는 전송(1시간 영상이면 약 670MiB 왕복)이 없다.
    저장소가 복사를 끝낼 때까지 응답하지 않으므로 대용량 시한을 쓴다.
    """
    _adapter(_for_bucket(bucket), bulk=True).copy(_ref(bucket, src_key), _ref(bucket, dst_key))


def delete_object(*, bucket: str, key: str, store: Store | None = None) -> None:
    """객체를 삭제한다. 스테이징 정리에 쓴다. 없는 키를 지워도 S3는 성공으로 응답한다."""
    _adapter(store or _for_bucket(bucket)).delete(_ref(bucket, key))


# ── 저장소 대상 점검 ────────────────────────────────────────────────────────
#
# 프로젝트 저장소 점검(`stacks.project_storage`)이 그 연결의 키로 버킷에 직접 요청을 보낸다.
# 점검도 업로드·산출물 경로와 같은 클라이언트(`_client`)를 써서
# 점검은 통과하는데 업로드만 실패하는 연결이 생기지 않게 한다.


def check_bucket(store: Store) -> None:
    """버킷에 접근할 수 있는지 HEAD로 확인한다. 실패하면 클라이언트 예외를 그대로 던진다."""
    _client(store).head_bucket(Bucket=store.bucket)


def list_one(store: Store) -> int:
    """버킷 목록을 한 건만 읽어 목록 권한을 확인한다. 읽은 객체 수를 돌려준다."""
    resp = _client(store).list_objects_v2(Bucket=store.bucket, MaxKeys=1)
    return len(resp.get("Contents") or [])


def read_bytes(key: str, *, store: Store) -> bytes:
    """저장소 버킷의 작은 객체를 읽는다. 점검 객체를 되읽을 때 쓴다."""
    return _client(store).get_object(Bucket=store.bucket, Key=key)["Body"].read()


def bucket_cors(store: Store) -> list[dict] | None:
    """버킷의 CORS 규칙을 읽는다. 규칙이 없으면 `None`이다.

    Raises:
        Exception: 규칙이 없는 경우 외의 이유(권한·순단)로 읽지 못한 경우.
            호출부는 결과를 확인하지 못함으로 둔다.
    """
    try:
        return list(_client(store).get_bucket_cors(Bucket=store.bucket).get("CORSRules") or [])
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code == "NoSuchCORSConfiguration":
            return None
        raise


def object_exists(*, bucket: str, key: str, store: Store | None = None) -> bool | None:
    """객체가 실제로 있는지 HEAD로 확인한다.

    MI 요청은 `{bucketName, key}`만 넘기고 MI가 객체를 비동기로 가져가므로, 객체가 없으면
    수 분 뒤 import가 실패해야 드러나고 그 사이 MI에 자산이 하나 생긴다.
    요청 전과 버킷 키 직접 등록 때 먼저 확인한다.

    Args:
        store: 읽을 저장소 값. 비우면 버킷 이름으로 연결을 찾는다.

    Returns:
        bool | None: 객체가 있으면 True, 확실히 없으면(404·NoSuchKey) False,
            권한·네트워크·설정 문제로 확인하지 못하면 None.
            호출자는 정상 요청을 막지 않도록 False일 때만 차단한다.
    """
    try:
        _client(store or _for_bucket(bucket)).head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        status_code = (
            getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        )
        if str(code) in ("404", "NoSuchKey", "NotFound") or status_code == 404:
            return False
        log.warning("object_exists 판단 불가 (%s/%s): %s", bucket, key, exc)
        return None


def list_buckets(maiu_bucket: str | None = None, *, store: Store) -> list[dict]:
    """계정의 버킷 이름 목록을 돌려준다. 프로젝트 설정의 저장소 대상 선택지에 쓴다.

    `is_default`를 붙인 버킷을 화면이 먼저 선택한다. 목록은 `store` 연결의 키로 읽는다.

    Returns:
        list[dict]: `{"name", "is_default", "maiu_linked", "maiu_checked"}`의 이름 오름차순 목록.
            `maiu_checked`가 거짓이면 MI 연동 여부를 조회하지 못했거나(순단) 이 프로젝트 스택이
            MI를 쓰지 않는 경우다. 그때는 `maiu_linked`를 판정에 쓰지 않고 화면도 경고하지 않는다.
    """
    # 기본 버킷은 MI 연동 버킷이다. 화면이 처음 여는 버킷에서 바로 분석을 요청할 수 있다.
    # 연동 버킷을 조회하지 못했으면 저장소 값의 버킷을 기본으로 쓴다.
    default = (maiu_bucket or "").strip() or upload_bucket(store)
    # 연동 버킷은 호출자가 MI에 조회한 값(`maiu_bucket`)을 쓴다. 설정값으로 추정하면
    # 콘솔에서 연동 버킷을 바꿨을 때 경고가 엉뚱한 버킷에 표시된다.
    linked = (maiu_bucket or "").strip()
    names = sorted(b["Name"] for b in _client(store).list_buckets().get("Buckets", []))
    return [
        {"name": n, "is_default": n == default, "maiu_linked": bool(linked) and n == linked,
         # 연동 여부를 판정했으면 True. 구분하지 않으면 MI가 없는 스택에서도 화면이
         # 모든 버킷에 "분석 요청이 거절됩니다"를 표시한다.
         "maiu_checked": bool(linked)}
        for n in names
    ]


def browse_bucket(
    prefix: str = "", max_keys: int = 500, recursive: bool = False, token: str | None = None,
    bucket: str | None = None,
    *,
    store: Store,
) -> dict:
    """버킷의 폴더를 탐색한다. `store` 연결의 키로 읽는다.

    `bucket`을 주지 않으면 저장소 값의 버킷을 탐색한다. "폴더를 골라 하위 영상 분석 요청"
    흐름의 탐색 단계로, delimiter='/'로 현재 prefix 아래의 폴더와 파일을 한 단계만 나열한다.
    한 페이지(`max_keys`)만 조회하고, 더 있으면 `truncated=True`와 `next_token`을 돌려준다.

    `recursive=True`면 Delimiter를 빼고 prefix 아래의 모든 객체를 평면으로 나열한다.
    폴더의 모든 영상 키를 모을 때 쓰며 folders는 빈 목록이다.

    Args:
        token: 이전 응답의 next_token. 다음 페이지를 이어서 조회한다.

    Returns:
        dict: prefix, bucket, folders(list[str]), files(list[{key,size,last_modified}]),
              truncated, next_token(str|None).
    """
    client = _client(store)
    # 호출자가 고른 버킷을 우선한다. 없으면 저장소 버킷을 쓴다.
    bucket = bucket or upload_bucket(store)
    kwargs: dict = {"Bucket": bucket, "MaxKeys": max_keys}
    if not recursive:
        kwargs["Delimiter"] = "/"
    if prefix:
        kwargs["Prefix"] = prefix
    if token:
        kwargs["ContinuationToken"] = token
    resp = client.list_objects_v2(**kwargs)
    folders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    files = [
        {"key": o["Key"], "size": o.get("Size", 0), "last_modified": o.get("LastModified")}
        for o in resp.get("Contents", [])
        if o["Key"] != prefix  # 폴더 마커 자체는 뺀다
    ]
    return {"prefix": prefix, "bucket": bucket, "folders": folders, "files": files,
            "truncated": bool(resp.get("IsTruncated")),
            "next_token": resp.get("NextContinuationToken")}
