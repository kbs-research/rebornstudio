"""제출이 화면이 안내한 캡션 기준을 서버에서도 지키는지 보는 테스트.

캡션 최소 길이(`caption_min_tokens`)는 고정 기본값(50토큰)이고 API로 바꿀 수 없다.
워크벤치는 프로젝트 응답의 그 기준으로 제출 버튼을 막는다.
이 파일은 서버가 같은 기준으로 제출을 막는지 테스트로 보장한다.

판정 자체(토큰 세는 법, 이전 payload 읽는 법)는 `test_caption_gate.py`가 표로 검사한다.
이 파일은 요청 경로를 확인한다.
막히는지, 막힐 때 아무것도 남지 않는지, 막히지 않아야 할 것이 지나가는지 확인한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, Label, WorkItem
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import (
    _CAPTION_FILLER,
    add_member,
    claim_review,
    new_project,
    seed_clip_work,
    set_project_options,
    upload_video,
)

#: 기본 기준(50토큰)을 넘는 캡션. 다른 테스트들이 쓰는 문장과 같다(65토큰).
#: 이 파일에서 따로 만들지 않는다.
#: 둘이 다르면 게이트 테스트는 통과하는데 나머지 테스트가 막히는 경우가 생긴다.
LONG = _CAPTION_FILLER


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


def _payload(text: str, *, deleted: bool = False) -> dict:
    """지금 화면이 보내는 payload 모양을 만든다. 문장 배열이 원본이고 문자열은 문장 배열에서 만든 값이다."""
    return {
        "caption": {
            "brief": "" if deleted else text,
            "brief_sentences": [{"id": "s0", "text": text, "deleted": deleted}],
        }
    }


async def _ready_claim(client: AsyncClient, admin_token: str, slug: str) -> tuple[str, str, str]:
    """프로젝트, 라벨러, 가져온 작업 하나를 준비해 `(pid, 라벨러 토큰, work_item_id)`를 돌려준다."""
    pid = await new_project(client, admin_token, slug)
    _, tok = await add_member(client, admin_token, pid, "annotator")
    vid = await upload_video(client, admin_token, pid, title=slug)
    _, wid = await seed_clip_work(pid, vid)
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(tok))
    assert r.status_code == 200, r.text
    assert r.json()["work_item_id"] == wid
    return pid, tok, wid


async def _state(work_item_id: str) -> tuple[str, int, int]:
    """`(status, 라벨 수, 제출 감사 이벤트 수)`를 읽는다. 막힌 제출이면 셋 다 그대로다."""
    async with SessionLocal() as s:
        item = await s.get(WorkItem, UUID(work_item_id))
        labels = await s.scalar(
            select(func.count()).select_from(Label).where(Label.work_item_id == UUID(work_item_id))
        )
        events = await s.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.event_type == "work.submitted",
                DomainEvent.target_id == UUID(work_item_id),
            )
        )
        return item.status, labels, events


# 세 경우: 빈 캡션, 기준 미달, 기준 충족


async def test_empty_caption_is_rejected(client: AsyncClient, admin_token: str):
    """빈 캡션 제출이 `HTTP 200`·`submitted`가 아니라 422로 거절되는지 확인한다.

    빈 제출이 검수 대기열에 들어가면 검수자의 시간을 쓴다.
    제출량은 사람을 재는 객관 지표라서 그 지표도 흔들린다.
    """
    _, tok, wid = await _ready_claim(client, admin_token, "sub-empty")
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(tok),
        json={"payload": {"caption": {"brief": "", "brief_sentences": []}}},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "caption_too_short"


async def test_rejected_submit_leaves_nothing_behind(client: AsyncClient, admin_token: str):
    """막힌 제출이 아무것도 남기지 않는지 확인한다.

    작업은 라벨러가 계속 보유하고, 라벨도 이벤트도 생기지 않는다.
    게이트를 쓰기 뒤에 두면 라벨 행만 쌓이고 상태는 바뀌지 않는 중간 상태가 생긴다.
    """
    _, tok, wid = await _ready_claim(client, admin_token, "sub-clean")
    assert await _state(wid) == ("claimed", 0, 0)

    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload("짧다.")})
    assert r.status_code == 422, r.text

    assert await _state(wid) == ("claimed", 0, 0)
    # 작업을 아직 보유하고 있으므로 캡션을 채워 다시 내면 된다. 되돌릴 수 없는 상태를 만들지 않는다.
    ok = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload(LONG)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "submitted"


async def test_short_caption_is_rejected(client: AsyncClient, admin_token: str):
    """기준 미달(3토큰 < 50) 캡션도 막히는지 확인한다."""
    _, tok, wid = await _ready_claim(client, admin_token, "sub-short")
    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload("짧다.")})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "caption_too_short"


async def test_long_enough_caption_passes(client: AsyncClient, admin_token: str):
    """기준을 채우면 제출되는지 확인한다. 게이트가 정상 경로를 막지 않는다."""
    _, tok, wid = await _ready_claim(client, admin_token, "sub-ok")
    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload(LONG)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"
    assert await _state(wid) == ("submitted", 1, 1)


# 기준은 프로젝트가 정한다


async def test_gate_follows_the_project_setting(client: AsyncClient, admin_token: str):
    """기준을 올리면 전에 통과하던 캡션이 막히는지 확인한다. 워크벤치가 읽는 기준과 같다.

    서버가 상수를 따로 두면 프로젝트 응답의 기준과 실제 판정이 달라진다. 서버가 그 컬럼을 실제로 읽는지
    확인하려고 기본값과 다른 값을 DB에 직접 쓴다.
    """
    pid, tok, wid = await _ready_claim(client, admin_token, "sub-knob")
    await set_project_options(pid, caption_min_tokens=200)
    got = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()
    assert got["caption_min_tokens"] == 200

    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload(LONG)})
    assert r.status_code == 422, "기준 200토큰인데 65토큰 캡션이 지나갔다"
    assert r.json()["detail"] == "caption_too_short"


async def test_zero_minimum_matches_the_screen(client: AsyncClient, admin_token: str):
    """기준이 0이면 빈 캡션도 제출되는지 확인한다. 워크벤치의 `0 >= 0`과 같은 기준이다.

    서버만 더 엄하면 화면은 통과인데 서버가 거절해서 라벨러가 고칠 곳을 찾지 못한다.
    그래서 서버를 화면보다 엄하게 만들지 않는다.
    """
    pid, tok, wid = await _ready_claim(client, admin_token, "sub-zero")
    await set_project_options(pid, caption_min_tokens=0)

    r = await client.post(
        f"/work/{wid}/submit", headers=_h(tok),
        json={"payload": {"caption": {"brief": "", "brief_sentences": []}}},
    )
    assert r.status_code == 200, r.text


# 삭제 표시 문장은 게이트를 통과시키지 못한다


async def test_deleted_sentences_do_not_open_the_gate(client: AsyncClient, admin_token: str):
    """지운 문장으로는 제출할 수 없는지 확인한다. 최종 캡션에 없는 문장은 검수자도 보지 못한다.

    라벨러가 삭제 표시만 해 둔 긴 문장이 게이트를 열면 검수자는 빈 캡션을 받는다.
    """
    _, tok, wid = await _ready_claim(client, admin_token, "sub-del")
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload(LONG, deleted=True)},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "caption_too_short"


async def test_live_sentence_counts_even_beside_a_deleted_one(
    client: AsyncClient, admin_token: str
):
    """살아 있는 문장이 기준을 채우면 지운 문장이 함께 있어도 제출되는지 확인한다.

    기준은 살아 있는 문장으로만 잰다.
    """
    _, tok, wid = await _ready_claim(client, admin_token, "sub-mix")
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(tok),
        json={
            "payload": {
                "caption": {
                    "brief": LONG,
                    "brief_sentences": [
                        {"id": "s0", "text": LONG},
                        {"id": "s1", "text": "지운 문장입니다.", "deleted": True},
                    ],
                }
            }
        },
    )
    assert r.status_code == 200, r.text


# 이전 payload를 막지 않는다


async def test_legacy_payload_can_be_resubmitted_after_rejection(
    client: AsyncClient, admin_token: str
):
    """이전 모양의 payload로 다시 제출해도 막지 않는지 확인한다. 막으면 그 작업은 재작업할 수 없게 된다.

    이전 payload는 `brief` 문자열만 있고 `brief_sentences`가 비어 있다.
    이전 버전에서 저장한 행에 이 모양이 남아 있다. 이유는 `caption_qc.runner._sentences_of`의 docstring에 있다.
    배열만 보는 게이트는 그 캡션을 0토큰으로 읽어 반려 후 재제출을 계속 막는다.
    그러면 라벨러가 화면에서 할 수 있는 일이 없어진다.

    `caption`이 문자열이던 더 이전 모양은 다음 테스트가 확인한다.
    """
    pid, tok, wid = await _ready_claim(client, admin_token, "sub-legacy")
    _, rev = await add_member(client, admin_token, pid, "reviewer")

    first = await client.post(
        f"/work/{wid}/submit", headers=_h(tok), json={"payload": _payload(LONG)},
    )
    assert first.status_code == 200, first.text

    await claim_review(client, rev, pid, wid)
    rj = await client.post(
        f"/work/{wid}/review", headers=_h(rev), json={"decision": "reject", "reason": "다시"},
    )
    assert rj.status_code == 200, rj.text

    # 배열이 빈 이전 모양이다. 게이트는 `brief`로 대체해 읽는다.
    again = await client.post(
        f"/work/{wid}/submit", headers=_h(tok),
        json={"payload": {"caption": {"brief": LONG, "brief_sentences": []}}},
    )
    assert again.status_code == 200, f"옛 payload 재제출이 막혔다: {again.text}"


async def test_legacy_string_caption_is_read_too(client: AsyncClient, admin_token: str):
    """`caption`이 문자열이던 모양도 읽는지 확인한다. `_caption_of`와 같은 이유다."""
    _, tok, wid = await _ready_claim(client, admin_token, "sub-str")
    r = await client.post(f"/work/{wid}/submit", headers=_h(tok), json={"payload": {"caption": LONG}})
    assert r.status_code == 200, r.text


def test_delivery_reads_what_the_gate_read():
    """제출 게이트가 읽은 글자를 납품도 읽는지 확인한다.

    판정이 두 곳에 있으면 결과가 갈린다.
    그래서 납품이 읽는 캡션이 게이트가 쓰는 `caption.final_text`와 같은지 확인한다.
    납품 쪽이 `brief` 키만 보면 문장 배열만 담긴 제출(API로 낸 것, 화면이 바뀐 뒤의 것)은 제출이 200인데 완료 목록에서 "캡션 없음 · 납품 불가"가 된다.
    화면은 `brief`와 `brief_sentences`를 둘 다 보내서 평소에는 드러나지 않는다.
    """
    from api_gateway.services import caption
    from api_gateway.services.exports import export_service

    sentences_only = {"caption": {"brief_sentences": [
        {"text": "앵커가 소식을 전하고 있습니다."},
        {"text": "지운 문장입니다.", "deleted": True},
    ]}}
    want = caption.final_text(sentences_only)
    assert want == "앵커가 소식을 전하고 있습니다."
    # 완료 목록과 납품 메타가 읽는 값이다. 둘 다 게이트와 같은 글자여야 한다.
    assert export_service._caption_of(sentences_only).brief == want
    # 문장 배열은 그대로 실어 보낸다. 문장 수와 구간은 문장 배열에서 센다.
    assert len(export_service._caption_of(sentences_only).brief_sentences) == 2


def _row_with(sentences: list[dict]):
    """완료 행 하나를 실제 함수(`_completed_row`)로 만든다.

    도우미만 검사하면 호출부가 규약을 지키는지 알 수 없다.
    테스트가 `caption.live_sentences`를 직접 부르면 통과해도 호출부를 보장하지 않는다.
    이 검사는 `_completed_row`가 그 규약을 거치는지 확인한다.
    """
    import uuid as _u
    from datetime import UTC, datetime

    from api_gateway.models import Clip, Video, WorkItem
    from api_gateway.services.exports import export_service

    vid, pid = _u.uuid4(), _u.uuid4()
    w = WorkItem(work_item_id=_u.uuid4(), project_id=pid, video_id=vid,
                 payload={"caption": {"brief_sentences": sentences}})
    v = Video(video_id=vid, project_id=pid, title="t", duration_seconds=10.0, extra={})
    c = Clip(clip_id=_u.uuid4(), video_id=vid, project_id=pid,
             start_seconds=0.0, end_seconds=5.0, auto_caption={})
    return export_service._completed_row(w, v, c, datetime.now(UTC))


def test_completed_row_does_not_count_deleted_sentences():
    """삭제 표시한 문장을 문장 수에 넣지 않는지 확인한다.

    `live_sentences`의 docstring은 지운 문장이 제출 게이트·품질검수·내보내기에도 없어야 한다고 정한다.
    완료 행도 그 규약을 거쳐야 캡션과 배지의 수가 한 행 안에서 어긋나지 않는다.
    """
    row = _row_with([
        {"text": "첫 문장입니다.", "start": 0.0, "end": 1.0},
        {"text": "지운 문장입니다.", "deleted": True, "start": 1.0, "end": 2.0},
        {"text": "셋째 문장입니다."},
    ])
    # 문장 수는 사람이 보는 캡션의 문장 수와 같아야 한다.
    assert row.sentence_count == 2, "지운 문장을 셌다"
    # 캡션 글자와도 맞는다. 한 행 안에서 두 수가 어긋나면 안 된다.
    assert "지운 문장" not in (row.caption_brief or "")


def test_legacy_string_payload_still_counts():
    """이전 문자열 payload를 그대로 두는지 확인한다. 문자열에는 삭제 개념이 없다.

    배열만 세면 캡션이 있는 건도 `캡션 0`으로 표시된다.
    """
    from api_gateway.services import caption
    from api_gateway.services.exports import export_service

    cap = export_service._caption_of({"caption": "옛 캡션입니다. 두 문장입니다."})
    assert cap.brief and not cap.brief_sentences
    # 배열이 비어 있으므로 live 결과도 비어 있다. 호출부는 이때 문자열을 쪼갠다.
    assert caption.live_sentences(cap.brief_sentences) == []
