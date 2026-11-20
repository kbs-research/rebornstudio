"""영상 티켓(MAIU 디스패치 표식)이 사람 큐에 나오지 않는지 보는 work_items 범위 회귀 테스트.

등록부터 MAIU까지 이어지는 흐름의 영상 work_item(clip_id IS NULL)은 디스패치 대기 표식이다.
라벨링 claim이 이 표식을 가져가면 status가 available을 벗어나 MAIU 대기 풀에서 영구히 빠진다.
사람 작업은 클립 단위라서 claim은 클립 유무와 관계없이 영상 티켓을 건너뛴다.
"""
from decimal import Decimal
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, WorkItem
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


async def test_video_ticket_never_claimable(client: AsyncClient, admin_token: str):
    """영상 티켓이 클립 유무와 관계없이 사람 큐에 나오지 않는지 확인한다. 사람 작업은 클립 단위다."""
    pid = await new_project(client, admin_token, "wqs-a")
    await upload_video(client, admin_token, pid)  # MAIU 디스패치 티켓만 생긴다

    r = await client.post(
        f"/projects/{pid}/work/next", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 404, f"영상 티켓이 라벨링 큐에 노출됨: {r.text}"


async def test_video_ticket_hidden_from_claim_once_clips_exist(
    client: AsyncClient, admin_token: str
):
    """클립이 생기면 그 영상의 영상 티켓은 라벨링 claim 대상에서 빠지고 클립 티켓만 잡히는지 확인한다."""
    pid = await new_project(client, admin_token, "wqs-b")
    vid = await upload_video(client, admin_token, pid)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    # MAIU가 클립을 만든 상태를 재현한다. 영상 티켓은 남아 있지만 이제 파이프라인 잔재다.
    async with SessionLocal() as s:
        clip = Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.000"),
            rights_status="auto_cleared",
        )
        s.add(clip)
        await s.commit()
        clip_id = clip.clip_id

    r = await client.post(f"/projects/{pid}/work/next", headers=hdr)
    assert r.status_code == 404, f"영상 티켓이 라벨링 큐에 노출됨: {r.text}"

    # 클립 티켓을 넣으면 정상적으로 claim된다.
    async with SessionLocal() as s:
        s.add(WorkItem(
            video_id=UUID(vid), project_id=UUID(pid), clip_id=clip_id, status="available",
        ))
        await s.commit()

    r = await client.post(f"/projects/{pid}/work/next", headers=hdr)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        claimed = await s.scalar(
            select(WorkItem).where(WorkItem.work_item_id == UUID(r.json()["work_item_id"]))
        )
    assert claimed.clip_id == clip_id, "클립 티켓이 아니라 영상 티켓이 잡혔다"


async def test_retire_video_ticket_only_after_clips_exist(
    client: AsyncClient, admin_token: str
):
    """영상 티켓 회수가 클립이 생긴 뒤에만 일어나는지 확인한다.

    클립이 생기기 전의 영상 티켓은 디스패치 대기 표식이라 남겨 둔다.
    """
    from api_gateway.services.work import service as work_service

    pid = await new_project(client, admin_token, "wqs-d")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        # 클립이 없으면 아무것도 지우지 않는다.
        assert await work_service.retire_video_ticket(s, UUID(vid)) == 0
        await s.commit()

    async with SessionLocal() as s:
        s.add(Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.000"),
            rights_status="auto_cleared",
        ))
        await s.commit()

    async with SessionLocal() as s:
        assert await work_service.retire_video_ticket(s, UUID(vid)) == 1
        await s.commit()

    async with SessionLocal() as s:
        left = await s.scalar(
            select(WorkItem).where(
                WorkItem.video_id == UUID(vid), WorkItem.clip_id.is_(None)
            )
        )
    assert left is None, "클립 생성 후에도 영상 티켓이 남아 있다"


async def test_pipeline_video_ticket_hidden_from_review_queue(
    client: AsyncClient, admin_token: str
):
    """submitted 상태의 영상 티켓을 검수 대기로 세지 않고 가져오지도 않는지 확인한다."""
    pid = await new_project(client, admin_token, "wqs-c")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        s.add(Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.000"),
            rights_status="auto_cleared",
        ))
        # 영상 티켓을 submitted로 바꾼다.
        item = await s.scalar(
            select(WorkItem).where(
                WorkItem.video_id == UUID(vid), WorkItem.clip_id.is_(None)
            )
        )
        item.status = "submitted"
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.get(f"/projects/{pid}/review/available-count", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0, f"파이프라인 잔재 영상 티켓이 검수 대기에 세어짐: {r.json()}"
    r = await client.post(f"/projects/{pid}/review/next", headers=h)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "no_review_available"
