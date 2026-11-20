"""원본 체크섬의 스트리밍 해시와 beat 채우기 테스트(`services/media/checksum`)."""
import hashlib

from api_gateway.database import SessionLocal
from api_gateway.services.media.checksum import _parse_uri, fill_missing_checksums, sha256_of
from httpx import AsyncClient
from sqlalchemy import func, select
from tests import _storage
from tests._helpers import new_project, upload_video

from api_gateway.models import Video  # isort: skip


def test_parse_uri_whitelists_schemes():
    """scheme 허용 규칙이 resolver와 같은지 확인한다.

    다른 scheme까지 파싱하면 엉뚱한 버킷을 읽는다.
    """
    assert _parse_uri("s3://bkt/a/b.mp4") == ("bkt", "a/b.mp4")
    assert _parse_uri("ncp://bkt/a.mp4") == ("bkt", "a.mp4")
    assert _parse_uri("https://x/y.mp4") is None
    assert _parse_uri("s3://bkt") is None      # 키가 없다


def test_sha256_streams_the_object():
    """객체를 디스크에 저장하지 않고 스트리밍으로 읽어 해시한다. 값은 `sha256sum` 결과와 같아야 한다."""
    body = b"reborn" * 10_000
    _storage.put_bytes("checksum-test/a.bin", body, content_type="application/octet-stream")
    got = sha256_of(bucket=_storage.BUCKET, key="checksum-test/a.bin")
    assert got == f"sha256:{hashlib.sha256(body).hexdigest()}"


async def test_beat_fills_missing_checksums(client: AsyncClient, admin_token: str):
    """beat가 측정하지 않은 원본의 체크섬을 채운다. 등록은 빠르게 끝내고 값은 나중에 붙인다."""
    pid = await new_project(client, admin_token, "ck")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        v = await s.get(Video, __import__("uuid").UUID(vid))
        assert v.source_sha256 is None          # 등록 때는 측정하지 않는다
        key = v.source_uri.split("/", 3)[3]
        _storage.put_bytes(key, b"bytes", content_type="video/mp4")

    async with SessionLocal() as s:
        # limit을 고정하지 않는다. 미측정 영상 전체 수를 세어 그만큼을 limit으로 준다.
        # 다른 테스트도 같은 DB를 쓰므로 앞선 테스트가 남긴 미측정 영상이 배치를 채우면 방금 만든 영상이 빠진다.
        # 그러면 단독 실행은 통과하고 전체 실행에서 실패한다.
        pending = await s.scalar(
            select(func.count()).select_from(
                select(Video.video_id).where(Video.source_sha256.is_(None)).subquery()
            )
        )
        assert await fill_missing_checksums(s, limit=pending) >= 1

    async with SessionLocal() as s:
        v = await s.get(Video, __import__("uuid").UUID(vid))
        assert v.source_sha256 == f"sha256:{hashlib.sha256(b'bytes').hexdigest()}"


async def _fill_all() -> int:
    """미측정 영상 전체를 한 배치로 처리한다.

    앞선 테스트의 행이 배치를 채워 이 영상이 빠지지 않게 한다.
    """
    async with SessionLocal() as s:
        pending = await s.scalar(
            select(func.count()).select_from(
                select(Video.video_id).where(Video.source_sha256.is_(None)).subquery()
            )
        )
        return await fill_missing_checksums(s, limit=max(1, pending))


async def test_permanent_failure_stops_at_the_cap(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """영구 실패는 상한에서 멈추고 사유를 남긴다.

    원본이 지워졌거나 권한이 없는 영상을 주기마다 다시 읽으면 traceback만 쌓인다.
    사유가 남아야 화면이 체크섬을 구할 수 없다는 사실을 표시할 수 있다.
    """
    import uuid

    from api_gateway.services.media import checksum
    from botocore.exceptions import ClientError

    pid = await new_project(client, admin_token, "ck-cap")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))

    def _gone(**_kw):
        """원본이 없는 경우(NoSuchKey)를 흉내 낸다."""
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "없음"},
                           "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")

    monkeypatch.setattr(checksum, "sha256_of", _gone)
    for _ in range(checksum._MAX_RETRIES + 1):
        await _fill_all()

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra.get("checksum_failures") == checksum._MAX_RETRIES, "상한에서 안 멈췄다"
        assert v.extra.get(checksum.ERROR_KEY), "사유가 없으면 화면이 왜 없는지 말 못 한다"
        left = [r[0] for r in (await s.execute(checksum._pending_query(None))).all()]
    assert vid not in left, "상한에 닿았는데 아직 후보다 — 10분마다 또 읽는다"


async def test_transient_failure_is_not_counted(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """일시 오류는 실패 횟수로 세지 않는다.

    짧은 연결 끊김 세 번으로 원본 체크섬을 포기하지 않게 한다.
    """
    import uuid

    from api_gateway.services.media import checksum
    from botocore.exceptions import EndpointConnectionError

    pid = await new_project(client, admin_token, "ck-blip")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))

    def _blip(**_kw):
        """스토리지 연결 오류를 흉내 낸다."""
        raise EndpointConnectionError(endpoint_url="https://storage.example")

    monkeypatch.setattr(checksum, "sha256_of", _blip)
    for _ in range(checksum._MAX_RETRIES + 1):
        await _fill_all()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert "checksum_failures" not in (v.extra or {})


async def test_beat_skips_already_measured(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """beat가 이미 측정한 원본을 다시 읽지 않고 값을 덮어쓰지 않는지 확인한다. 전체 바이트를 다시 읽으면 비용이 크다.

    해시는 읽은 키를 기록하는 스텁으로 바꾼다. 같은 프로젝트의 미측정 원본은 읽혀야 비교가 성립한다.
    """
    from uuid import UUID

    from api_gateway.services.media import checksum as mod

    pid = await new_project(client, admin_token, "ck2")
    measured = UUID(await upload_video(client, admin_token, pid))
    pending = UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, measured)
        v.source_sha256 = "sha256:already"
        measured_key = v.source_uri.split("/", 3)[3]
        pending_key = (await s.get(Video, pending)).source_uri.split("/", 3)[3]
        await s.commit()

    read: list[str] = []

    def _stub(*, bucket: str, key: str) -> str:
        """읽은 키를 기록하고 고정 해시를 돌려준다."""
        read.append(key)
        return "sha256:stub"

    monkeypatch.setattr(mod, "sha256_of", _stub)
    async with SessionLocal() as s:
        # 미측정 영상 전체가 한 배치에 들어가게 미측정 수를 limit으로 준다.
        n = await s.scalar(select(func.count()).select_from(
            select(Video.video_id).where(Video.source_sha256.is_(None)).subquery()))
        await fill_missing_checksums(s, limit=n)

    assert pending_key in read
    assert measured_key not in read
    async with SessionLocal() as s:
        assert (await s.get(Video, measured)).source_sha256 == "sha256:already"
        assert (await s.get(Video, pending)).source_sha256 == "sha256:stub"


async def test_budget_stops_before_the_next_beat_overlaps(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """시간 예산이 개수 제한보다 먼저 실행을 멈추는지 확인한다.

    파일 크기가 100배씩 차이 나서 개수만으로는 한 실행이 2분일지 40분일지 알 수 없다.
    beat 주기를 넘기면 다음 실행이 겹쳐 그 주기 전체가 버려진다.
    """
    from api_gateway.config import settings

    pid = await new_project(client, admin_token, "ckb")
    for _ in range(3):
        vid = await upload_video(client, admin_token, pid)
        async with SessionLocal() as s:
            v = await s.get(Video, __import__("uuid").UUID(vid))
            _storage.put_bytes(v.source_uri.split("/", 3)[3], b"x", content_type="video/mp4")

    monkeypatch.setattr(settings, "checksum_budget_seconds", -1.0)   # 즉시 소진
    async with SessionLocal() as s:
        assert await fill_missing_checksums(s) == 0


async def test_batch_comes_from_settings(client: AsyncClient, admin_token: str, monkeypatch):
    """배치 크기를 설정에서 읽는지 확인한다. 초기 적재 때 코드를 고치지 않고 키울 수 있어야 한다.

    해시는 스텁으로 바꾼다. 이 테스트는 해시 값 대신 고르는 건수를 확인한다.
    실제 스토리지를 쓰면 배치에 섞인 다른 테스트의 행이 객체가 없어 `NoSuchKey`로
    실패하고, 처리 건수가 0이 된다.
    """
    from api_gateway.config import settings
    from api_gateway.services.media import checksum as mod

    pid = await new_project(client, admin_token, "ckc")
    for _ in range(3):
        await upload_video(client, admin_token, pid)

    calls: list[dict] = []

    def _stub(**kw) -> str:
        """호출 인자를 기록하고 고정 해시를 돌려준다."""
        calls.append(kw)
        return "sha256:stub"

    monkeypatch.setattr(mod, "sha256_of", _stub)
    monkeypatch.setattr(settings, "checksum_batch", 1)
    async with SessionLocal() as s:
        assert await fill_missing_checksums(s) == 1
    assert len(calls) == 1      # 설정값이 배치 크기라서 한 건만 고른다


async def test_one_failure_does_not_kill_the_rest_of_the_batch(
    client: AsyncClient, admin_token: str
):
    """한 건의 실패가 배치의 나머지를 막지 않는다.

    로컬에서 한 건씩 돌리면 이 문제가 보이지 않는다.
    실패 처리로 `db.rollback()`을 부르면 세션에 연결된 나머지 ORM 객체가 모두 expire된다.
    그러면 다음 반복의 속성 접근이 암묵적 IO가 되어 `MissingGreenlet`으로 실패한다.
    결국 잘못된 행 하나 때문에 배치의 나머지가 함께 실패한다.
    """
    pid = await new_project(client, admin_token, "ckfail")
    bad = await upload_video(client, admin_token, pid)
    good = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        # 없는 키를 가리키게 해서 `NoSuchKey`를 낸다. 원본이 지워졌는데 행이 남은 상태와 같다.
        b = await s.get(Video, __import__("uuid").UUID(bad))
        b.source_uri = f"s3://{_storage.BUCKET}/nope/does-not-exist.mp4"
        v = await s.get(Video, __import__("uuid").UUID(good))
        _storage.put_bytes(v.source_uri.split("/", 3)[3], b"ok", content_type="video/mp4")
        await s.commit()

    async with SessionLocal() as s:
        # 다른 테스트도 같은 DB를 쓰므로 두 영상이 모두 배치에 들어가게 미측정 영상 전체 수를 limit으로 준다.
        pending = await s.scalar(
            select(func.count()).select_from(
                select(Video.video_id).where(Video.source_sha256.is_(None)).subquery()
            )
        )
        await fill_missing_checksums(s, limit=pending)

    async with SessionLocal() as s:
        assert (await s.get(Video, __import__("uuid").UUID(bad))).source_sha256 is None
        assert (await s.get(Video, __import__("uuid").UUID(good))).source_sha256 == (
            f"sha256:{hashlib.sha256(b'ok').hexdigest()}"
        )


async def test_credential_and_permission_errors_are_not_counted(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """자격증명이 없거나 권한이 없는 실패는 영구 실패로 세지 않는다.

    연결 설정이 잠시 빠지거나 권한이 바뀐 동안 세 번 실패로 포기하면 체크섬 없이 원본 정리가 풀린다.
    설정을 고치면 같은 원본을 다시 읽을 수 있으므로 다음 주기에 다시 시도한다.
    """
    import uuid

    from api_gateway.services import platform_credentials
    from api_gateway.services.media import checksum
    from botocore.exceptions import ClientError

    pid = await new_project(client, admin_token, "ck-cred")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))

    errors = [
        platform_credentials.CredentialsMissingError("연결 없음"),
        ClientError({"Error": {"Code": "AccessDenied", "Message": "거부"},
                     "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject"),
        ClientError({"Error": {"Code": "InvalidAccessKeyId", "Message": "키 오류"},
                     "ResponseMetadata": {"HTTPStatusCode": 401}}, "GetObject"),
    ]

    def _denied(**_kw):
        """자격증명·권한 오류를 차례로 흉내 낸다."""
        raise errors[0]

    monkeypatch.setattr(checksum, "sha256_of", _denied)
    for err in list(errors):
        errors[0] = err
        await _fill_all()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert "checksum_failures" not in (v.extra or {})


async def test_storage_connection_loaded_before_reading(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """원본을 읽기 전에 영상에 고정한 저장소 연결을 이 프로세스에 읽어 들인다.

    워커 메모리에 연결이 아직 없으면 읽기가 자격증명 오류로 실패한다.
    """
    import uuid

    from api_gateway.services.media import checksum, storage

    pid = await new_project(client, admin_token, "ck-load")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    loaded: list[dict | None] = []
    real = storage.ensure_loaded

    async def _spy(db, extra):
        """읽어 들인 저장소 기록을 남긴다."""
        loaded.append(extra)
        await real(db, extra)

    monkeypatch.setattr(storage, "ensure_loaded", _spy)
    monkeypatch.setattr(checksum, "sha256_of", lambda **_kw: "sha256:stub")
    await _fill_all()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.source_sha256 == "sha256:stub"
        assert v.extra in loaded


async def test_unparsable_uri_counts_as_permanent(
    client: AsyncClient, admin_token: str,
):
    """원본 위치를 해석할 수 없으면 영구 실패로 센다.

    세지 않으면 체크섬이 끝내 정해지지 않아 원본 정리가 그 영상을 영구히 기다린다.
    """
    import uuid

    from api_gateway.services.media import checksum

    pid = await new_project(client, admin_token, "ck-uri")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        (await s.get(Video, vid)).source_uri = "https://example.invalid/source.mp4"
        await s.commit()
    await _fill_all()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.extra.get("checksum_failures") == 1
        assert v.extra.get(checksum.ERROR_KEY)


async def test_rows_that_keep_failing_move_behind_untried_rows(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """세지 않는 실패가 이어지는 영상은 아직 시도하지 않은 영상 뒤로 간다.

    그런 영상이 배치 크기보다 많으면 매 주기 같은 영상만 다시 읽어 다른 원본의 체크섬이 멈춘다.
    """
    import uuid

    from api_gateway.services.media import checksum
    from botocore.exceptions import ClientError

    pid = await new_project(client, admin_token, "ck-rotate")
    failing = uuid.UUID(await upload_video(client, admin_token, pid))
    fresh = uuid.UUID(await upload_video(client, admin_token, pid))

    def _denied(**_kw):
        """권한 오류를 흉내 낸다. 세지 않는 실패다."""
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "거부"},
                           "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject")

    real_query = checksum._pending_query
    monkeypatch.setattr(checksum, "sha256_of", _denied)
    # 이 실행은 먼저 등록한 영상 하나만 시도하게 좁힌다. 같은 DB에 있는 다른 테스트의 영상을 건드리지 않는다.
    monkeypatch.setattr(checksum, "_pending_query",
                        lambda limit: real_query(limit).where(Video.video_id == failing))
    async with SessionLocal() as s:
        await fill_missing_checksums(s, limit=1)
    monkeypatch.undo()

    async with SessionLocal() as s:
        order = [r[0] for r in (await s.execute(checksum._pending_query(None))).all()]
    assert order.index(fresh) < order.index(failing), "실패가 이어지는 영상이 앞자리를 계속 차지한다"
