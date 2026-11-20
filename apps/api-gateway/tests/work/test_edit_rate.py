"""수정률 테스트. AI 초안 보관, 편집 거리, 라벨러 집계와 분모, 권한을 확인한다.

순수 계산(`services/work/edit_rate.py`)은 DB 없이 검사하고, 제출·집계는 API로 끝까지 검사한다.
기대값의 편집 거리는 교과서식 표 계산(`_reference_distance`)으로 따로 구한다.
구현이 쓰는 비트 병렬 식으로 기대값을 구하면 둘이 함께 틀려도 테스트가 통과한다.

이 파일의 프로젝트는 캡션 제출 게이트를 꺼 둔다(`caption_min_tokens=0`).
캡션이 측정 대상이라서, 기준을 넘기려고 채움 문장을 붙이면 초안과의 거리가 그 문장에 묻힌다.
게이트 자체는 `test_submit_caption_gate.py`가 검사한다.
"""
import random
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Label
from api_gateway.services.work import edit_rate
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from tests._helpers import (
    add_member,
    claim_review,
    latest_label_id,
    new_project,
    seed_clip_work,
    upload_video,
)


def _reference_distance(a: str, b: str) -> int:
    """표를 채우는 교과서식 레벤슈타인 거리를 구한다. 비트 병렬 구현과 대조할 기준이다."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _expected(draft: str, caption: str) -> tuple[int, int]:
    """정규화한 두 문자열의 (편집 거리, 초안 글자 수) 기대값을 표 계산으로 구한다."""
    base = edit_rate.normalize(draft)
    return _reference_distance(base, edit_rate.normalize(caption)), len(base)


# ── 순수 계산 ────────────────────────────────────────────────────────────────


def test_edit_distance_matches_the_textbook_table():
    """비트 병렬 구현이 교과서식 표와 같은 값을 내는지 확인한다.

    한글·공백·빈 문자열과 짧은 쪽이 바뀌는 경우도 포함한다.
    """
    rng = random.Random(20260915)
    alphabet = "가나다라 마바사.,ab"
    for _ in range(600):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert edit_rate.edit_distance(a, b) == _reference_distance(a, b), (a, b)
    # 짧은 쪽이 64자를 넘는 경우다. 비트 벡터가 기계 워드보다 커져도 같은 값이어야 한다.
    for _ in range(20):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(100, 180)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(100, 180)))
        assert edit_rate.edit_distance(a, b) == _reference_distance(a, b)


def test_edit_distance_known_cases():
    """넣기·빼기·바꾸기 한 글자처럼 손으로 셀 수 있는 경우와 긴 캡션의 값이 맞는지 확인한다."""
    assert edit_rate.edit_distance("", "") == 0
    assert edit_rate.edit_distance("가나다", "") == 3
    assert edit_rate.edit_distance("", "가나다") == 3
    assert edit_rate.edit_distance("kitten", "sitting") == 3
    assert edit_rate.edit_distance("무함마드가", "무함마드는") == 1  # 조사 한 글자
    assert edit_rate.edit_distance("소환했습니다.", "소환했다.") == 2  # `습니` 빼기
    # 2,000자에서 공통 앞부분을 떼면 남는 1,000자가 모두 바뀐 경우다.
    assert edit_rate.edit_distance("가" * 2000, "가" * 1000 + "나" * 1000) == 1000
    # 2,000자에서 앞뒤가 겹치지 않아 전체 구간을 계산하는 경우다.
    # 한 글자 밀린 것이라 빼기 1 + 넣기 1이다.
    assert edit_rate.edit_distance("가나" * 1000, "나가" * 1000) == 2


def test_normalize_folds_whitespace_and_composes_hangul():
    """줄바꿈과 연속 공백을 공백 하나로 바꾸고 자모로 풀린 한글(NFD)을 음절로 합치는지 확인한다."""
    assert edit_rate.normalize("  첫 문장.\n\n둘째   문장.  ") == "첫 문장. 둘째 문장."
    nfd = unicodedata.normalize("NFD", "한글")
    assert len(nfd) > 2
    assert edit_rate.normalize(nfd) == "한글"


def test_unedited_ai_draft_measures_zero():
    """AI 초안을 고치지 않고 내면 워크벤치가 문장으로 나눴다가 다시 이어도 0인지 확인한다.

    워크벤치는 초안을 문장으로 나눈 뒤(`splitSentences`) 공백 하나로 다시 잇는다(`joinSentences`).
    줄바꿈이 공백이 되므로 정규화가 없으면 고치지 않은 제출이 수정으로 잡힌다.
    """
    draft = "시장이 기자회견을 열었습니다.\n질문을 받았습니다."
    rejoined = "시장이 기자회견을 열었습니다. 질문을 받았습니다."  # 화면이 보내는 brief
    m = edit_rate.measure({"summary": f"  {draft}  "}, {"caption": {"brief": rejoined}})
    assert m is not None
    assert m.distance == 0
    assert m.draft == draft  # 원문은 앞뒤 공백만 뗀다. 화면이 그대로 표시한다.
    assert m.draft_chars == len(rejoined)


def test_caption_text_reads_every_payload_shape():
    """이전 형식의 문자열, `brief`, 문장 배열(삭제 표시·빈 문장 제외)을 모두 읽는지 확인한다.

    납품이 읽는 캡션과 같은 값이다.
    """
    assert edit_rate.caption_text_of({"caption": "옛 캡션"}) == "옛 캡션"
    assert edit_rate.caption_text_of({"caption": {"brief": "요약", "brief_sentences": []}}) == "요약"
    sentences = [
        {"id": "s0", "text": "남는 문장."},
        {"id": "s1", "text": "지운 문장.", "deleted": True},
        {"id": "s2", "text": "   "},
        {"id": "s3", "text": "끝 문장."},
    ]
    assert (
        edit_rate.caption_text_of({"caption": {"brief_sentences": sentences}})
        == "남는 문장. 끝 문장."
    )
    assert edit_rate.caption_text_of({}) == ""
    assert edit_rate.caption_text_of({"caption": 3}) == ""


@pytest.mark.parametrize(
    "auto_caption",
    [None, {}, {"summary": None}, {"summary": "   \n "}, {"summary": 3}, "요약"],
)
def test_no_ai_draft_is_not_measured(auto_caption):
    """초안이 없으면 재지 않고 0 대신 `None`을 내는지 확인한다. 0이면 고치지 않은 것으로 읽힌다."""
    assert edit_rate.measure(auto_caption, {"caption": "무엇이든"}) is None


# ── 제출과 집계 ──────────────────────────────────────────────────────────────


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _clip_work(
    client: AsyncClient, admin_token: str, pid: str, *, summary: str | None, title: str
) -> str:
    """영상 하나와 클립 작업 하나를 만든다.

    `summary`를 주면 그 클립의 AI 초안(장면 요약)으로 넣는다.
    """
    vid = await upload_video(client, admin_token, pid, title=title)
    clip_id, wid = await seed_clip_work(pid, vid)
    if summary is not None:
        async with SessionLocal() as s:
            await s.execute(
                update(Clip).where(Clip.clip_id == UUID(clip_id))
                .values(auto_caption={"summary": summary})
            )
            await s.commit()
    return wid


async def _claim(client: AsyncClient, tok: str, pid: str, wid: str) -> None:
    """라벨링 작업을 가져오고, 서버가 정한 순서에서 기대한 작업이 왔는지 확인한다."""
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(tok))
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == wid, f"{wid} 대신 {r.json()['work_item_id']}이 왔다"


async def _submit(client: AsyncClient, tok: str, wid: str, caption: str, **extra) -> None:
    """캡션 한 줄로 제출한다. `extra`는 payload에 함께 싣는 값이고 서버가 신뢰하지 않아야 한다."""
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(tok),
        json={"payload": {"caption": {"brief": caption}, **extra}},
    )
    assert r.status_code == 200, r.text


async def _labels(wid: str) -> list[Label]:
    """그 작업의 라벨 행을 제출 순서로 읽는다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(Label).where(Label.work_item_id == UUID(wid)).order_by(Label.submitted_at)
        )).all())


async def _perf_row(
    client: AsyncClient, admin_token: str, pid: str, uid: str, **params
) -> dict:
    """운영자 표(`labeler-performance`)에서 그 사람의 행을 읽는다."""
    r = await client.get(
        f"/projects/{pid}/dashboard/labeler-performance", headers=_h(admin_token), params=params,
    )
    assert r.status_code == 200, r.text
    return next(x for x in r.json() if x["user_id"] == uid)


async def test_first_submission_keeps_the_ai_draft_the_server_read(
    client: AsyncClient, admin_token: str
):
    """첫 제출이 라벨러가 받은 AI 초안과 초안 대비 수정량을 남기고, 초안을 서버가 직접 읽는지 확인한다.

    payload에 초안이라고 표시한 값을 실어 보내도 쓰지 않는다.
    그 값을 믿으면 제출본과 같은 문자열을 초안으로 보내 수정률을 0으로 만들 수 있다.
    """
    pid = await new_project(client, admin_token, "er-first", caption_min_tokens=0)
    _, annot = await add_member(client, admin_token, pid, "annotator")
    summary = "  시장이 회견을 열었습니다.\n질문을 받았습니다. "
    wid = await _clip_work(client, admin_token, pid, summary=summary, title="v1")

    await _claim(client, annot, pid, wid)
    caption = "시장이 기자회견을 열었습니다. 질문에 답했습니다."
    await _submit(client, annot, wid, caption, ai_draft=caption, seed_source=caption)

    (row,) = await _labels(wid)
    distance, chars = _expected(summary, caption)
    assert distance > 0
    assert row.parent_label_id is None
    assert row.ai_draft == summary.strip()  # 서버가 클립에서 읽은 초안이다. payload의 `ai_draft`는 쓰지 않는다.
    assert (row.edit_distance, row.draft_chars) == (distance, chars)

    # 개정 이력 조회가 첫 단계(AI 초안)를 함께 준다.
    r = await client.get(f"/work/{wid}/labels", headers=_h(annot))
    assert r.status_code == 200, r.text
    (body,) = r.json()
    assert body["ai_draft"] == summary.strip()
    assert (body["edit_distance"], body["draft_chars"]) == (distance, chars)


async def test_clip_without_ai_draft_is_not_measured_and_not_zero(
    client: AsyncClient, admin_token: str
):
    """초안이 없던 클립은 재지 않아 라벨의 측정 필드가 비고 사람 표의 수정률도 `None`인지 확인한다."""
    pid = await new_project(client, admin_token, "er-nodraft", caption_min_tokens=0)
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    wid = await _clip_work(client, admin_token, pid, summary=None, title="v1")

    await _claim(client, annot, pid, wid)
    await _submit(client, annot, wid, "초안 없이 쓴 캡션입니다.")

    (row,) = await _labels(wid)
    assert (row.ai_draft, row.edit_distance, row.draft_chars) == (None, None, None)

    perf = await _perf_row(client, admin_token, pid, uid)
    assert perf["submitted"] == 1
    assert perf["edit_measured"] == 0
    assert perf["edit_rate"] is None  # 재지 않았으면 None이다. 0.0이면 고치지 않은 것으로 읽힌다.
    assert (perf["edit_distance"], perf["draft_chars"]) == (0, 0)


async def test_resubmission_after_rejection_is_not_measured_again(
    client: AsyncClient, admin_token: str
):
    """반려 뒤 재제출을 다시 재지 않는지 확인한다. 재제출은 직전 제출에서 시작하므로 다시 재면 같은 교정을 두 번 센다."""
    pid = await new_project(client, admin_token, "er-rework", caption_min_tokens=0)
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    draft = "초안 문장입니다."
    wid = await _clip_work(client, admin_token, pid, summary=draft, title="v1")

    await _claim(client, annot, pid, wid)
    first = "초안 문장을 고쳤습니다."
    await _submit(client, annot, wid, first)
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers=_h(rev), json={"decision": "reject", "reason": "수정 필요"},
    )
    assert r.status_code == 200, r.text
    # 재작업은 원래 작업자가 보유한 상태로 돌아온다. 다시 가져오지 않고 바로 제출한다.
    await _submit(client, annot, wid, "완전히 새로 쓴 캡션입니다. 문장을 더 붙였습니다.")

    first_row, second_row = await _labels(wid)
    assert second_row.parent_label_id == first_row.label_id
    assert (first_row.edit_distance, first_row.draft_chars) == _expected(draft, first)
    assert (second_row.ai_draft, second_row.edit_distance, second_row.draft_chars) == (
        None, None, None,
    )

    perf = await _perf_row(client, admin_token, pid, uid)
    assert perf["submitted"] == 2  # 처리량은 재제출도 센다
    assert perf["edit_measured"] == 1  # 수정률은 작업의 첫 제출만 센다


async def test_labeler_edit_rate_is_a_ratio_of_sums_with_its_denominator(
    client: AsyncClient, admin_token: str
):
    """라벨러의 수정률이 Σ편집 거리 ÷ Σ초안 글자 수이고 잰 건수와 글자 수 분자·분모를 함께 주는지 확인한다.

    재지 않은 제출(초안이 없던 클립, 0080 이전 제출)은 분모에 넣지 않는다.
    0으로 넣으면 수정률이 실제보다 낮아진다.
    """
    pid = await new_project(client, admin_token, "er-sum", caption_min_tokens=0)
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    specs = [
        ("가나다라마바사아자차카타파하.", "가나다라마바사아자차카타파하입니다."),
        ("짧은 초안.", "전혀 다른 긴 문장으로 새로 썼습니다."),
        (None, "초안 없이 쓴 캡션입니다."),
    ]
    wids = [
        await _clip_work(client, admin_token, pid, summary=d, title=f"v{i}")
        for i, (d, _) in enumerate(specs)
    ]
    for wid, (_, caption) in zip(wids, specs, strict=True):
        await _claim(client, annot, pid, wid)
        await _submit(client, annot, wid, caption)

    # 0080 이전 제출이다. 측정 필드 세 개가 빈 채 남아 있다(백필하지 않는다).
    # 빈 측정 필드는 측정하지 않았다는 뜻이다.
    past_wid = await _clip_work(client, admin_token, pid, summary="과거 초안.", title="past")
    async with SessionLocal() as s:
        s.add(Label(work_item_id=UUID(past_wid), submitted_by=UUID(uid), label_data={}))
        await s.commit()

    measured = [_expected(d, c) for d, c in specs if d is not None]
    total_distance = sum(d for d, _ in measured)
    total_chars = sum(c for _, c in measured)
    perf = await _perf_row(client, admin_token, pid, uid)
    assert perf["submitted"] == 4
    assert perf["edit_measured"] == 2
    assert (perf["edit_distance"], perf["draft_chars"]) == (total_distance, total_chars)
    assert perf["edit_rate"] == round(total_distance / total_chars * 100, 1)
    # 두 번째 건은 초안보다 길게 새로 썼다. 건 단위로는 100%를 넘을 수 있다.
    # 표의 값은 합계끼리의 비율이다.
    assert measured[1][0] > measured[1][1]


async def test_since_window_cuts_the_edit_rate_too(client: AsyncClient, admin_token: str):
    """기간 창을 수정률에도 적용해 제출 수와 같은 행 집합에서 세는지 확인한다."""
    pid = await new_project(client, admin_token, "er-since", caption_min_tokens=0)
    uid, annot = await add_member(client, admin_token, pid, "annotator")
    specs = [("오래된 초안입니다.", "오래된 초안을 고쳤습니다."), ("새 초안입니다.", "새 초안입니다!")]
    wids = [
        await _clip_work(client, admin_token, pid, summary=d, title=f"v{i}")
        for i, (d, _) in enumerate(specs)
    ]
    for wid, (_, caption) in zip(wids, specs, strict=True):
        await _claim(client, annot, pid, wid)
        await _submit(client, annot, wid, caption)
    async with SessionLocal() as s:
        await s.execute(
            update(Label).where(Label.work_item_id == UUID(wids[0]))
            .values(submitted_at=datetime.now(UTC) - timedelta(days=30))
        )
        await s.commit()

    assert (await _perf_row(client, admin_token, pid, uid))["edit_measured"] == 2
    cut = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    narrowed = await _perf_row(client, admin_token, pid, uid, since=cut)
    distance, chars = _expected(*specs[1])
    assert narrowed["submitted"] == 1
    assert narrowed["edit_measured"] == 1
    assert (narrowed["edit_distance"], narrowed["draft_chars"]) == (distance, chars)


async def test_reviewer_revision_is_not_the_labelers_edit_and_the_chain_has_three_stages(
    client: AsyncClient, admin_token: str
):
    """검수자의 수정 후 승인을 재지 않고, 완료 상세의 이력이 AI 초안부터 시작하는지 확인한다.

    이력은 AI 초안(첫 제출 행의 `ai_draft`), 라벨러 제출본, 검수 수정본 순서다.
    """
    pid = await new_project(client, admin_token, "er-chain", caption_min_tokens=0)
    annot_uid, annot = await add_member(client, admin_token, pid, "annotator")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    draft = "가나다라마바사."
    wid = await _clip_work(client, admin_token, pid, summary=draft, title="v1")

    await _claim(client, annot, pid, wid)
    await _submit(client, annot, wid, "가나다라마바사아.")
    await claim_review(client, rev, pid, wid)
    r = await client.post(
        f"/work/{wid}/review", headers=_h(rev),
        json={
            "decision": "modify_and_approve",
            "modified_label_data": {"caption": {"brief": "검수자가 완전히 새로 쓴 캡션입니다."}},
            "base_label_id": await latest_label_id(client, rev, wid),
        },
    )
    assert r.status_code == 200, r.text

    labeler_row, reviewer_row = await _labels(wid)
    assert (labeler_row.edit_distance, labeler_row.draft_chars) == (1, 8)  # `아` 하나 넣기
    assert str(reviewer_row.submitted_by) == rev_uid
    assert (reviewer_row.ai_draft, reviewer_row.edit_distance) == (None, None)

    annot_perf = await _perf_row(client, admin_token, pid, annot_uid)
    assert (annot_perf["edit_measured"], annot_perf["edit_rate"]) == (1, 12.5)
    rev_perf = await _perf_row(client, admin_token, pid, rev_uid)
    assert (rev_perf["edit_measured"], rev_perf["edit_rate"]) == (0, None)

    r = await client.get(f"/projects/{pid}/completed/{wid}", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    first, second = r.json()["revisions"]
    assert (first["ai_draft"], first["edit_distance"], first["draft_chars"]) == (draft, 1, 8)
    assert second["ai_draft"] is None


async def test_ai_draft_is_readable_only_where_the_label_chain_is(
    client: AsyncClient, admin_token: str
):
    """초안을 개정 이력과 같은 권한으로만 조회해 다른 라벨러는 403을 받고 검수자는 볼 수 있는지 확인한다.

    수정률 표(운영자 전용)는 라벨러에게 닫혀 있다. 라벨러는 자기 수만 `/me`로 본다.
    """
    pid = await new_project(client, admin_token, "er-authz", caption_min_tokens=0)
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, other = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    wid = await _clip_work(client, admin_token, pid, summary="권한 확인용 초안입니다.", title="v1")
    await _claim(client, annot, pid, wid)
    await _submit(client, annot, wid, "권한 확인용 캡션입니다.")

    r = await client.get(f"/work/{wid}/labels", headers=_h(other))
    assert r.status_code == 403, r.text
    r = await client.get(f"/work/{wid}/labels", headers=_h(rev))
    assert r.status_code == 200, r.text
    assert r.json()[0]["ai_draft"] == "권한 확인용 초안입니다."

    r = await client.get(f"/projects/{pid}/dashboard/labeler-performance", headers=_h(annot))
    assert r.status_code == 403, r.text


async def test_measure_columns_are_all_or_nothing(client: AsyncClient, admin_token: str):
    """초안 없이 거리만 남는 행을 DB가 거절하는지(CHECK) 확인한다.

    그런 행은 거리를 잰 기준 초안을 알 수 없다.
    """
    pid = await new_project(client, admin_token, "er-check", caption_min_tokens=0)
    uid, _ = await add_member(client, admin_token, pid, "annotator")
    wid = await _clip_work(client, admin_token, pid, summary=None, title="v1")
    async with SessionLocal() as s:
        s.add(Label(
            work_item_id=UUID(wid), submitted_by=UUID(uid), label_data={}, edit_distance=3,
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


def test_caption_text_is_the_same_rule_as_the_gate():
    """제출 게이트·수정률·납품이 같은 캡션 글자를 쓰는지 확인한다.

    이 모듈이 캡션을 따로 추출해 `brief`를 먼저 보고 없을 때만 문장 배열을 보면 우선순위가 기준 규칙과 반대가 된다.
    화면이 둘 다 보내므로 평소에는 같지만, 둘이 다르면 결과가 달라진다.
    """
    from api_gateway.services import caption

    payload = {"caption": {
        "brief": "옛 문장이 남아 있다",
        "brief_sentences": [
            {"text": "새로 고친 문장이다."},
            {"text": "지운 문장이다.", "deleted": True},
        ],
    }}
    got = edit_rate.caption_text_of(payload)
    assert got == caption.final_text(payload)
    # 문장 배열이 기준이고, 삭제 표시한 문장은 빠진다.
    # `brief`를 쓰면 납품되지 않을 글자를 재게 된다.
    assert got == "새로 고친 문장이다."
