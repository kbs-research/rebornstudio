"""영상·클립 목록 검색어를 글자 그대로 찾는지 확인하는 테스트.

`%`·`_`를 와일드카드로 해석하면 `q=%`가 모든 행을 돌려준다. 그러면 결과 없음을 표시할 수 없다.
검색어에 NUL이 섞이면 드라이버 오류로 500이 나므로 NUL을 지우고 찾는다.
"""
from decimal import Decimal
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


async def _clip(pid: str, vid: str, start: float, summary: str) -> None:
    """요약 문장이 있는 클립 하나를 만든다."""
    async with SessionLocal() as s:
        s.add(Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal(str(start)), end_seconds=Decimal(str(start + 5)),
            rights_status="cleared", auto_caption={"summary": summary},
        ))
        await s.commit()


async def test_video_search_treats_wildcards_as_text(client: AsyncClient, admin_token: str):
    """`%`·`_`는 그 글자가 든 제목만 찾는다."""
    pid = await new_project(client, admin_token, "q-lit-v")
    h = {"Authorization": f"Bearer {admin_token}"}
    plain = await upload_video(client, admin_token, pid, title="9시 뉴스 샘플")
    pct = await upload_video(client, admin_token, pid, title="득표율 50% 돌파")
    under = await upload_video(client, admin_token, pid, title="file_name")

    def ids(r):
        """목록 응답에서 영상 id 집합을 꺼낸다."""
        assert r.status_code == 200, r.text
        return {v["video_id"] for v in r.json()}

    assert ids(await client.get(f"/projects/{pid}/videos", params={"q": "%"}, headers=h)) == {pct}
    assert ids(await client.get(f"/projects/{pid}/videos", params={"q": "_"}, headers=h)) == {under}
    assert plain not in ids(await client.get(f"/projects/{pid}/videos", params={"q": "%"}, headers=h))

    sel = await client.post(f"/projects/{pid}/videos/select", json={"q": "%"}, headers=h)
    assert sel.status_code == 200, sel.text
    assert sel.json()["video_ids"] == [pct]


async def test_video_search_with_nul_is_not_500(client: AsyncClient, admin_token: str):
    """NUL이 섞인 검색어는 NUL을 지우고 찾는다. 드라이버 오류가 500으로 나가지 않는다."""
    pid = await new_project(client, admin_token, "q-nul")
    h = {"Authorization": f"Bearer {admin_token}"}
    vid = await upload_video(client, admin_token, pid, title="it's news")
    r = await client.get(f"/projects/{pid}/videos", params={"q": "\x00'"}, headers=h)
    assert r.status_code == 200, r.text
    assert [v["video_id"] for v in r.json()] == [vid]
    r = await client.get(f"/projects/{pid}/clips", params={"q": "\x00"}, headers=h)
    assert r.status_code == 200, r.text


async def test_clip_search_treats_wildcards_as_text(client: AsyncClient, admin_token: str):
    """클립 목록에도 같은 규칙을 적용한다. 요약에 `%`가 든 클립만 나온다."""
    pid = await new_project(client, admin_token, "q-lit-c")
    h = {"Authorization": f"Bearer {admin_token}"}
    vid = await upload_video(client, admin_token, pid, title="clipvid")
    await _clip(pid, vid, 0, "지지율 40% 기록")
    await _clip(pid, vid, 5, "평범한 장면")
    r = await client.get(f"/projects/{pid}/clips", params={"q": "%"}, headers=h)
    assert r.status_code == 200, r.text
    assert [c["scene_summary"] for c in r.json()] == ["지지율 40% 기록"]
    r = await client.get(f"/projects/{pid}/clips", params={"q": "_"}, headers=h)
    assert r.json() == []


async def test_clip_count_matches_list_filters(client: AsyncClient, admin_token: str):
    """클립 수를 목록과 같은 조건으로 센다. 콘텐츠 클립 탭의 "N개 중" 분모다."""
    pid = await new_project(client, admin_token, "q-count")
    h = {"Authorization": f"Bearer {admin_token}"}
    vid = await upload_video(client, admin_token, pid, title="countvid")
    for i in range(3):
        await _clip(pid, vid, i * 5, f"장면 {i}")
    await _clip(pid, vid, 20, "지지율 40%")
    r = await client.get(f"/projects/{pid}/clips/count", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 4}
    r = await client.get(f"/projects/{pid}/clips/count", params={"q": "%"}, headers=h)
    assert r.json() == {"total": 1}
    listed = (await client.get(f"/projects/{pid}/clips", params={"limit": 2}, headers=h)).json()
    assert len(listed) == 2, "목록은 페이지로 끊기고 수는 전체를 센다"
