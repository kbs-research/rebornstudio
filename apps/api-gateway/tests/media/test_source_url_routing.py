"""source-url presign(연결별 클라이언트)과 source_uri 파싱 테스트."""
import pytest
from api_gateway.services.media import storage
from api_gateway.services.media.video import _parse_s3_uri
from fastapi import HTTPException
from tests import _storage


def test_parse_s3_uri_splits_bucket_key():
    """s3://bucket/key를 (bucket, key)로 나눈다."""
    assert _parse_s3_uri("s3://example-analysis-bucket/clip/D/x.mp4") == (
        "example-analysis-bucket", "clip/D/x.mp4",
    )
    assert _parse_s3_uri("ncp://b/k") == ("b", "k")


def test_parse_s3_uri_rejects_bad():
    """scheme이나 key가 없으면 409로 거절한다."""
    for bad in ("http://x/y", "s3://onlybucket", "garbage"):
        with pytest.raises(HTTPException) as e:
            _parse_s3_uri(bad)
        assert e.value.status_code == 409


def test_presign_signs_only_buckets_a_connection_owns(monkeypatch):
    """presign은 그 버킷을 가진 연결의 클라이언트로 서명한다. 어느 연결의 것도 아닌 버킷은 서명하지 않는다.

    요청한 bucket/key가 그대로 서명에 실린다. 기본 저장소 연결이 없으므로 어느 연결에도 없는 버킷은
    서명하지 않고 `CredentialsMissingError`를 낸다(`platform_credentials.storage_for_bucket`).
    """
    from api_gateway.services import platform_credentials

    calls = []

    class FakeClient:
        """presign 호출을 기록하는 가짜 S3 클라이언트."""
        def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803  (boto3 kwarg)
            """호출된 bucket/key를 기록하고 가짜 URL을 돌려준다."""
            calls.append((Params["Bucket"], Params["Key"]))
            return f"https://one/{Params['Bucket']}/{Params['Key']}"

    monkeypatch.setattr(storage, "_client", lambda store=None, **_: FakeClient())

    storage.presign_get_for(bucket=_storage.BUCKET, key="k")
    storage.presign_get("thumb/t.jpg", bucket=_storage.BUCKET, filename="t.jpg")
    with pytest.raises(platform_credentials.CredentialsMissingError):
        storage.presign_get_for(bucket="example-analysis-bucket", key="clip/x.mp4")

    assert calls == [(_storage.BUCKET, "k"), (_storage.BUCKET, "thumb/t.jpg")]
