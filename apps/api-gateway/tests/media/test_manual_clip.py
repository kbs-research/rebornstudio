"""사람이 직접 넣는 장면 구간 테스트.

MI 스택에서는 장면을 다듬는 기능이지만, 장면 분할 기능이 없는 스택에서는 장면을 만드는 유일한 방법이다.
여섯 가지를 확인한다.
장면이 만들어진다. 같은 구간을 두 번 만들지 않는다. 기존 클립과 겹치지 않는다. 잘못된 구간을 거절한다. 출처가 남는다.
만든 장면이 라벨링 대기열에 들어가고, 영상 단위 분석 요청 티켓은 지워진다.
"""
import asyncio
import uuid
from decimal import Decimal

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video, WorkItem
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import new_project, seed_clip_work, set_project_options, upload_video


async def _video(client: AsyncClient, token: str, *, duration: float | None = 120.0):
    """테스트용 프로젝트와 영상을 만들고 영상 길이를 duration으로 둔다."""
    pid = await new_project(client, token, "manual")
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.duration_seconds = duration
        await s.commit()
    return pid, vid


async def test_a_person_can_make_a_scene(client: AsyncClient, admin_token: str):
    """장면이 없는 영상에 사람이 장면을 만든다.

    이 기능이 없으면 장면 분할 기능이 없는 스택은 아무 작업도 할 수 없다.
    """
    pid, vid = await _video(client, admin_token)
    r = await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 10.0, "end_seconds": 42.5},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["start_seconds"], body["end_seconds"]) == (10.0, 42.5)


async def test_the_source_is_recorded(client: AsyncClient, admin_token: str):
    """사람이 넣은 클립에는 출처를 남긴다. 벤더가 자른 클립과 구분해야 한다."""
    pid, vid = await _video(client, admin_token)
    await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 0.0, "end_seconds": 30.0},
    )
    async with SessionLocal() as s:
        c = (await s.scalars(select(Clip).where(Clip.video_id == vid))).one()
    assert c.extra["source"] == "manual"
    # 캡션은 라벨링 단계에서 채운다. 여기서 넣으면 만든 것과 작업한 것이 섞인다.
    assert not c.auto_caption and not c.speakers


async def test_the_same_range_twice_is_a_conflict(client: AsyncClient, admin_token: str):
    """같은 구간을 다시 넣으면 409로 거절한다.

    중복이 생기면 같은 시간대에 클립이 둘이 되고 어느 쪽이 맞는지 알 수 없다.
    """
    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    body = {"start_seconds": 5.0, "end_seconds": 20.0}
    assert (await client.post(f"/projects/{pid}/videos/{vid}/clips",
                              headers=h, json=body)).status_code == 201
    r = await client.post(f"/projects/{pid}/videos/{vid}/clips", headers=h, json=body)
    assert r.status_code == 409
    assert r.json()["detail"] == "clip_overlaps"


async def test_zero_length_and_reversed_ranges_are_rejected(client: AsyncClient, admin_token: str):
    """길이가 0이거나 시작이 끝보다 늦은 구간을 계약 검증이 422로 거절한다."""
    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    for body in ({"start_seconds": 10.0, "end_seconds": 10.0},
                 {"start_seconds": 30.0, "end_seconds": 12.0}):
        r = await client.post(f"/projects/{pid}/videos/{vid}/clips", headers=h, json=body)
        assert r.status_code == 422, f"{body} 가 통과했다"


async def test_too_short_range_is_rejected_with_the_default_minimum(
    client: AsyncClient, admin_token: str,
):
    """하한을 정하지 않은 프로젝트는 기본 최소 길이보다 짧은 구간을 422로 거절하고 같은 길이는 허용한다.

    0.234초 같은 조각은 라벨러가 볼 수 없는 클립이 된다. 클립 삭제 경로가 없어 만든 뒤에는 되돌릴 수 없다.
    """
    from reborn_contracts.clip import CLIP_MIN_SECONDS

    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.get(f"/projects/{pid}", headers=h)).json()["clip_min_seconds"] \
        == CLIP_MIN_SECONDS
    url = f"/projects/{pid}/videos/{vid}/clips"
    for body in ({"start_seconds": 0, "end_seconds": 0.234},
                 {"start_seconds": 0, "end_seconds": CLIP_MIN_SECONDS - 0.1}):
        r = await client.post(url, headers=h, json=body)
        assert r.status_code == 422 and r.json()["detail"] == "clip_too_short", body
    ok = await client.post(url, headers=h, json={"start_seconds": 0, "end_seconds": CLIP_MIN_SECONDS})
    assert ok.status_code == 201, ok.text


async def test_the_project_minimum_applies_to_manual_ranges(client: AsyncClient, admin_token: str):
    """클립 길이 하한을 정한 프로젝트는 그 하한으로 검사한다. 화면이 쓸 하한 값도 프로젝트 응답에 싣는다.

    분석이 자르는 클립과 사람이 넣는 클립이 같은 하한을 따라야 한 프로젝트의 클립 길이가 고르다.
    클립 길이는 API로 바꿀 수 없는 고정 기본값이라 하한을 DB에 직접 쓴다.
    """
    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    await set_project_options(
        pid, settings={"scene_options": {"min_seconds": 20, "max_seconds": 90}},
    )
    r = await client.get(f"/projects/{pid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clip_min_seconds"] == 20
    url = f"/projects/{pid}/videos/{vid}/clips"
    r = await client.post(url, headers=h, json={"start_seconds": 0, "end_seconds": 15})
    assert r.status_code == 422 and r.json()["detail"] == "clip_too_short"
    ok = await client.post(url, headers=h, json={"start_seconds": 0, "end_seconds": 20})
    assert ok.status_code == 201, ok.text


async def test_beyond_the_video_is_rejected(client: AsyncClient, admin_token: str):
    """영상 길이를 넘는 구간을 400으로 거절한다. 만들면 재생할 수 없는 클립이 된다."""
    pid, vid = await _video(client, admin_token, duration=60.0)
    r = await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 10.0, "end_seconds": 900.0},
    )
    assert r.status_code == 400 and r.json()["detail"] == "out_of_range"


async def test_end_within_tolerance_is_fitted_to_the_video(
    client: AsyncClient, admin_token: str,
):
    """허용 오차(0.5초) 안에서 영상 길이를 넘은 끝은 영상 끝으로 줄여 저장한다."""
    pid, vid = await _video(client, admin_token, duration=60.0)
    r = await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 30.0, "end_seconds": 60.4},
    )
    assert r.status_code == 201, r.text
    assert r.json()["end_seconds"] == 60.0


async def test_unknown_duration_does_not_block(client: AsyncClient, admin_token: str):
    """영상 길이가 없으면 길이 검사를 하지 않는다. 길이 정보가 없는 경우와 초과한 경우를 구분한다.

    probe 전 영상에 장면을 넣지 못하게 하면 장면 분할 기능이 없는 스택은
    probe가 끝날 때까지 아무것도 할 수 없다.
    """
    pid, vid = await _video(client, admin_token, duration=None)
    r = await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 0.0, "end_seconds": 999.0},
    )
    assert r.status_code == 201, r.text


async def test_manual_clip_overlapping_existing_is_refused(client: AsyncClient, admin_token: str):
    """직접 넣는 구간이 기존 클립과 겹치면 409로 거절한다. 끝과 시작이 맞닿는 구간은 허용한다."""
    pid, vid = await _video(client, admin_token)
    await seed_clip_work(pid, str(vid), start=20.867, end=42.4, rights_status="cleared")
    url = f"/projects/{pid}/videos/{vid}/clips"
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(url, headers=h, json={"start_seconds": 10, "end_seconds": 30})
    assert r.status_code == 409 and r.json()["detail"] == "clip_overlaps"
    r = await client.post(url, headers=h, json={"start_seconds": 25, "end_seconds": 40})
    assert r.status_code == 409
    r = await client.post(url, headers=h, json={"start_seconds": 10, "end_seconds": 20.867})
    assert r.status_code == 201, r.text


async def test_concurrent_manual_clips_do_not_overlap(client: AsyncClient, admin_token: str):
    """겹치는 두 구간을 동시에 넣어도 하나만 들어간다. 검사와 삽입 사이에 다른 요청이 끼어들지 못한다."""
    pid, vid = await _video(client, admin_token)
    url = f"/projects/{pid}/videos/{vid}/clips"
    h = {"Authorization": f"Bearer {admin_token}"}
    a, b = await asyncio.gather(
        client.post(url, headers=h, json={"start_seconds": 0, "end_seconds": 30}),
        client.post(url, headers=h, json={"start_seconds": 10, "end_seconds": 40}),
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count()).select_from(Clip).where(Clip.video_id == vid))
    assert n == 1


async def test_the_source_travels_so_the_screen_need_not_guess(
    client: AsyncClient, admin_token: str,
):
    """구간을 만든 출처를 응답에 싣는다.

    분석 스택은 프리셋마다 다르다. 화면이 구간을 만든 주체를 추측하면 다른 프리셋에서 틀린 값이 된다.
    그래서 구간을 만든 쪽이 `extra.source`에 남기고 DTO로 내보낸다.
    출처를 남기지 않은 이전 클립은 `None`이고, 출처 정보가 없다는 뜻이다.
    """
    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    made = await client.post(
        f"/projects/{pid}/videos/{vid}/clips", headers=h,
        json={"start_seconds": 0.0, "end_seconds": 10.0},
    )
    assert made.status_code == 201 and made.json()["source"] == "manual"

    # 샷 검출 스택(MI 아님)이 만든 구간도 같은 필드에 출처를 남긴다.
    async with SessionLocal() as s:
        s.add(Clip(video_id=vid, project_id=uuid.UUID(pid),
                   start_seconds=Decimal("20"), end_seconds=Decimal("30"),
                   rights_status="auto_cleared", extra={"source": "scenedetect"}))
        # 출처를 남기지 않은 이전 클립이다. 출처 값을 지어내지 않는다.
        s.add(Clip(video_id=vid, project_id=uuid.UUID(pid),
                   start_seconds=Decimal("40"), end_seconds=Decimal("50"),
                   rights_status="auto_cleared"))
        await s.commit()
    rows = (await client.get(f"/projects/{pid}/videos/{vid}/clips", headers=h)).json()
    assert [r["source"] for r in rows] == ["manual", "scenedetect", None]


async def test_a_manual_clip_enters_the_labeling_queue(client: AsyncClient, admin_token: str):
    """사람이 넣은 장면에 라벨링 작업이 하나 열리고, 다시 열어도 하나로 남는다.

    작업이 없는 클립은 라벨링 대기열에 나타나지 않는다.
    장면 분할 기능이 없는 스택에서는 수동 장면이 유일한 작업 원천이라, 작업이 없으면 라벨링을 시작할 수 없다.
    """
    from api_gateway.services.work import service as work_service

    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}
    url = f"/projects/{pid}/videos/{vid}/clips"
    made = await client.post(url, headers=h, json={"start_seconds": 0.0, "end_seconds": 30.0})
    assert made.status_code == 201, made.text
    clip_id = uuid.UUID(made.json()["clip_id"])
    # 같은 구간을 다시 넣어도 작업이 늘지 않는다.
    assert (await client.post(url, headers=h,
                              json={"start_seconds": 0.0, "end_seconds": 30.0})).status_code == 409
    async with SessionLocal() as s:
        items = (await s.scalars(select(WorkItem).where(WorkItem.clip_id == clip_id))).all()
        assert [(i.status, i.video_id) for i in items] == [("available", vid)]
        # 다른 생성 경로가 같은 클립에 작업을 다시 열어도 하나로 남는다.
        await work_service.ensure_for_clip(s, await s.get(Clip, clip_id))
        await s.commit()
        n = await s.scalar(
            select(func.count()).select_from(WorkItem).where(WorkItem.clip_id == clip_id)
        )
    assert n == 1


async def test_a_manual_clip_retires_the_analysis_ticket(client: AsyncClient, admin_token: str):
    """클립이 없는 영상에 구간을 넣으면 영상 단위 분석 요청 티켓을 같은 요청에서 지운다.

    티켓이 남으면 영상 목록의 MI 단계가 분석 대기로 남아 준비 단계(작업 준비 완료)와 어긋난다.
    프로젝트 전체 분석 요청도 이 영상을 MI에 보내 과금하고 사람이 넣은 구간과 겹치는 클립을 만든다.
    """
    pid, vid = await _video(client, admin_token)
    h = {"Authorization": f"Bearer {admin_token}"}

    async def tickets() -> int:
        """영상의 영상 단위 작업 수를 센다."""
        async with SessionLocal() as s:
            return int(await s.scalar(
                select(func.count()).select_from(WorkItem)
                .where(WorkItem.video_id == vid, WorkItem.clip_id.is_(None))
            ) or 0)

    assert await tickets() == 1, "업로드가 분석 요청 티켓을 만들지 않았다"
    made = await client.post(
        f"/projects/{pid}/videos/{vid}/clips", headers=h,
        json={"start_seconds": 0.0, "end_seconds": 30.0},
    )
    assert made.status_code == 201, made.text
    assert await tickets() == 0
    # 두 번째 구간도 같은 경로를 지난다. 지울 티켓이 없어도 실패하지 않는다.
    again = await client.post(
        f"/projects/{pid}/videos/{vid}/clips", headers=h,
        json={"start_seconds": 30.0, "end_seconds": 60.0},
    )
    assert again.status_code == 201, again.text

    rows = (await client.get(f"/projects/{pid}/videos", headers=h)).json()
    row = next(r for r in rows if r["video_id"] == str(vid))
    assert (row["pipeline_stage"], row["prep_stage"]) == ("analyzed", "ready_for_work")


async def test_the_create_response_carries_the_opened_work(client: AsyncClient, admin_token: str):
    """만든 구간의 응답에 방금 연 라벨링 작업의 상태와 id를 싣는다.

    응답이 작업을 비워 두면 작업이 없는 클립(비식별화 신고·제외)과 같은 모양이 된다.
    """
    pid, vid = await _video(client, admin_token)
    made = await client.post(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"start_seconds": 0.0, "end_seconds": 30.0},
    )
    assert made.status_code == 201, made.text
    body = made.json()
    async with SessionLocal() as s:
        item = (await s.scalars(
            select(WorkItem).where(WorkItem.clip_id == uuid.UUID(body["clip_id"]))
        )).one()
    assert (body["work_status"], body["work_item_id"]) == ("available", str(item.work_item_id))
