"""업로드 완료 확인이 크기로 거절한 원본 객체를 지우는지 확인한다.

멀티파트 업로드에는 크기 조건이 없어 상한을 넘는 파일도 합쳐진다. 완료 확인(`complete_upload`)이
실제 크기로 거절할 때 그 객체를 지우지 않으면 쓰지 않는 객체가 `intake/`에 남아 과금된다.
삭제가 실패해도 거절 응답(422)은 그대로여야 한다.

저장소는 인프로세스 moto다. 상한은 `settings.s3_max_upload_bytes`를 테스트 안에서만 낮춘다.
"""
import boto3
import httpx
from api_gateway.config import settings
from api_gateway.services.media import storage
from botocore.exceptions import ClientError
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project

#: 테스트가 올리는 원본 크기(바이트). 낮춘 상한보다 크다.
_BODY = b"x" * 64


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def _exists(key: str) -> bool:
    """테스트 버킷에 객체가 있으면 참. 앱 경로를 거치지 않고 moto를 직접 조회한다."""
    s3 = boto3.client("s3", endpoint_url=_storage.ENDPOINT, aws_access_key_id="testing",
                      aws_secret_access_key="testing", region_name="us-east-1")
    try:
        s3.head_object(Bucket=_storage.BUCKET, Key=key)
        return True
    except ClientError:
        return False


async def _multipart_uploaded(client: AsyncClient, token: str) -> tuple[str, str]:
    """영상을 등록하고 멀티파트로 파트 하나를 올린다. `(video_id, upload_id)`를 돌려준다."""
    pid = await new_project(client, token, "mpu-rej")
    vid = (await client.post(f"/projects/{pid}/videos", headers=_h(token),
                             json={"title": "mpu-rej", "copyright_policy": "pre_cleared"})
           ).json()["video_id"]
    mpu = (await client.post(f"/videos/{vid}/upload/multipart", headers=_h(token))).json()
    parts = (await client.post(f"/videos/{vid}/upload/parts", headers=_h(token),
                               json={"upload_id": mpu["upload_id"], "part_numbers": [1]})).json()
    async with httpx.AsyncClient() as c:
        assert (await c.put(parts["urls"]["1"], content=_BODY)).status_code == 200
    return vid, mpu["upload_id"]


async def test_oversized_multipart_object_is_deleted(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """상한을 넘은 멀티파트 업로드는 422로 거절하고 합친 객체를 지운다."""
    vid, upload_id = await _multipart_uploaded(client, admin_token)
    monkeypatch.setattr(settings, "s3_max_upload_bytes", len(_BODY) - 1)

    r = await client.post(f"/videos/{vid}/upload/complete", headers=_h(admin_token),
                          json={"upload_id": upload_id})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "video_file_too_large"
    assert not _exists(f"intake/{vid}/source"), "거절한 객체가 버킷에 남았다"
    video = (await client.get(f"/videos/{vid}", headers=_h(admin_token))).json()
    assert video["upload_status"] != "uploaded"


async def test_delete_failure_keeps_the_rejection(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """객체 삭제가 실패해도 응답은 크기 초과 거절(422) 그대로다."""
    vid, upload_id = await _multipart_uploaded(client, admin_token)
    monkeypatch.setattr(settings, "s3_max_upload_bytes", len(_BODY) - 1)
    calls: list[str] = []

    def _fail(*, bucket: str, key: str, store=None) -> None:
        """삭제 요청을 세고 저장소 일시 오류(503)를 던진다."""
        calls.append(key)
        raise ClientError({"Error": {"Code": "ServiceUnavailable"},
                           "ResponseMetadata": {"HTTPStatusCode": 503}}, "DeleteObject")

    monkeypatch.setattr(storage, "delete_object", _fail)

    r = await client.post(f"/videos/{vid}/upload/complete", headers=_h(admin_token),
                          json={"upload_id": upload_id})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "video_file_too_large"
    assert calls == [f"intake/{vid}/source"]


async def test_accepted_upload_keeps_the_object(client: AsyncClient, admin_token: str):
    """상한 안의 업로드는 객체를 그대로 두고 확정한다."""
    vid, upload_id = await _multipart_uploaded(client, admin_token)

    r = await client.post(f"/videos/{vid}/upload/complete", headers=_h(admin_token),
                          json={"upload_id": upload_id})

    assert r.status_code == 200, r.text
    assert _exists(f"intake/{vid}/source")
