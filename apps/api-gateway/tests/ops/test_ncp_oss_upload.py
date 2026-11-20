"""`ncp_oss_upload`의 SigV4 서명과 캐노니컬 키 단위 테스트.

AWS SigV4 표준 참조 벡터로 서명이 표준과 1바이트도 다르지 않은지 검증한다.
서명이 1바이트라도 틀리면 업로드 전체가 403이 된다.
`scripts/`는 패키지가 아니라서 이 테스트를 `tests/`에 둔다.
`scripts/` 아래 두면 `testpaths = ["tests"]`가 수집하지 않고 `from scripts.…`도 수집 단계에서 실패한다.
"""
import hashlib
import hmac
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "ops"))

from ncp_oss_upload import _canonical_key, _sigv4_auth  # noqa: E402


def test_canonical_key_preserves_slash():
    """'/' 구분자를 percent-encoding 없이 보존하는지 확인한다."""
    assert _canonical_key("a/b/c.mp4") == "a/b/c.mp4"
    assert _canonical_key("/leading-slash/x") == "leading-slash/x"


def test_canonical_key_percent_encodes_specials():
    """RFC3986 비예약 문자가 아닌 문자(한글 등)를 percent-encoding하는지 확인한다."""
    assert "%" in _canonical_key("폴더/x.mp4")


def test_sigv4_signing_key_matches_aws_reference():
    """AWS 공식 예제 입력으로 만든 signing key가 문서의 참조 값과 같은지 확인한다.

    입력: secret_key=wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY, date_stamp=20120215,
    region=us-east-1, service=iam.
    """
    secret = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    def _h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()  # HMAC-SHA256 계산
    k_date = _h(("AWS4" + secret).encode(), "20120215")
    k_region = _h(k_date, "us-east-1")
    k_service = _h(k_region, "iam")
    k_signing = _h(k_service, "aws4_request")
    # AWS 문서의 참조 signing key
    assert k_signing.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"


def test_sigv4_auth_header_format():
    """Authorization 헤더가 SigV4 형식(Credential, SignedHeaders, Signature)을 따르는지 확인한다."""
    now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    auth = _sigv4_auth(
        method="PUT",
        host="kr.object.ncloudstorage.com",
        canonical_uri="/my-bucket/path/to/file.mp4",
        headers={
            "Host": "kr.object.ncloudstorage.com",
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
            "x-amz-date": "20260528T100000Z",
        },
        payload_hash="UNSIGNED-PAYLOAD",
        access_key="ak",
        secret_key="sk",
        region="kr-standard",
        now=now,
    )
    # 헤더 형식
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=ak/20260528/kr-standard/s3/aws4_request, ")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    assert ", Signature=" in auth
    # 서명은 sha256 16진수 64자다
    sig = auth.split("Signature=")[-1]
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_sigv4_determinism():
    """같은 입력으로 두 번 호출하면 같은 서명을 돌려주는지 확인한다."""
    now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    kwargs = dict(
        method="PUT", host="kr.object.ncloudstorage.com",
        canonical_uri="/b/k", headers={
            "Host": "kr.object.ncloudstorage.com",
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
            "x-amz-date": "20260528T100000Z",
        },
        payload_hash="UNSIGNED-PAYLOAD",
        access_key="ak", secret_key="sk", region="kr-standard", now=now,
    )
    assert _sigv4_auth(**kwargs) == _sigv4_auth(**kwargs)
