"""완료 목록의 캡션 검색 테스트.

검색은 화면에 보이는 최종 캡션(`caption.final_text`)과 같은 글자를 찾아야 한다.
"""
from httpx import AsyncClient
from tests._helpers import new_project, seed_clip_work, upload_video
from tests.exports.test_exports import _approve


async def test_completed_search_finds_text_only_in_sentences(client: AsyncClient, admin_token: str):
    """문장 배열에만 있는 캡션도 검색되고, 삭제 표시한 문장의 글자와 키 이름으로는 찾지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "csearch")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=0.0, end=30.0)
    await _approve(wid, {"caption": {"brief_sentences": [
        {"id": "s0", "text": "기상 캐스터가 주말 날씨를 전한다.", "start": 1.0, "end": 5.0},
        {"id": "s1", "text": "지운 문장에만 있는 낱말 파인애플.", "deleted": True},
    ]}})
    h = {"Authorization": f"Bearer {admin_token}"}

    hit = await client.get(f"/projects/{pid}/completed", params={"q": "주말 날씨"}, headers=h)
    assert hit.status_code == 200
    assert hit.json()["total"] == 1, "문장 배열에만 있는 캡션을 찾지 못했다"

    gone = await client.get(f"/projects/{pid}/completed", params={"q": "파인애플"}, headers=h)
    assert gone.status_code == 200
    assert gone.json()["total"] == 0, "삭제 표시한 문장의 글자로 찾았다"

    key = await client.get(f"/projects/{pid}/completed", params={"q": "brief_sentences"}, headers=h)
    assert key.status_code == 200
    assert key.json()["total"] == 0, "payload의 키 이름으로 찾았다"


async def test_completed_search_still_reads_legacy_shapes(client: AsyncClient, admin_token: str):
    """문장 배열이 없는 옛 payload(`brief` 문자열, `caption` 문자열)도 계속 찾는지 확인한다."""
    pid = await new_project(client, admin_token, "csearch2")
    vid = await upload_video(client, admin_token, pid)
    _c1, w1 = await seed_clip_work(pid, vid, start=0.0, end=30.0)
    _c2, w2 = await seed_clip_work(pid, vid, start=30.0, end=60.0)
    await _approve(w1, {"caption": {"brief": "옛 모양의 캡션 사과나무입니다."}})
    await _approve(w2, {"caption": "문자열 시절의 캡션 배나무입니다."})
    h = {"Authorization": f"Bearer {admin_token}"}

    for word in ("사과나무", "배나무"):
        r = await client.get(f"/projects/{pid}/completed", params={"q": word}, headers=h)
        assert r.status_code == 200
        assert r.json()["total"] == 1, word


async def test_completed_search_reads_wildcards_as_text(client: AsyncClient, admin_token: str):
    """`%`·`_`를 글자 그대로 찾는지 확인한다. 와일드카드로 읽으면 모든 완료 행이 걸린다."""
    pid = await new_project(client, admin_token, "cwild")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid, start=0.0, end=30.0)
    await _approve(wid, {"caption": {"brief_sentences": [
        {"id": "s0", "text": "물가가 3% 올랐다는 발표가 나왔다."},
    ]}})
    h = {"Authorization": f"Bearer {admin_token}"}

    for q in ("_", "%%", "\\"):
        r = await client.get(f"/projects/{pid}/completed", params={"q": q}, headers=h)
        assert r.status_code == 200
        assert r.json()["total"] == 0, f"{q!r}가 와일드카드로 읽혀 행이 걸렸다"

    hit = await client.get(f"/projects/{pid}/completed", params={"q": "3%"}, headers=h)
    assert hit.status_code == 200
    assert hit.json()["total"] == 1, "글자 그대로의 %를 찾지 못했다"
