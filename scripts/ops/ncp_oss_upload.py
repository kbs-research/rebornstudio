"""NCP Object Storage에 파일 하나를 PUT으로 올린다. raw HTTP와 AWS SigV4를 쓴다.

boto3를 쓰지 않는 이유: NCP 환경에 따라 boto3 일부 버전이 호환되지 않는 경우가 있다.
그래서 NCP PutObject 사양대로 HTTPS PUT을 직접 호출한다.

요청 형식 (path style):
    PUT https://{endpoint}/{bucket-name}/{object-name}
    Authorization: AWS4-HMAC-SHA256 Credential=.../aws4_request, SignedHeaders=..., Signature=...
    x-amz-date: 20YYMMDDThhmmssZ
    x-amz-content-sha256: UNSIGNED-PAYLOAD
    Content-Type: ...
    Content-Length: ...
    body: <raw bytes>

자격증명은 앱 DB의 자격증명 연결(NCP 저장소 연결)에서 읽는다. 환경 변수로 받지 않는다.
주소·리전·버킷을 주지 않으면 그 연결의 값을 쓴다.

    uv run --package api-gateway python scripts/ops/ncp_oss_upload.py \
        --connection ncp-storage --local ./a.mp4 --key raw/a.mp4 [--bucket 다른-버킷]

DB 접속은 앱과 같은 `DATABASE_URL`, 복호는 앱과 같은 `CREDENTIALS_KEY`를 쓴다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import mimetypes
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

_SERVICE = "s3"
_UNSIGNED = "UNSIGNED-PAYLOAD"


def _sigv4_auth(
    *,
    method: str,
    host: str,
    canonical_uri: str,
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
) -> str:
    """AWS Signature V4로 `Authorization` 헤더 값을 만든다."""
    # canonical headers: 키는 소문자로, 값은 앞뒤 공백을 지우고, 키 사전순으로 정렬한다.
    canon_items = sorted((k.lower(), v.strip()) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in canon_items)
    signed_headers = ";".join(k for k, _ in canon_items)

    canonical_request = "\n".join([
        method, canonical_uri, "",  # query string은 없으므로 빈 문자열이다
        canonical_headers, signed_headers, payload_hash,
    ])
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key: bytes, msg: str) -> bytes:
        """HMAC-SHA256 바이트 다이제스트를 반환한다."""
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, _SERVICE)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _canonical_key(key: str) -> str:
    """SigV4 canonical URI용으로 키를 인코딩한다.

    '/'는 그대로 두고, RFC3986 unreserved 문자가 아닌 것은 percent-encoding한다.
    """
    return quote(key.lstrip("/"), safe="/")


async def put_object(
    *,
    local_path: Path,
    bucket: str,
    key: str,
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
    content_type: str | None = None,
    timeout: float = 120.0,
) -> dict:
    """파일 하나를 단일 PUT으로 올리고 상태 코드·ETag·크기를 dict로 돌려준다.

    응답 상태 코드가 400 이상이면 `RuntimeError`를 낸다.
    """
    body = local_path.read_bytes()
    ct = content_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"

    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    canonical_uri = f"/{bucket}/{_canonical_key(key)}"
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    # 서명 대상 헤더다. host, x-amz-content-sha256, x-amz-date는 필수 SignedHeaders다.
    sign_headers = {
        "Host": host,
        "x-amz-content-sha256": _UNSIGNED,
        "x-amz-date": amz_date,
    }
    auth = _sigv4_auth(
        method="PUT", host=host, canonical_uri=canonical_uri,
        headers=sign_headers, payload_hash=_UNSIGNED,
        access_key=access_key, secret_key=secret_key, region=region, now=now,
    )
    headers = {
        **sign_headers,
        "Authorization": auth,
        "Content-Type": ct,
        "Content-Length": str(len(body)),
    }
    url = f"{endpoint.rstrip('/')}{canonical_uri}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.put(url, headers=headers, content=body)
    if r.status_code >= 400:
        raise RuntimeError(f"PUT {url} → {r.status_code} {r.text[:400]}")
    return {"status": r.status_code, "etag": r.headers.get("ETag"), "size": len(body)}


async def _connection(name: str):
    """이름으로 고른 NCP 저장소 연결의 값을 앱 DB에서 읽는다. 없거나 여럿이면 `None`이다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Connection
    from api_gateway.services import platform_credentials as pc
    from sqlalchemy import select

    async with SessionLocal() as db:
        conns = list((await db.scalars(select(Connection).where(
            Connection.name == name, Connection.provider == pc.STORAGE_PROVIDER))).all())
        if len(conns) != 1:
            print(f"! NCP 저장소 연결 '{name}'이 {len(conns)}개입니다", file=sys.stderr)
            return None
        await pc.refresh(db)
    return pc.storage_connection(str(conns[0].id))


async def _amain() -> int:
    """명령행 인자를 읽어 `put_object`를 호출하고 결과를 출력한다.

    종료 코드: 0은 성공, 1은 로컬 파일 없음, 2는 연결을 읽지 못함이다.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection", required=True, help="자격증명 연결의 NCP 저장소 연결 이름")
    ap.add_argument("--local", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--bucket", default=None, help="비우면 연결 설정의 버킷")
    ap.add_argument("--endpoint", default=None, help="비우면 연결 설정의 주소")
    ap.add_argument("--region", default=None, help="비우면 연결 설정의 리전")
    args = ap.parse_args()

    local = Path(args.local)
    if not local.is_file():
        print(f"! local file not found: {local}", file=sys.stderr)
        return 1
    creds = await _connection(args.connection)
    if creds is None or not creds.has_keys:
        print(f"! 연결 '{args.connection}'의 키를 읽지 못했습니다", file=sys.stderr)
        return 2
    bucket = args.bucket or creds.bucket
    if not bucket:
        print("! 버킷이 없습니다. --bucket을 주거나 연결 설정에 버킷을 적습니다", file=sys.stderr)
        return 2

    size = local.stat().st_size
    print(f"→ PUT {local} ({size:,} bytes) → s3://{bucket}/{args.key}")
    out = await put_object(
        local_path=local, bucket=bucket, key=args.key,
        endpoint=args.endpoint or creds.endpoint, region=args.region or creds.region,
        access_key=creds.access_key, secret_key=creds.secret_key,
    )
    print(f"✓ {out['status']} etag={out['etag']}")
    print(f"s3://{bucket}/{args.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
