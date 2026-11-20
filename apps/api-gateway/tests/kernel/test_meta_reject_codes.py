"""영상 메타 상속과 반려 사유 코드를 서버가 지키는지 보는 테스트.

방송사·프로그램명·장르·방영일은 영상 단위 값이다.
관리자가 영상에 넣고 클립이 물려받는다.
납품·품질검수·워크벤치는 영상 값을 읽고 라벨러 입력(`payload.meta`)은 읽지 않는다.

반려 사유는 코드 배열로 저장한다. 목록은 고정 기본값이고 API로 바꿀 수 없다.
저장된 목록이 있으면 그 목록을 읽는다. 코드는 바뀌지 않고, 꺼진 사유도 이력에서는 이름으로 읽힌다.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, DomainEvent, Project, Review, Video, WorkItem
from api_gateway.services import reject_reasons
from api_gateway.services.caption_qc import runner
from api_gateway.services.caption_qc import settings as qc_settings
from api_gateway.services.exports.export_package import build_metadata
from httpx import AsyncClient
from reborn_contracts import RejectReasonRef
from sqlalchemy import select, text
from tests._helpers import (
    add_member,
    claim_review,
    new_project,
    seed_clip_work,
    set_project_options,
    upload_video,
)

_KST = timezone(timedelta(hours=9))
_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0081_meta_reject_codes.py"

#: 이 파일의 테스트가 쓰는 문장 개체. 제출 게이트(`caption_min_tokens` 기본 50)를
#: 넘도록 실제 캡션 길이로 둔다. 짧으면 제출이 422로 막혀 반려까지 가지 못한다.
_SENTS = [
    {"id": "s1", "text": "앵커가 스튜디오에서 오늘의 주요 뉴스를 차례로 전합니다."},
    {"id": "s2", "text": "화면 하단에 기사 제목과 자막이 함께 표시되어 있습니다."},
    {"id": "s3", "text": "이어서 현장에 나가 있는 기자를 연결해 상황을 자세히 묻습니다."},
]
_CAPTION = {"brief": " ".join(s["text"] for s in _SENTS), "brief_sentences": _SENTS}
_BUILTIN_ORDER = [
    "caption_inaccurate", "deident_insufficient", "metadata_missing",
]


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


def _migration():
    """마이그레이션 0081을 모듈로 읽는다. 백필을 마이그레이션과 같은 SQL로 검증한다."""
    spec = importlib.util.spec_from_file_location("m0081_meta_reject_codes", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _submitted(client: AsyncClient, admin_token: str) -> dict:
    """라벨러가 제출하고 검수자가 가져온 작업 하나를 만든다. 판정 테스트에 쓴다."""
    pid = await new_project(client, admin_token, "rcode")
    annot_uid, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    r = await client.post(f"/projects/{pid}/work/next", headers=_h(annot))
    assert r.status_code == 200 and r.json()["work_item_id"] == wid, r.text
    r = await client.post(f"/work/{wid}/submit", headers=_h(annot),
                          json={"payload": {"caption": _CAPTION}})
    assert r.status_code == 200, r.text
    await claim_review(client, rev, pid, wid)
    return {"pid": pid, "vid": vid, "wid": wid, "annot": annot, "annot_uid": annot_uid, "rev": rev}


async def _reviews(wid: str, reviewer: str, rows: list[tuple]) -> list[UUID]:
    """판정 행 `(verdict, codes, comment, reviewed_at)`을 DB에 직접 넣는다.

    백필과 통계 테스트에 쓴다.
    """
    ids: list[UUID] = []
    async with SessionLocal() as s:
        for verdict, codes, comment, at in rows:
            r = Review(work_item_id=UUID(wid), reviewer_id=UUID(reviewer), verdict=verdict,
                       reason_codes=codes, comment=comment, reviewed_at=at)
            s.add(r)
            await s.flush()
            ids.append(r.review_id)
        await s.commit()
    return ids


def _listing(project_body: dict) -> list[dict]:
    """프로젝트 응답의 사유 목록을 `settings`에 저장하는 형태로 바꾼다."""
    return [{"code": x["code"], "label": x["label"], "enabled": x["enabled"]}
            for x in project_body["reject_reasons"]]


# 영상 메타는 관리자가 영상에 넣는다


async def test_project_admin_edits_video_meta_and_it_is_audited(
    client: AsyncClient, admin_token: str,
):
    """프로젝트 관리자가 영상 메타를 넣을 수 있고 변경이 감사 기록에 남는지 확인한다.

    네 값은 그 영상의 모든 클립이 물려받으므로 변경을 기록한다.
    """
    pid = await new_project(client, admin_token, "vmeta")
    vid = await upload_video(client, admin_token, pid)
    _, op_tok = await add_member(client, admin_token, pid, "project_admin")
    r = await client.patch(f"/videos/{vid}", headers=_h(op_tok), json={
        "genre": "보도", "broadcaster": " 샘플 방송 ", "aired_at": "2026-09-01T00:00:00+09:00",
    })
    assert r.status_code == 200, r.text
    assert r.json()["genre"] == "보도" and r.json()["broadcaster"] == "샘플 방송", "공백은 떼고 저장한다"
    async with SessionLocal() as s:
        evs = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "video.updated", DomainEvent.target_id == UUID(vid),
        ))).all()
    assert len(evs) == 1
    assert set(evs[0].payload["changed"]) == {"genre", "broadcaster", "aired_at"}


@pytest.mark.parametrize("role", ["annotator", "reviewer"])
async def test_labeler_and_reviewer_cannot_edit_video_meta(
    client: AsyncClient, admin_token: str, role: str,
):
    """라벨러와 검수자의 영상 메타 수정을 403으로 막는지 확인한다. 영상 단위 값은 관리자가 관리한다."""
    pid = await new_project(client, admin_token, "vmeta-perm")
    vid = await upload_video(client, admin_token, pid)
    _, tok = await add_member(client, admin_token, pid, role)
    r = await client.patch(f"/videos/{vid}", headers=_h(tok), json={"genre": "보도"})
    assert r.status_code == 403 and r.json()["detail"] == "not_project_admin", r.text


async def test_labeler_reads_inherited_meta_on_the_clip(client: AsyncClient, admin_token: str):
    """라벨러가 클립 조회 응답에서 물려받은 네 값을 읽는지 확인한다. 라벨러에게 영상 API를 따로 열지 않는다."""
    pid = await new_project(client, admin_token, "vmeta-clip")
    vid = await upload_video(client, admin_token, pid)
    cid, _wid = await seed_clip_work(pid, vid)
    assert (await client.patch(f"/videos/{vid}", headers=_h(admin_token), json={
        "broadcaster": "샘플 방송", "program_name": "9시 뉴스 샘플", "genre": "보도",
        "aired_at": "2026-09-01T00:00:00+09:00",
    })).status_code == 200
    _, annot = await add_member(client, admin_token, pid, "annotator")
    r = await client.get(f"/projects/{pid}/clips/{cid}", headers=_h(annot))
    assert r.status_code == 200, r.text
    vm = r.json()["video_meta"]
    assert (vm["broadcaster"], vm["program_name"], vm["genre"]) == ("샘플 방송", "9시 뉴스 샘플", "보도")
    assert datetime.fromisoformat(vm["aired_at"].replace("Z", "+00:00")) == datetime(
        2026, 9, 1, tzinfo=_KST)


def test_delivery_meta_comes_from_the_video_not_the_labeler():
    """납품 메타의 네 값이 영상 값에서 오는지 확인한다.

    라벨러가 옛 방식으로 넣은 `meta`는 읽지 않는다.
    """
    payload = {"caption": {"brief": "앵커가 오늘의 주요 뉴스를 전합니다."},
               "meta": {"genre": "정치", "broadcaster": "견본 방송", "program": "다른 프로",
                        "air_date": "2020-01-01"}}
    w = WorkItem(work_item_id=uuid4(), video_id=uuid4(), project_id=uuid4(), payload=payload)
    c = Clip(clip_id=uuid4(), start_seconds=0.0, end_seconds=10.0, rights_status="auto_cleared")
    aired = datetime(2026, 9, 1, tzinfo=_KST)
    v = Video(video_id=uuid4(), project_id=uuid4(), title="t", broadcaster="샘플 방송",
              program_name="9시 뉴스 샘플", genre="보도", aired_at=aired, extra={})
    m = build_metadata(w, v, c, None, 0.0)
    assert (m.broadcaster, m.program_name, m.genre, m.aired_at) == ("샘플 방송", "9시 뉴스 샘플", "보도", aired)
    # 영상 값이 비어 있으면 빈 채로 나간다. 라벨러 입력으로 채우지 않는다(키·형식은 그대로).
    empty = Video(video_id=uuid4(), project_id=uuid4(), title="t", extra={})
    m2 = build_metadata(w, empty, c, None, 0.0)
    assert (m2.broadcaster, m2.program_name, m2.genre, m2.aired_at) == (None, None, None, None)


async def test_qc_reads_meta_from_the_video_not_the_labeler(client: AsyncClient, admin_token: str):
    """품질검수의 메타 지적이 영상 값을 기준으로 나오는지 확인한다.

    영상에 값을 넣으면 지적이 사라진다.
    """
    pid = await new_project(client, admin_token, "vmeta-qc")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.broadcaster, v.program_name, v.genre = "샘플 방송", "9시 뉴스 샘플", None
        v.aired_at = datetime.now(UTC)
        v.duration_seconds = 1800.0
        p = await s.get(Project, UUID(pid))
        p.settings = {**(p.settings or {}), qc_settings.SETTINGS_KEY: {"enabled": True}}
        await s.commit()
    _cid, wid = await seed_clip_work(pid, vid, start=10.0, end=40.0)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(wid))
        wi.status, wi.submitted_at = "submitted", datetime.now(UTC)
        # 라벨러가 옛 방식으로 장르를 넣어도 영상 값이 비었으면 비어 있는 것으로 판정한다.
        wi.payload = {"caption": _CAPTION, "meta": {"genre": "보도"}}
        await s.commit()
    async with SessionLocal() as s:
        run = await runner.run(s, UUID(wid), "submit")
        await s.commit()
    found = [f for f in run.findings if f["check"] == "metadata_missing"]
    assert [f["message"] for f in found] == ["영상 메타에 장르가 없습니다"]
    assert found[0]["severity"] == "info", "라벨러의 반려·보류 사유가 아니다"

    async with SessionLocal() as s:
        (await s.get(Video, UUID(vid))).genre = "보도"
        await s.commit()
    async with SessionLocal() as s:
        run = await runner.run(s, UUID(wid), "submit")
        await s.commit()
    assert not [f for f in run.findings if f["check"] == "metadata_missing"]


async def test_completed_detail_shows_inherited_meta_and_reason_names(
    client: AsyncClient, admin_token: str,
):
    """완료 상세가 납품에 실리는 값(영상 메타)과 사유 이름을 싣는지 확인한다."""
    pid = await new_project(client, admin_token, "vmeta-done")
    rev_uid, _ = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    assert (await client.patch(f"/videos/{vid}", headers=_h(admin_token),
                               json={"genre": "보도"})).status_code == 200
    _cid, wid = await seed_clip_work(pid, vid)
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(wid))
        wi.status, wi.submitted_at = "approved", now
        wi.payload = {"caption": _CAPTION, "meta": {"genre": "정치"}}
        await s.commit()
    await _reviews(wid, rev_uid, [
        ("reject", ["caption_inaccurate"], "표기 확인", now - timedelta(hours=1)),
        ("approve", [], None, now),
    ])
    r = await client.get(f"/projects/{pid}/completed/{wid}", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_meta"]["genre"] == "보도", "라벨러 입력이 아니라 영상 값이다"
    assert "meta" not in body, "납품이 안 읽는 값을 화면에 싣지 않는다"
    reject = next(x for x in body["reviews"] if x["verdict"] == "reject")
    assert reject["reasons"] == [{"code": "caption_inaccurate", "label": "캡션 부정확"}]
    assert reject["comment"] == "표기 확인"


# 반려 사유는 코드로 저장한다


async def test_reject_stores_codes_and_sentences_round_trip(client: AsyncClient, admin_token: str):
    """반려 사유 코드와 문장이 저장된 그대로 돌아오는지 확인한다.

    코드는 중복을 빼고 순서를 지킨다. 사유 문장은 따로 저장한다.
    지적 문장은 보낸 그대로 돌아온다.
    """
    x = await _submitted(client, admin_token)
    flagged = [_SENTS[1]["text"], _SENTS[0]["text"]]
    r = await client.post(f"/work/{x['wid']}/review", headers=_h(x["rev"]), json={
        "decision": "reject",
        "reason_codes": ["caption_inaccurate", "deident_insufficient", "caption_inaccurate"],
        "reason": "고유명사 표기를 확인해주세요",
        "flagged_sentences": flagged,
    })
    assert r.status_code == 200, r.text
    rows = (await client.get(f"/work/{x['wid']}/reviews", headers=_h(x["annot"]))).json()
    last = rows[-1]
    assert last["reason_codes"] == ["caption_inaccurate", "deident_insufficient"]
    assert [y["label"] for y in last["reasons"]] == ["캡션 부정확", "비식별화 미흡"]
    assert last["comment"] == "고유명사 표기를 확인해주세요", "문장은 사유 칩과 섞이지 않는다"
    assert last["flagged_sentences"] == flagged, "지적 문장은 순서까지 그대로다"


async def test_unknown_or_disabled_reason_is_422_and_nothing_changes(
    client: AsyncClient, admin_token: str,
):
    """목록에 없거나 꺼진 코드를 422로 거부하는지 확인한다. 오류가 해당 필드를 가리키고 반려 횟수는 늘지 않는다."""
    x = await _submitted(client, admin_token)
    r = await client.post(f"/work/{x['wid']}/review", headers=_h(x["rev"]),
                          json={"decision": "reject", "reason_codes": ["no_such_reason"]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"] == ["body", "reason_codes", 0]
    # `메타 누락`은 기본값에서 꺼져 있다. 라벨러가 고칠 입력란이 없는 사유다.
    r = await client.post(f"/work/{x['wid']}/review", headers=_h(x["rev"]), json={
        "decision": "reject", "reason_codes": ["caption_inaccurate", "metadata_missing"],
    })
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d[0]["loc"] == ["body", "reason_codes", 1] and "메타 누락" in d[0]["msg"]
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(x["wid"]))
        assert wi.status == "submitted" and wi.rejection_count == 0
        assert not (await s.scalars(select(Review).where(
            Review.work_item_id == UUID(x["wid"])))).all()


async def test_reason_codes_only_ride_on_a_reject(client: AsyncClient, admin_token: str):
    """승인에 사유 코드가 붙으면 모순된 요청으로 보고 422를 돌려주는지 확인한다. 판정 규칙은 서버 한 곳에 있다."""
    x = await _submitted(client, admin_token)
    r = await client.post(f"/work/{x['wid']}/review", headers=_h(x["rev"]),
                          json={"decision": "approve", "reason_codes": ["caption_inaccurate"]})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "reason_codes"]


async def test_project_starts_with_the_default_reason_list(client: AsyncClient, admin_token: str):
    """사유 목록을 정하지 않은 프로젝트가 기본 목록을 쓰는지 확인한다.

    라벨러가 고칠 수 없는 `메타 누락`만 꺼져 있다.
    """
    pid = await new_project(client, admin_token, "rlist")
    body = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()
    listing = {x["code"]: x for x in body["reject_reasons"]}
    assert list(listing) == _BUILTIN_ORDER
    assert listing["metadata_missing"]["enabled"] is False
    assert all(x["enabled"] for c, x in listing.items() if c != "metadata_missing")
    assert not {"sentence_range", "speaker_name", "tag_inappropriate"} & set(listing), "입력 기능이 없는 사유"


def test_retired_reason_reads_by_name_and_is_not_selectable():
    """목록에서 뺀 사유가 지난 반려의 이력에서는 이름으로 읽히고 새 목록에는 다시 들어오지 않는지 확인한다."""
    retired = ["sentence_range", "speaker_name", "tag_inappropriate"]
    assert reject_reasons.refs(retired, list(reject_reasons.BUILTIN)) == [
        RejectReasonRef(code="sentence_range", label="문장 구간 오류"),
        RejectReasonRef(code="speaker_name", label="화자명 오류"),
        RejectReasonRef(code="tag_inappropriate", label="태그 부적절"),
    ]
    stored = {reject_reasons.SETTINGS_KEY: [{"code": c, "label": "옛 사유"} for c in retired]}
    assert not set(retired) & {r.code for r in reject_reasons.of_project(stored)}


async def test_disabled_reason_still_reads_by_name_in_history(
    client: AsyncClient, admin_token: str,
):
    """끈 사유가 새 반려에는 쓰이지 않고 이력에서는 이름으로 표시되는지 확인한다.

    사유 목록은 API로 바꿀 수 없는 고정 기본값이다. 저장된 목록을 읽는 동작은 남아 있으므로
    끈 목록을 DB에 직접 쓴다.
    """
    x = await _submitted(client, admin_token)
    r = await client.post(f"/work/{x['wid']}/review", headers=_h(x["rev"]),
                          json={"decision": "reject", "reason_codes": ["deident_insufficient"]})
    assert r.status_code == 200, r.text
    body = _listing((await client.get(f"/projects/{x['pid']}", headers=_h(admin_token))).json())
    for y in body:
        if y["code"] == "deident_insufficient":
            y["enabled"] = False
    await set_project_options(x["pid"], settings={reject_reasons.SETTINGS_KEY: body})
    listing = {y["code"]: y for y in (await client.get(
        f"/projects/{x['pid']}", headers=_h(admin_token))).json()["reject_reasons"]}
    assert listing["deident_insufficient"]["enabled"] is False
    rows = (await client.get(f"/work/{x['wid']}/reviews", headers=_h(x["annot"]))).json()
    assert rows[-1]["reasons"] == [{"code": "deident_insufficient", "label": "비식별화 미흡"}]


# 0081 백필


async def test_backfill_turns_known_chip_names_into_codes(client: AsyncClient, admin_token: str):
    """백필이 알려진 칩 이름을 코드로 바꾸고 모르는 조각을 사유 문장으로 남기는지 확인한다.

    어떤 조각도 지우지 않는다.
    """
    pid = await new_project(client, admin_token, "bf-rv")
    rev_uid, _ = await add_member(client, admin_token, pid, "reviewer")
    vid = await upload_video(client, admin_token, pid)
    _cid, wid = await seed_clip_work(pid, vid)
    now = datetime.now(UTC)
    ids = await _reviews(wid, rev_uid, [
        ("reject", [], "캡션 부정확 · 타임코드 오류 · 자막 위치가 틀렸습니다", now),
        ("reject", [], "재작업 요청", now),
        ("reject", [], "메타 누락", now),
        ("approve", [], "캡션 부정확", now),
        ("reject", ["speaker_name"], "캡션 부정확", now),
    ])
    m = _migration()
    async with SessionLocal() as s:
        await s.execute(text(m.BACKFILL_REVIEWS_SQL.format(scope="AND r.review_id = ANY(:ids)")),
                        {"ids": ids})
        await s.commit()
    async with SessionLocal() as s:
        got = {r.review_id: r for r in (await s.scalars(
            select(Review).where(Review.review_id.in_(ids)))).all()}
    a, b, c, d, e = (got[i] for i in ids)
    assert (a.reason_codes, a.comment) == (["caption_inaccurate", "sentence_range"],
                                           "자막 위치가 틀렸습니다")
    assert (b.reason_codes, b.comment) == ([], "재작업 요청"), "모르는 조각은 문장으로 남는다"
    assert (c.reason_codes, c.comment) == (["metadata_missing"], None)
    assert (d.reason_codes, d.comment) == ([], "캡션 부정확"), "반려가 아니면 손대지 않는다"
    assert (e.reason_codes, e.comment) == (["speaker_name"], "캡션 부정확"), "이미 코드가 있으면 그대로"


async def test_backfill_lifts_agreeing_labeler_meta_to_the_video(
    client: AsyncClient, admin_token: str,
):
    """옛 라벨러 입력을 값이 하나로 일치할 때만 비어 있는 영상 필드로 옮기는지 확인한다."""
    pid = await new_project(client, admin_token, "bf-vm")
    va = await upload_video(client, admin_token, pid, title="va")
    vb = await upload_video(client, admin_token, pid, title="vb")
    vc = await upload_video(client, admin_token, pid, title="vc")

    async def _item(video_id: str, meta: dict, start: float) -> None:
        """영상의 클립 작업 하나에 옛 라벨러 입력을 싣는다."""
        _c, w = await seed_clip_work(pid, video_id, start=start, end=start + 5)
        async with SessionLocal() as s:
            (await s.get(WorkItem, UUID(w))).payload = {"caption": _CAPTION, "meta": meta}
            await s.commit()

    same = {"genre": "보도", "broadcaster": "샘플 방송", "air_date": "2026-09-01"}
    await _item(va, same, 0.0)
    await _item(va, same, 10.0)
    await _item(vb, {"genre": "보도"}, 0.0)
    await _item(vb, {"genre": "시사"}, 10.0)
    await _item(vc, {"genre": "보도"}, 0.0)
    async with SessionLocal() as s:
        (await s.get(Video, UUID(vc))).genre = "스포츠"
        await s.commit()

    m = _migration()
    vids = [UUID(va), UUID(vb), UUID(vc)]
    async with SessionLocal() as s:
        await s.run_sync(lambda ss: m.backfill_video_meta(
            ss.connection(), scope="AND w.video_id = ANY(:vids)", params={"vids": vids}))
        await s.commit()
    async with SessionLocal() as s:
        a = await s.get(Video, vids[0])
        b = await s.get(Video, vids[1])
        c = await s.get(Video, vids[2])
    assert (a.genre, a.broadcaster) == ("보도", "샘플 방송")
    assert a.aired_at == datetime(2026, 9, 1, tzinfo=_KST), "방영일은 KST 자정이다"
    assert b.genre is None, "값이 갈리면 누가 맞는지 모른다 — 비워 둔다"
    assert c.genre == "스포츠", "영상에 이미 있는 값을 덮지 않는다"


def test_parse_air_date_reads_kst_days_only():
    """없는 날짜와 빈 값을 버리는지 확인한다. 형변환 실패로 마이그레이션 전체가 멈추지 않게 한다."""
    m = _migration()
    assert m.parse_air_date("2026-09-01") == datetime(2026, 9, 1, tzinfo=_KST)
    assert m.parse_air_date("2026-02-30") is None
    assert m.parse_air_date("") is None and m.parse_air_date(None) is None


def test_saved_list_keeps_order_and_regains_missing_builtin_codes():
    """저장된 목록의 순서를 지키고, 저장에 없는 기본 코드를 끝에 붙이는지 확인한다.

    기본 코드는 사라지지 않는다.
    """
    saved = {"reject_reasons": [
        {"code": "caption_inaccurate", "label": "캡션 오류", "enabled": True},
        {"code": "custom_1", "label": "키워드 부적절", "enabled": True},
        {"code": "BAD CODE", "label": "깨진 행"},
        "깨진 값",
    ]}
    codes = [r.code for r in reject_reasons.of_project(saved)]
    assert codes[:2] == ["caption_inaccurate", "custom_1"]
    assert set(codes) >= {r.code for r in reject_reasons.BUILTIN}
    assert "BAD CODE" not in codes
