"""NCP 어댑터의 서버 쪽 복사가 큰 객체를 멀티파트 복사로 나누는지 검사한다.

S3의 단일 CopyObject는 5 GiB까지만 복사한다. 규격 검사는 그보다 큰 원본도 받으므로
큰 객체는 UploadPartCopy로 나눠 복사해야 한다. 5 GiB를 실제로 만들 수 없어서
멀티파트 기준을 작게 바꿔 같은 경로를 탄다.
"""
import uuid

import pytest
from reborn_contracts.storage_backend import ObjectRef


@pytest.fixture
def ncp():
    """인프로세스 moto 버킷에 붙은 NCP 어댑터와 호출된 S3 작업 이름 목록을 준다."""
    import contextlib

    from api_gateway.services.media.storage import _client
    from api_gateway.services.stacks.storage_adapters.ncp import NcpStorage
    from tests import _storage

    c = _client(_storage.store())
    with contextlib.suppress(Exception):
        c.create_bucket(Bucket="test-bucket")
    ops: list[str] = []

    def _record(model, **_kw):
        """호출된 작업 이름을 남긴다."""
        ops.append(model.name)

    c.meta.events.register("before-call.s3", _record)
    yield NcpStorage(c), ops
    c.meta.events.unregister("before-call.s3", _record)


def _ref(key: str) -> ObjectRef:
    """테스트 버킷의 객체 참조를 만든다."""
    return ObjectRef(bucket="test-bucket", key=key)


def test_large_copy_uses_multipart(ncp, monkeypatch):
    """멀티파트 기준을 넘는 객체는 UploadPartCopy로 나눠 복사하고 내용과 타입을 그대로 둔다."""
    from api_gateway.services.stacks.storage_adapters import ncp as mod

    store, ops = ncp
    mib = 1024 * 1024
    monkeypatch.setattr(mod, "COPY_MULTIPART_THRESHOLD", 5 * mib)
    monkeypatch.setattr(mod, "COPY_PART_SIZE", 5 * mib)
    body = bytes(range(256)) * (12 * mib // 256)
    src, dst = _ref(f"copy-{uuid.uuid4()}/src.mp4"), _ref(f"copy-{uuid.uuid4()}/dst.mp4")
    store.put_bytes(src, body, content_type="video/mp4")
    ops.clear()

    store.copy(src, dst)

    assert "UploadPartCopy" in ops, ops
    assert "CopyObject" not in ops, ops
    assert store.get_bytes(dst) == body
    assert store.head(dst).content_type == "video/mp4"
    store.delete(src)
    store.delete(dst)


def test_small_copy_stays_single_request(ncp):
    """기준보다 작은 객체는 CopyObject 한 번으로 복사한다."""
    store, ops = ncp
    src, dst = _ref(f"copy-{uuid.uuid4()}/s.mp4"), _ref(f"copy-{uuid.uuid4()}/d.mp4")
    store.put_bytes(src, b"small", content_type="video/mp4")
    ops.clear()

    store.copy(src, dst)

    assert "CopyObject" in ops and "UploadPartCopy" not in ops, ops
    assert store.get_bytes(dst) == b"small"
    assert store.head(dst).content_type == "video/mp4"
    store.delete(src)
    store.delete(dst)
