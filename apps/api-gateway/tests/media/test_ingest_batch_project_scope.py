"""영상 등록이 다른 프로젝트의 등록 묶음 id를 받지 않는지 확인한다.

묶음 id는 클라이언트가 만들어 보낸다. 서버는 행이 없으면 만들고 있으면 그대로 쓴다.
프로젝트를 대조하지 않으면 B 프로젝트의 영상이 A 프로젝트의 묶음에 붙는다.
그러면 A의 묶음 상세와 메타 양식이 B의 영상을 싣고, A의 관리자가 B의 영상 메타를 읽게 된다.
"""
import uuid
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services.media import meta_import
from httpx import AsyncClient
from sqlalchemy import func, select
from tests import _storage
from tests._helpers import new_project, upload_video


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _videos_in(batch: str) -> int:
    """묶음에 붙은 영상 수를 센다."""
    async with SessionLocal() as s:
        return await s.scalar(
            select(func.count()).select_from(Video).where(Video.upload_batch_id == UUID(batch))
        )


async def test_upload_and_storage_register_reject_other_project_batch(
    client: AsyncClient, admin_token: str,
):
    """다른 프로젝트의 묶음 id로 영상을 등록하면 404 batch_not_found이고 그 묶음에 영상이 붙지 않는다."""
    pa = await new_project(client, admin_token, "bscope-a")
    pb = await new_project(client, admin_token, "bscope-b")
    batch = str(uuid.uuid4())
    await upload_video(client, admin_token, pa, batch=batch)
    assert await _videos_in(batch) == 1

    r = await client.post(f"/projects/{pb}/videos", headers=_h(admin_token), json={
        "title": "샘플 방송", "copyright_policy": "pre_cleared", "upload_batch_id": batch,
    })
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found", r.text

    _storage.put_bytes("raw/bscope.mp4", b"x", content_type="video/mp4")
    r = await client.post(f"/projects/{pb}/storage/register", headers=_h(admin_token), json={
        "keys": ["raw/bscope.mp4"], "copyright_policy": "pre_cleared", "upload_batch_id": batch,
    })
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found", r.text
    assert await _videos_in(batch) == 1


async def test_meta_rows_only_include_the_batch_project(client: AsyncClient, admin_token: str):
    """묶음의 메타 양식·미리보기 대상이 묶음 프로젝트의 영상만 싣는지 확인한다.

    다른 프로젝트 영상이 이미 묶음에 붙어 있어도 그 영상의 메타는 양식에 나가지 않는다.
    """
    pa = await new_project(client, admin_token, "bmeta-a")
    pb = await new_project(client, admin_token, "bmeta-b")
    batch = str(uuid.uuid4())
    own = await upload_video(client, admin_token, pa, batch=batch, title="샘플 방송 A")
    stray = await upload_video(client, admin_token, pb, title="샘플 방송 B")
    async with SessionLocal() as s:
        (await s.get(Video, UUID(stray))).upload_batch_id = UUID(batch)
        await s.commit()
        rows = await meta_import._batch_videos(s, UUID(batch), UUID(pa))
    assert [str(v.video_id) for v in rows] == [own]
