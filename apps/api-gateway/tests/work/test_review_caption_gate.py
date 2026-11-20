"""검수자의 수정 후 승인이 라벨러 제출과 같은 캡션 기준을 지키는지 보는 테스트.

수정 후 승인은 검수자가 고친 캡션을 승인본으로 남긴다.
제출 게이트를 거치지 않으면 기준보다 짧은 캡션이 승인되어 납품 규격 오류로 남는다.
판정 자체는 `test_caption_gate.py`가 표로 검사하고, 이 파일은 판정 경로를 확인한다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Label, Review, WorkItem
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import (
    _CAPTION_FILLER,
    add_member,
    caption_payload,
    claim_review,
    latest_label_id,
    new_project,
    seed_clip_work,
    set_project_options,
    upload_video,
)


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _submitted(client: AsyncClient, admin_token: str, slug: str) -> tuple[str, str, str]:
    """라벨러가 기준을 채워 제출하고 검수자가 가져온 작업을 만들어 `(pid, 검수자 토큰, work_item_id)`를 돌려준다."""
    pid = await new_project(client, admin_token, slug)
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid, title=slug)
    _, wid = await seed_clip_work(pid, vid)
    assert (await client.post(f"/projects/{pid}/work/next", headers=_h(annot))).status_code == 200
    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": caption_payload("처음 라벨")})
    assert r.status_code == 200, r.text
    await claim_review(client, rev, pid, wid)
    return pid, rev, wid


async def _modify(client: AsyncClient, rev: str, wid: str, data: dict):
    """수정본으로 수정 후 승인을 요청한다."""
    return await client.post(
        f"/work/{wid}/review", headers=_h(rev),
        json={"decision": "modify_and_approve", "modified_label_data": data,
              "base_label_id": await latest_label_id(client, rev, wid), "reason": "문장 수정"},
    )


async def _state(wid: str) -> tuple[str, int, int, bool]:
    """`(status, 라벨 수, 판정 수, 검수 보유 여부)`를 읽는다. 막힌 판정이면 넷 다 그대로다."""
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(wid))
        labels = await s.scalar(
            select(func.count()).select_from(Label).where(Label.work_item_id == UUID(wid))
        )
        reviews = await s.scalar(
            select(func.count()).select_from(Review).where(Review.work_item_id == UUID(wid))
        )
        return item.status, labels, reviews, item.review_claimed_by is not None


@pytest.mark.parametrize("data", [
    # 아홉 글자 캡션이다. 기본 기준(50토큰)에 못 미친다.
    {"caption": {"brief": "짧은 캡션입니다.", "brief_sentences": [{"id": "s0", "text": "짧은 캡션입니다."}]}},
    # 긴 문장을 모두 삭제 표시한 수정본이다. 삭제한 문장은 납품 캡션에 없다.
    {"caption": {"brief": "", "brief_sentences": [
        {"id": "s0", "text": _CAPTION_FILLER, "deleted": True}]}},
    # 문자열만 있는 이전 모양도 같은 기준으로 잰다.
    {"caption": "검수자 수정본"},
], ids=["short", "all-deleted", "legacy-string"])
async def test_short_modified_caption_is_rejected(
    client: AsyncClient, admin_token: str, data: dict,
):
    """기준보다 짧은 수정본을 제출 게이트와 같은 오류(422 caption_too_short)로 거절하고 아무것도 바꾸지 않는다.

    작업은 제출 상태로 남고 검수 보유도 그대로라 검수자가 캡션을 채워 다시 판정할 수 있다.
    """
    _, rev, wid = await _submitted(client, admin_token, "rv-gate-short")
    r = await _modify(client, rev, wid, data)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "caption_too_short"
    assert await _state(wid) == ("submitted", 1, 0, True)


async def test_long_enough_modified_caption_is_approved(client: AsyncClient, admin_token: str):
    """기준을 채운 수정본은 승인되고 새 라벨 개정이 생긴다. 게이트가 정상 경로를 막지 않는다."""
    _, rev, wid = await _submitted(client, admin_token, "rv-gate-ok")
    r = await _modify(client, rev, wid, caption_payload("검수자가 고친 캡션"))
    assert r.status_code == 200, r.text
    assert await _state(wid) == ("approved", 2, 1, False)


async def test_review_gate_follows_the_project_setting(client: AsyncClient, admin_token: str):
    """수정본 기준도 프로젝트의 `caption_min_tokens`를 읽는다. 기준이 0이면 빈 수정본도 승인된다.

    검수 화면과 워크벤치의 `0 >= 0` 판정과 같은 기준이다. 서버를 화면보다 엄하게 만들지 않는다.
    """
    pid, rev, wid = await _submitted(client, admin_token, "rv-gate-zero")
    await set_project_options(pid, caption_min_tokens=0)
    r = await _modify(client, rev, wid, {"caption": {"brief": "", "brief_sentences": []}})
    assert r.status_code == 200, r.text
