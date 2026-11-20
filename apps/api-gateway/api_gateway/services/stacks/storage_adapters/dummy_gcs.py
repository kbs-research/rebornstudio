"""GCS 방식의 더미 저장소. 저장소 계약을 두 번째 구현으로 검증한다.

NCP 구현 하나만 있으면 계약이 boto3의 형태에 맞춰 굳는다.
형태가 다른 구현을 붙여야 계약이 견디는지 확인할 수 있으므로 이 더미는 GCS 방식으로 만든다.

- 재개 업로드: NCP(boto3)는 `s3-multipart`로 파트 번호를 쓴다.
  이 더미는 `gcs-resumable`로 세션 URI와 오프셋을 쓴다.
- URI 스킴: NCP는 `s3://`, 이 더미는 `gs://`다.
- 서버 쪽 복사: NCP는 지원한다. 이 더미는 지원하지 않으며 능력으로 선언하지 않는다.

계약은 업로드 방식을 하나로 통일하지 않는다. 두 구현이 모두 multipart면 그 차이를 검증할 수 없다.

능력이 없을 때 도메인의 동작을 확인할 구현이 하나는 필요하다. 그래서 실제 GCS는 서버 쪽 복사를
지원하지만 이 더미는 `SERVER_SIDE_COPY`를 선언하지 않는다.
계약 테스트는 선언하지 않은 능력이 없는 것을 정상으로 본다.
선언한 능력이 동작하지 않는 것은 결함으로 본다.

이 더미는 로컬 디렉터리에 쓰며 실제 GCS, GCS 자격증명, 네트워크를 쓰지 않는다. 계약의 형태만
검증한다. GCS의 S3 호환 XML API로 어디까지 되는지는 실제 GCS에 연결해야 확인할 수 있다.
네트워크 없이 동작하므로 오프라인 개발과 데모에도 쓴다.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from reborn_contracts.storage_backend import (
    ObjectInfo,
    ObjectRef,
    StorageCapability,
    StorageInfo,
    UploadSession,
)

INFO = StorageInfo(
    key="dummy-gcs",
    label="더미 GCS (로컬 디스크)",
    capabilities=frozenset({
        StorageCapability.PRESIGNED_GET,
        StorageCapability.PRESIGNED_PUT,
        StorageCapability.RESUMABLE_UPLOAD,
        StorageCapability.BUCKET_BROWSE,
        # SERVER_SIDE_COPY는 일부러 뺀다. 이유는 모듈 docstring에 있다.
    }),
    upload_kind="gcs-resumable",
    uri_scheme="gs",
)

#: 한 파트 크기. GCS resumable이 요구하는 256KiB 배수로 둔다.
#: NCP와 다른 값으로 두어 계약의 "파트 크기는 백엔드가 정한다"를 검증한다.
PART_SIZE_BYTES = 8 * 1024 * 1024


class DummyGcsStorage:
    """로컬 디렉터리를 버킷처럼 쓰는 저장소.

    Args:
        root: 버킷 디렉터리를 담는 루트 디렉터리. 테스트는 `tmp_path`를 넘긴다.
    """

    info = INFO

    def __init__(self, root: Path) -> None:
        """루트 디렉터리를 정하고 없으면 만든다."""
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # 경로.

    def _path(self, ref: ObjectRef) -> Path:
        """`(버킷, 키)`를 로컬 경로로 바꾼다. 키의 `/`는 디렉터리가 된다.

        키에 `..`가 있으면 루트 밖을 가리킬 수 있어 더미에서도 `ValueError`를 던진다.
        테스트는 임의 키를 넣으므로 검사하지 않으면 저장소 밖의 파일을 지울 수 있다.
        """
        target = (self._root / ref.bucket / ref.key).resolve()
        if not target.is_relative_to(self._root.resolve()):
            raise ValueError(f"버킷 밖을 가리키는 키: {ref.key}")
        return target

    def uri(self, ref: ObjectRef) -> str:
        """도메인이 저장하는 URI 문자열을 만든다. 스킴은 백엔드가 정한다(`gs://`)."""
        return f"{self.info.uri_scheme}://{ref.bucket}/{ref.key}"

    # 읽기.

    def head(self, ref: ObjectRef) -> ObjectInfo | None:
        """객체 정보를 돌려준다. 객체가 없으면 `None`이다(빈 객체 `size_bytes=0`과 구분한다)."""
        p = self._path(ref)
        if not p.is_file():
            return None
        st = p.stat()
        return ObjectInfo(
            size_bytes=st.st_size,
            etag=hashlib.md5(p.read_bytes()).hexdigest(),  # noqa: S324 — 무결성 아님, 식별자
            last_modified=datetime.fromtimestamp(st.st_mtime, UTC),
        )

    def exists(self, ref: ObjectRef) -> bool:
        """객체가 있으면 True를 돌려준다."""
        return self._path(ref).is_file()

    def get_bytes(self, ref: ObjectRef) -> bytes:
        """객체 내용을 읽는다."""
        return self._path(ref).read_bytes()

    def presign_get(self, ref: ObjectRef, *, ttl_seconds: int = 3600) -> str:
        """서명된 읽기 URL을 만든다. 더미는 `file://` URL을 돌려준다.

        계약은 ffmpeg·httpx가 그대로 받을 수 있는 URL을 요구한다. DB에 연결하지 않는 워커는
        이 문자열 하나로 작업하므로, 전용 형식을 쓰면 워커에 백엔드별 처리가 필요해진다.
        """
        return self._path(ref).as_uri() + f"?ttl={ttl_seconds}"

    # 쓰기.

    def put_bytes(self, ref: ObjectRef, body: bytes,
                  *, content_type: str = "application/octet-stream") -> None:
        """객체를 쓴다. 더미는 `content_type`을 받지만 저장하지 않는다."""
        # 더미에는 타입을 저장할 곳이 없어 인자를 받기만 하고 버린다.
        # 계약이 요구하는 것은 시그니처다. 저장하지 않는 값을 저장한 것처럼 꾸미지 않는다.
        del content_type
        p = self._path(ref)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)

    def presign_put(self, ref: ObjectRef, *, ttl_seconds: int = 3600,
                    content_type: str | None = None) -> str:
        """서명된 쓰기 URL을 만든다. 더미는 `file://` URL에 업로드 표시를 붙여 돌려준다."""
        ct = f"&type={content_type}" if content_type else ""
        return self._path(ref).as_uri() + f"?upload&ttl={ttl_seconds}{ct}"

    def delete(self, ref: ObjectRef) -> None:
        """객체를 지운다. 재시도가 안전하도록 삭제는 멱등이며 객체가 없어도 오류를 내지 않는다."""
        self._path(ref).unlink(missing_ok=True)

    # 재개 가능한 업로드: NCP와 방식이 다르다.

    def begin_upload(self, ref: ObjectRef) -> UploadSession:
        """업로드 세션을 연다. 세션의 `kind`에 `gcs-resumable`을 명시한다.

        `session_url`을 채우고 `upload_id`는 비운다. 화면은 `kind`로 처리 방식을 나눈다.
        두 값을 같은 필드에 담으면 화면이 값의 형태로 방식을 추측해야 하므로 필드를 나눈다.
        """
        p = self._path(ref)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")   # 오프셋 0에서 시작한다
        return UploadSession(
            kind="gcs-resumable",
            ref=ref,
            session_url=p.as_uri() + "?resumable",
            part_size_bytes=PART_SIZE_BYTES,
        )

    def append_chunk(self, session: UploadSession, offset: int, chunk: bytes) -> int:
        """지정한 바이트 오프셋에 청크를 이어 쓴다. 위치는 파트 번호 대신 바이트 오프셋으로 받는다.

        Returns:
            int: 다음에 이어 쓸 오프셋. GCS가 `Range` 헤더로 돌려주는 값에 해당한다.

        Raises:
            ValueError: 오프셋이 현재 파일 크기와 다른 경우. 어긋난 채 이어 붙이면 파일이 깨진다.
        """
        p = self._path(session.ref)
        size = p.stat().st_size
        if offset != size:
            raise ValueError(f"오프셋 어긋남: 받은 {offset}, 지금 {size}")
        with p.open("ab") as f:
            f.write(chunk)
        return size + len(chunk)

    def finish_upload(self, session: UploadSession) -> ObjectInfo:
        """업로드 세션을 닫고 객체 정보를 돌려준다.

        GCS는 마지막 청크를 받으면 업로드가 끝나므로 따로 확정하지 않는다.
        S3의 `complete_multipart`는 파트 목록을 다시 보내야 한다.
        이 메서드로 계약이 두 방식의 차이를 견디는지 확인한다.
        """
        info = self.head(session.ref)
        if info is None:
            raise ValueError("세션이 가리키는 객체가 없다")
        return info

    # 운영 화면.

    def list_buckets(self) -> list[str]:
        """루트 아래 디렉터리를 버킷 목록으로 돌려준다."""
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())

    def list_objects(self, bucket: str, prefix: str = "") -> list[ObjectRef]:
        """접두어로 객체를 찾는다. 결과는 키 순으로 정렬한다.

        정렬하지 않으면 실행마다 순서가 달라져 화면의 목록 순서가 바뀐다.
        """
        base = self._root / bucket
        if not base.is_dir():
            return []
        out = [
            ObjectRef(bucket=bucket, key=str(p.relative_to(base)))
            for p in base.rglob("*") if p.is_file()
        ]
        return sorted((r for r in out if r.key.startswith(prefix)), key=lambda r: r.key)

    def drop_bucket(self, bucket: str) -> None:
        """버킷 디렉터리를 지운다. 테스트 정리에 쓰며 운영 어댑터에는 없는 메서드다."""
        shutil.rmtree(self._root / bucket, ignore_errors=True)
