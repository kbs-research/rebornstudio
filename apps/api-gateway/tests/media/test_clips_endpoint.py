"""클립 조회 엔드포인트 테스트.

GET /projects/{id}/videos/{video_id}/clips와 프로젝트 단위 목록·단건 조회를 확인한다.
"""
from decimal import Decimal
from uuid import UUID

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import Clip
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


@pytest_asyncio.fixture(loop_scope="session")
async def video_with_clips(client: AsyncClient, admin_token: str):
    """프로젝트, 업로드된 영상, 클립 2건을 만든다. 클립은 DB에 직접 넣는다."""
    pid = await new_project(client, admin_token, "clips-ep")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        s.add(Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.250"),
            rights_status="auto_cleared",
            auto_caption={"brief": "첫 장면", "speeches": []},
            speakers=[{"id": 1, "name": "화자1"}],
        ))
        s.add(Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("5.250"), end_seconds=Decimal("10.000"),
            rights_status="auto_cleared",
            auto_caption={"brief": "두번째"},
            speakers=[],
        ))
        await s.commit()
    return pid, vid


async def test_list_clips_returns_two_in_time_order(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """클립 2건이 start_seconds 오름차순으로 반환되고 DTO 필드가 일치한다."""
    pid, vid = video_with_clips
    r = await client.get(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["start_seconds"] == 0.0
    assert body[0]["auto_caption_brief"] == "첫 장면"
    assert body[1]["start_seconds"] == 5.25


async def test_list_clips_member_can_view(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """annotator 멤버도 영상의 클립 목록을 조회할 수 있다.

    라벨러 화면의 화자·배너 표시에 쓴다.
    """
    pid, vid = video_with_clips
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {annot_tok}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_list_clips_403_for_outsider(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """프로젝트 비멤버의 클립 조회를 403으로 거절한다."""
    from tests._helpers import make_user
    pid, vid = video_with_clips
    _, out_tok = await make_user(client, admin_token, "clip-outsider")
    r = await client.get(
        f"/projects/{pid}/videos/{vid}/clips",
        headers={"Authorization": f"Bearer {out_tok}"},
    )
    assert r.status_code == 403


async def test_list_clips_404_for_unknown_video(
    client: AsyncClient, admin_token: str
):
    """없는 영상 ID로 조회하면 404를 돌려준다."""
    pid = await new_project(client, admin_token, "clips-404")
    missing = "00000000-0000-4000-8000-000000000000"
    r = await client.get(
        f"/projects/{pid}/videos/{missing}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


async def test_list_project_clips_includes_video_title(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """프로젝트 단위 클립 목록에 video_title을 싣는다."""
    pid, _ = video_with_clips
    r = await client.get(
        f"/projects/{pid}/clips",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all(c["video_title"] for c in body)  # join으로 채운다


async def test_list_project_clips_member_can_view(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """프로젝트 클립 목록은 멤버 누구나 읽을 수 있다. annotator는 200을 받고 비멤버는 403으로 거절한다.

    콘텐츠 화면의 클립 탭(FE clips_panel, 일반 멤버 라우트)이 GET /projects/{id}/clips를 호출한다.
    그래서 목록은 멤버 전체가 읽기 전용으로 볼 수 있고, 비멤버는 막는다.
    """
    from tests._helpers import make_user
    pid, _ = video_with_clips
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(
        f"/projects/{pid}/clips",
        headers={"Authorization": f"Bearer {annot_tok}"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    _, out_tok = await make_user(client, admin_token, "clip-list-outsider")
    r403 = await client.get(
        f"/projects/{pid}/clips",
        headers={"Authorization": f"Bearer {out_tok}"},
    )
    assert r403.status_code == 403


async def test_get_single_clip(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """클립 단건 조회를 검사한다. 정상 200, 비멤버 403, 없는 클립 404다."""
    from tests._helpers import make_user
    pid, _ = video_with_clips
    h = {"Authorization": f"Bearer {admin_token}"}
    cid = (await client.get(f"/projects/{pid}/clips", headers=h)).json()[0]["clip_id"]

    r = await client.get(f"/projects/{pid}/clips/{cid}", headers=h)
    assert r.status_code == 200 and r.json()["clip_id"] == cid

    _, out = await make_user(client, admin_token, "clip-single-out")
    r403 = await client.get(f"/projects/{pid}/clips/{cid}", headers={"Authorization": f"Bearer {out}"})
    assert r403.status_code == 403

    missing = "00000000-0000-4000-8000-000000000000"
    r404 = await client.get(f"/projects/{pid}/clips/{missing}", headers=h)
    assert r404.status_code == 404


def _cursor(c: dict) -> str:
    """클립 한 건으로 다음 페이지 커서 쿼리를 만든다. 커서 값 네 개가 모두 있어야 한다."""
    from urllib.parse import urlencode
    return urlencode({
        "before": c["created_at"], "before_video_id": c["video_id"],
        "before_start": c["start_seconds"], "before_id": c["clip_id"],
    })


async def test_list_project_clips_pagination(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """프로젝트 클립 목록의 복합 커서 페이지네이션을 검사한다.

    created_at이 같아도 페이지 사이에 겹치거나 빠지는 행이 없다. 커서는 마지막 항목의
    (created_at, video_id, start_seconds, clip_id) 네 값이다.
    """
    pid, _ = video_with_clips
    h = {"Authorization": f"Bearer {admin_token}"}
    page1 = (await client.get(f"/projects/{pid}/clips?limit=1", headers=h)).json()
    assert len(page1) == 1
    c = page1[0]
    page2 = (await client.get(f"/projects/{pid}/clips?limit=1&{_cursor(c)}", headers=h)).json()
    # 두 클립이 같은 commit(같은 created_at)이어도 시작 시각으로 다음 1건을 정확히 가져온다.
    assert len(page2) == 1
    assert page2[0]["clip_id"] != c["clip_id"]
    page3 = (await client.get(f"/projects/{pid}/clips?limit=1&{_cursor(page2[0])}", headers=h)).json()
    assert page3 == []


async def test_list_project_clips_in_time_order_per_video(
    client: AsyncClient, admin_token: str
):
    """같은 시각에 만든 클립을 영상별로 묶고 영상 안에서는 시작 시각 순으로 정렬한다.

    한 트랜잭션에서 만든 클립은 created_at이 같다. (created_at, clip_id) 순서로 정렬하면
    무작위 id 순이 되어 한 영상의 클립이 섞인다.
    """
    pid = await new_project(client, admin_token, "clips-order")
    v1 = await upload_video(client, admin_token, pid, title="order-a")
    v2 = await upload_video(client, admin_token, pid, title="order-b")
    starts = [30.0, 0.0, 90.5, 60.0, 15.0]
    async with SessionLocal() as s:
        for vid in (v1, v2):
            for st in starts:
                s.add(Clip(
                    video_id=UUID(vid), project_id=UUID(pid),
                    start_seconds=Decimal(str(st)), end_seconds=Decimal(str(st + 5)),
                    rights_status="auto_cleared",
                ))
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    seen: list[dict] = []
    url = f"/projects/{pid}/clips?limit=3"
    for _ in range(10):
        page = (await client.get(url, headers=h)).json()
        if not page:
            break
        seen += page
        url = f"/projects/{pid}/clips?limit=3&{_cursor(page[-1])}"
    assert len(seen) == 10
    assert len({c["clip_id"] for c in seen}) == 10
    # 영상별로 붙어 있고, 영상 안에서는 시작 시각 순이다.
    first, second = seen[:5], seen[5:]
    assert len({c["video_id"] for c in first}) == 1
    assert len({c["video_id"] for c in second}) == 1
    for group in (first, second):
        assert [c["start_seconds"] for c in group] == sorted(starts)


async def test_list_project_clips_newest_batch_first(client: AsyncClient, admin_token: str):
    """나중에 만든 클립 묶음이 앞에 오고 커서로 넘긴 다음 페이지에 이전 묶음이 이어지는지 확인한다.

    같은 영상이라도 다시 분석해 만든 클립이 먼저 보여야 운영자가 최근 결과를 찾는다.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    pid = await new_project(client, admin_token, "clips-newest")
    vid = await upload_video(client, admin_token, pid)
    batches: list[list[UUID]] = []
    for starts in ((0.0, 10.0), (20.0, 30.0)):
        async with SessionLocal() as s:
            rows = [Clip(video_id=UUID(vid), project_id=UUID(pid),
                         start_seconds=Decimal(str(st)), end_seconds=Decimal(str(st + 5)),
                         rights_status="auto_cleared") for st in starts]
            s.add_all(rows)
            await s.commit()
            batches.append([c.clip_id for c in rows])
    # 앞 묶음을 한 시간 전에 만든 것으로 옮긴다. 같은 초에 커밋돼도 순서가 정해진다.
    async with SessionLocal() as s:
        await s.execute(update(Clip).where(Clip.clip_id.in_(batches[0]))
                        .values(created_at=datetime.now(UTC) - timedelta(hours=1)))
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    page1 = (await client.get(f"/projects/{pid}/clips?limit=2", headers=h)).json()
    page2 = (await client.get(f"/projects/{pid}/clips?limit=2&{_cursor(page1[-1])}",
                              headers=h)).json()
    assert [UUID(c["clip_id"]) for c in page1] == batches[1]
    assert [UUID(c["clip_id"]) for c in page2] == batches[0]


async def test_list_project_clips_search(
    client: AsyncClient, admin_token: str
):
    """검색(`q`)을 서버에서 적용한다. 그래서 불러온 페이지 밖의 클립도 찾는다."""
    pid = await new_project(client, admin_token, "clips-q")
    news = await upload_video(client, admin_token, pid, title="사건브리핑 2026.07.01")
    other = await upload_video(client, admin_token, pid, title="저녁 뉴스 샘플")
    async with SessionLocal() as s:
        s.add(Clip(
            video_id=UUID(news), project_id=UUID(pid),
            start_seconds=Decimal("0"), end_seconds=Decimal("5"), rights_status="rejected",
        ))
        s.add(Clip(
            video_id=UUID(other), project_id=UUID(pid),
            start_seconds=Decimal("0"), end_seconds=Decimal("5"), rights_status="auto_cleared",
            auto_caption={"summary": "앵커가 스튜디오에서 사건을 전합니다"},
        ))
        await s.commit()
    h = {"Authorization": f"Bearer {admin_token}"}

    by_title = (await client.get(f"/projects/{pid}/clips?q=사건브리핑", headers=h)).json()
    assert [c["video_id"] for c in by_title] == [news]
    by_summary = (await client.get(f"/projects/{pid}/clips?q=스튜디오", headers=h)).json()
    assert [c["video_id"] for c in by_summary] == [other]


async def test_list_project_clips_incomplete_cursor_is_422(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """커서 값 네 개 중 일부만 오면 422로 거절한다. 일부만 비교하면 행을 건너뛰거나 반복한다."""
    pid, _ = video_with_clips
    h = {"Authorization": f"Bearer {admin_token}"}
    c = (await client.get(f"/projects/{pid}/clips?limit=1", headers=h)).json()[0]
    r = await client.get(
        f"/projects/{pid}/clips?limit=1&before={c['created_at']}&before_id={c['clip_id']}",
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "incomplete_cursor"


async def test_count_project_clips_matches_list_filters(
    client: AsyncClient, admin_token: str
):
    """클립 수(`/clips/count`)를 목록과 같은 조건으로 센다. 첫 화면부터 분모를 표시하는 데 쓴다."""
    pid = await new_project(client, admin_token, "clips-count")
    news = await upload_video(client, admin_token, pid, title="사건브리핑 2026.07.01")
    other = await upload_video(client, admin_token, pid, title="저녁 뉴스 샘플")
    async with SessionLocal() as s:
        for i in range(3):
            s.add(Clip(
                video_id=UUID(news), project_id=UUID(pid),
                start_seconds=Decimal(i * 5), end_seconds=Decimal(i * 5 + 5), rights_status="rejected",
            ))
        s.add(Clip(
            video_id=UUID(other), project_id=UUID(pid),
            start_seconds=Decimal("0"), end_seconds=Decimal("5"), rights_status="auto_cleared",
            auto_caption={"summary": "앵커가 스튜디오에서 사건을 전합니다"},
        ))
        await s.commit()
    h = {"Authorization": f"Bearer {admin_token}"}

    for query, expected in {"": 4, "?q=사건브리핑": 3, "?q=스튜디오": 1, "?q=사건": 4}.items():
        r = await client.get(f"/projects/{pid}/clips/count{query}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json() == {"total": expected}, query
        listed = (await client.get(f"/projects/{pid}/clips{query}", headers=h)).json()
        assert len(listed) == expected, query


async def test_count_project_clips_member_only(
    client: AsyncClient, admin_token: str, video_with_clips
):
    """클립 수도 목록과 같이 멤버만 조회할 수 있다. 비멤버는 403으로 거절한다."""
    from tests._helpers import make_user
    pid, _ = video_with_clips
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/clips/count", headers={"Authorization": f"Bearer {annot_tok}"})
    assert r.status_code == 200
    assert r.json() == {"total": 2}
    _, out_tok = await make_user(client, admin_token, "clip-count-outsider")
    r403 = await client.get(f"/projects/{pid}/clips/count", headers={"Authorization": f"Bearer {out_tok}"})
    assert r403.status_code == 403
