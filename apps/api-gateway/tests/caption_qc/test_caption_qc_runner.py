"""캡션 QC 트리거 배선 테스트. 제출·승인 두 지점에서 QC가 실제로 도는지 확인한다.

규칙·어댑터 자체는 각자의 테스트가 검사한다. 이 파일은 배선과 게이트만 확인한다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import CaptionQcRun, Project, Video, WorkItem
from api_gateway.services.caption_qc import runner
from api_gateway.services.caption_qc import settings as qc_settings
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, seed_clip_work, upload_video

# 실제 제출 payload 모양. 문자열 `brief`는 문장 배열에서 만든다(FE `joinSentences`).
# 둘 다 넣어야 한다. 납품 관문(`check_row`)은 문자열로 길이를 잰다.
_CLEAN_SENTS = [
    {"id": "s1", "text": "앵커가 스튜디오에서 오늘의 주요 뉴스를 전합니다."},
    {"id": "s2", "text": "화면 하단에 자막이 표시되어 있습니다."},
]
_DIRTY_SENTS = [{"id": "s1", "text": "연락처 010-1234-5678 이 자막에 보입니다."}]


#: 이전 payload에 남은 부가 라벨 키. 검사는 이 값을 읽지 않고, 캡션을 고쳐도 그대로 남는다.
#:
#: 메타데이터 검사는 라벨러 제출본이 아니라 영상을 본다.
#: 아래 `seeded_project`가 영상에 네 필드를 채운다. 비어 있으면 `info` 지적이 난다(판정은 그대로 `pass`).
#: 비운 상태는 아래 `test_info_findings_do_not_hold_delivery`가 따로 확인한다.
_LEGACY_LABELS = {
    "tags": {"scene_type": ["스튜디오"], "sentiment": "중립"},
    "description": {"persons": ["앵커"], "location": "스튜디오", "actions": ["보도"]},
}


def _payload(sents: list[dict]) -> dict:
    """문장 배열로 캡션을 채우고 이전 부가 라벨 키를 남긴 제출 payload를 만든다."""
    return {
        "caption": {"brief": " ".join(x["text"] for x in sents), "brief_sentences": sents},
        **_LEGACY_LABELS,
    }


_CLEAN = _payload(_CLEAN_SENTS)
_DIRTY = _payload(_DIRTY_SENTS)


@pytest.fixture
async def seeded_project(client: AsyncClient, admin_token: str):
    """프로젝트와 영상을 하나씩 만든다. 러너가 읽는 프로젝트 설정과 영상 메타를 갖춘다."""
    pid = await new_project(client, admin_token, "qcrun")
    vid = await upload_video(client, admin_token, pid)
    # 영상 메타 네 필드는 운영자가 영상 상세에서 채우는 값이고 클립이 물려받는다.
    # 메타 검사가 이 값을 보므로 네 필드를 모두 채워야 지적 0건 테스트가 성립한다.
    # 길이는 납품 관문이 쓴다.
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.broadcaster, v.program_name, v.genre = "샘플 방송", "9시 뉴스 샘플", "보도"
        v.aired_at = datetime.now(UTC)
        v.duration_seconds = 1800.0
        await s.commit()
    return UUID(pid), UUID(vid)


async def _enable_qc(project_id: UUID, **over) -> None:
    """프로젝트에 QC를 켠다. 기본값이 꺼짐이라 켜야 검사가 돈다."""
    async with SessionLocal() as s:
        p = await s.get(Project, project_id)
        p.settings = {**(p.settings or {}),
                      qc_settings.SETTINGS_KEY: {"enabled": True, **over}}
        await s.commit()


async def _mk_item(
    project_id: UUID, video_id: UUID, payload: dict, *, start: float = 10.0, end: float = 40.0
) -> UUID:
    """클립 단위 work_item을 새로 만들고 payload를 실어 제출 상태로 만든다.

    영상 등록이 만드는 영상 단위 work_item은 쓰지 않는다.
    그 행은 `uq_work_item_video_only`가 영상당 하나(clip_id가 NULL인 행)로 제한한다.
    """
    # 클립 단위로 만든다. 캡션 작업의 실제 단위이고, 납품 관문이 클립 구간에서 길이를 잰다.
    # 영상 단위 work_item은 길이가 0이라서 납품 관문에서 항상 막힌다.
    # 한 테스트가 두 건을 만들면 구간을 나눠야 한다.
    # `idx_clips_video_time_range`가 (영상, 시작, 끝) 유일성을 강제한다.
    _cid, wid = await seed_clip_work(str(project_id), str(video_id), start=start, end=end)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(wid))
        wi.status = "submitted"
        wi.payload = payload
        wi.submitted_at = datetime.now(UTC)
        await s.commit()
        return wi.work_item_id


async def _runs_of(wid: UUID) -> list[CaptionQcRun]:
    """이 작업의 QC 실행 행을 모두 읽는다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(CaptionQcRun).where(CaptionQcRun.work_item_id == wid)
        )).all())


async def test_disabled_project_runs_nothing(seeded_project):
    """QC를 켜지 않은 프로젝트에서는 아무것도 하지 않는지 확인한다. 기본값이 꺼짐이다."""
    pid, vid = seeded_project
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        assert await runner.run(s, wid, "submit") is None
    assert await _runs_of(wid) == []


async def test_submit_records_but_does_not_gate(seeded_project):
    """제출 시점 결과가 근거로만 남고 게이트를 바꾸지 않는지 확인한다.

    사람이 판정하기 전에 기계가 막지 않는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)      # 개인정보가 있어 지적이 난다
    async with SessionLocal() as s:
        await runner.run(s, wid, "submit")
        await s.commit()

    runs = await _runs_of(wid)
    assert len(runs) == 1 and runs[0].trigger == "submit"
    assert runs[0].verdict == "reject_suggested"          # 개인정보는 error다
    async with SessionLocal() as s:
        # 제출 시점에는 게이트 상태가 바뀌지 않는다.
        assert (await s.get(WorkItem, wid)).qc_status == "pending"


async def test_approve_gates_on_findings(seeded_project):
    """승인 직후에 게이트가 작동해 지적이 있으면 `held`가 되는지 확인한다."""
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)
    async with SessionLocal() as s:
        await runner.run(s, wid, "approve")
        await s.commit()
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "held"


async def test_clean_caption_passes_automatically(seeded_project):
    """지적이 없으면 사람이 처리하지 않아도 자동으로 통과하는지 확인한다."""
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        await runner.run(s, wid, "approve")
        await s.commit()
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "passed"
    runs = await _runs_of(wid)
    # 모델을 부르지 않았으면 그 사실이 남는다. 하지 않은 것을 한 것처럼 기록하지 않는다.
    assert runs[0].adapter_key == "rules" and runs[0].model is None


async def test_both_triggers_accumulate(seeded_project):
    """제출·승인 결과가 모두 쌓이는지 확인한다.

    제출본은 문제없었는데 고친 뒤 걸린 경우를 확인할 수 있어야 한다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        await runner.run(s, wid, "submit")
        await s.commit()
    # 검수자가 고쳤다고 가정하고 payload를 바꾼다. 수정 후 승인이 하는 일이다.
    async with SessionLocal() as s:
        (await s.get(WorkItem, wid)).payload = _DIRTY
        await s.commit()
    async with SessionLocal() as s:
        await runner.run(s, wid, "approve")
        await s.commit()

    runs = {r.trigger: r for r in await _runs_of(wid)}
    assert set(runs) == {"submit", "approve"}
    assert runs["submit"].verdict == "pass"                 # 제출본은 문제없었고
    assert runs["approve"].verdict == "reject_suggested"    # 고친 뒤 걸렸다


async def test_engine_failure_never_breaks_the_pipeline(seeded_project, monkeypatch):
    """QC가 실패해도 제출·승인을 되돌리지 않는지 확인한다.

    검수 엔진 고장이 사람이 한 일을 취소하면 안 된다.
    QC가 돌지 못하면 `pending`으로 남는다.
    게이트 조건이 `!= held`라서 납품도 막히지 않는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _CLEAN)

    def _boom(*a, **k):
        """엔진 고장을 흉내 낸다."""
        raise RuntimeError("엔진 고장")

    monkeypatch.setattr(runner.rules, "check_caption", _boom)
    async with SessionLocal() as s:
        assert await runner.run(s, wid, "approve") is None   # 예외가 밖으로 나오지 않는다
        await s.commit()
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "pending"   # 보류(`held`)로 바뀌지 않는다


# 보류함: 게이트가 숨긴 작업을 보여 준다


async def test_held_item_disappears_from_completed_but_is_counted(
    seeded_project, client: AsyncClient, admin_token: str
):
    """보류된 건이 완료 목록에서 빠지지만 보류 수로는 표시되는지 확인한다.

    이 수가 없으면 운영자는 완료 건수가 어제보다 줄어든 이유를 찾을 수 없다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        wi.status = "approved"
        await s.commit()
        await runner.run(s, wid, "approve")
        await s.commit()

    r = await client.get(f"/projects/{pid}/completed",
                         headers={"Authorization": f"Bearer {admin_token}"})
    body = r.json()
    assert body["total"] == 0            # 목록에서는 빠지고
    assert body["qc_held_total"] == 1    # 보류 수로는 표시한다


async def test_held_list_carries_the_reason(
    seeded_project, client: AsyncClient, admin_token: str
):
    """보류함이 보류 근거를 함께 싣는지 확인한다. 목록만 주면 이유를 알려고 다시 조회해야 한다."""
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        wi.status = "approved"
        await s.commit()
        await runner.run(s, wid, "approve")
        await s.commit()

    r = await client.get(f"/projects/{pid}/qc/held",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["work_item_id"] == str(wid)
    assert row["verdict"] == "reject_suggested"
    checks = {f["check"] for f in row["findings"]}
    assert "pii" in checks                      # 걸린 이유가 실린다
    assert row["adapter_key"] == "rules"        # 판정한 주체도 실린다
    # 검사한 항목도 싣는다. 보류 상세가 실행 상세와 같은 체크리스트를 그린다.
    stages = {st["key"]: st["status"] for st in row["stages"]}
    assert stages["pii"] == "flagged"
    assert "caption" in stages


# 단계 기록: 통과와 미실행을 구분하는지 확인한다


def _stage(run, key):
    """실행의 단계 기록에서 키가 같은 단계를 찾는다."""
    return next((s for s in (run.stages or []) if s["key"] == key), None)


async def test_skipped_stages_are_recorded_as_skipped_not_passed(seeded_project):
    """하지 않은 검사를 통과로 기록하지 않는지 확인한다. 통과로 기록하면 체크리스트가 잘못 표시된다.

    비속어 사전이 비면 검사할 것이 없다.
    클립 길이 기준이 없으면 지적할 기준이 없다.
    두 경우 모두 지적은 0건이며 통과 대신 미실행으로 기록한다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)          # 사전·용어집을 채우지 않은 기본 설정
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    assert run.stages, "단계 기록이 비었다"
    prof = _stage(run, "profanity")
    assert prof["status"] == "skipped" and prof["reason"], "안 한 이유가 없으면 통과와 구분이 안 된다"
    assert _stage(run, "clip_length")["status"] == "skipped"
    # 실제로 돈 것은 통과로 남는다.
    assert _stage(run, "caption")["status"] == "passed"
    assert _stage(run, "pii")["status"] == "passed"


async def test_model_stages_are_skipped_when_rules_already_flagged(seeded_project):
    """규칙에 걸리면 모델을 부르지 않고 네 모델 단계를 통과가 아니라 미실행으로 기록하는지 확인한다."""
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)      # 개인정보가 들어 있다
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    assert _stage(run, "pii")["status"] == "flagged"
    for k in ("spelling", "tone", "inappropriate", "glossary"):
        st = _stage(run, k)
        assert st["status"] == "skipped", f"{k}가 통과로 적혔다"
        assert "규칙" in st["reason"], st["reason"]


async def test_stage_keys_cover_every_model_check():
    """모델 단계 목록이 어댑터의 허용 집합과 같은지 확인한다. 다르면 체크리스트에 빠진 항목이 생긴다."""
    from api_gateway.services.caption_qc import adapters
    from api_gateway.services.caption_qc import runner as r

    assert set(r._MODEL_STAGES) == set(adapters._ALLOWED_CHECKS)


# 납품 화면의 판단 근거(비가역 액션의 근거는 버튼과 같은 화면에 둔다)


async def _approve(wid, pid, vid):
    """승인 상태로 만들고 승인 트리거를 실행한다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        wi.status = "approved"
        await s.commit()
        await runner.run(s, wid, "approve")
        await s.commit()


async def test_completed_row_says_what_checked_it(seeded_project, client: AsyncClient, admin_token):
    """규칙에 걸린 승인분이 완료 목록에서 빠지고 보류 수에 잡히는지 확인한다.

    완료 목록은 행마다 검사한 주체를 모델, 규칙만, 검사 안 함으로 구분한다.
    셋을 합치면 모델 검사 없이 통과한 건에 QC를 켜기 전 승인분까지 들어가 경고 수를 믿을 수 없다.
    `qc_checked_by` 값은 아래 두 테스트가 확인한다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    dirty = await _mk_item(pid, vid, _DIRTY, start=10.0, end=40.0)   # 규칙에 걸린다
    await _approve(dirty, pid, vid)

    r = await client.get(f"/projects/{pid}/completed?limit=100",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    row = next((x for x in body["items"] if x["work_item_id"] == str(dirty)), None)
    # 규칙에 걸린 건은 보류라서 완료 목록에 없다. 이 동작이 곧 게이트다.
    assert row is None
    # 대신 보류 수로 표시한다.
    assert body["qc_held_total"] >= 1


async def test_rules_only_pass_is_counted_at_the_button(seeded_project, client: AsyncClient,
                                                        admin_token):
    """모델을 부르지 못해 규칙만으로 통과한 건을 전체 기준으로 세는지 확인한다.

    이 수는 납품 버튼 옆에 뜬다.
    페이지에서 세면 100건만 실린 목록으로 전체 수를 계산하게 되어 틀린 값이 된다.
    분모를 밝히는 규칙을 따른다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)          # 연결이 없어 모델을 부르지 못하므로 adapter_key는 'rules'다
    clean = await _mk_item(pid, vid, _CLEAN, start=50.0, end=80.0)
    await _approve(clean, pid, vid)

    r = await client.get(f"/projects/{pid}/completed?limit=100",
                         headers={"Authorization": f"Bearer {admin_token}"})
    body = r.json()
    row = next(x for x in body["items"] if x["work_item_id"] == str(clean))
    assert row["qc_checked_by"] == "rules", row["qc_checked_by"]
    assert body["qc_unreviewed_total"] >= 1


async def test_items_approved_before_qc_are_not_counted(seeded_project, client: AsyncClient,
                                                        admin_token):
    """QC를 켜기 전에 승인된 건을 경고에서 빼는지 확인한다. 이 건은 검사 대상이 아니었던 건이다.

    켜기 전 승인분까지 세면 QC를 켠 직후 그 승인분 전체가 경고로 뜬다.
    그러면 모델 검사 없이 통과한 건을 경고 수로 가려낼 수 없다.
    """
    pid, vid = seeded_project        # QC를 켜지 않은 상태로 승인만 시킨다
    old = await _mk_item(pid, vid, _CLEAN, start=90.0, end=120.0)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, old)
        wi.status = "approved"
        await s.commit()

    r = await client.get(f"/projects/{pid}/completed?limit=100",
                         headers={"Authorization": f"Bearer {admin_token}"})
    body = r.json()
    row = next(x for x in body["items"] if x["work_item_id"] == str(old))
    assert row["qc_checked_by"] is None          # 검사 안 함
    assert body["qc_unreviewed_total"] == 0      # 경고로 세지 않는다


async def test_info_findings_do_not_hold_delivery(seeded_project, admin_token, client):
    """영상 메타가 비면 지적은 나지만 보류되지 않는지 확인한다.

    이 테스트는 선택 항목이 사실상 필수가 되지 않게 보장한다.
    `info`는 검수자에게 보이지만 판정에 들어가지 않는다.
    `warn`으로 내면 메타를 채우지 않은 깨끗한 제출이 모두 보류함으로 간다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    # 캡션만 있다.
    bare = {"caption": {"brief": " ".join(x["text"] for x in _CLEAN_SENTS),
                        "brief_sentences": _CLEAN_SENTS}}
    # 메타는 영상에서 비운다. 라벨러 제출본의 `meta`는 보지 않는다.
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.genre = None
        await s.commit()
    wid = await _mk_item(pid, vid, bare)
    async with SessionLocal() as s:
        await runner.run(s, wid, "submit")
        await s.commit()

    (run,) = await _runs_of(wid)
    assert run.verdict == "pass", "info 지적은 판정에 안 낀다"
    kinds = {f["check"] for f in run.findings}
    assert "metadata_missing" in kinds, "검수자에게는 보여야 한다"
    assert "labels_missing" not in kinds, "세부 묘사 검사는 실행하지 않는다"
    assert all(f["severity"] == "info" for f in run.findings)

    # 납품 게이트는 승인 때만 작동한다(제출은 `pending`). 그래서 승인까지 진행해 확인한다.
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "pending", "제출은 게이트가 아니다"
        await runner.run(s, wid, "approve")
        await s.commit()
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "passed", "info 는 납품을 막지 않는다"


# 비식별화를 건너뛴 사유: 영상 여정과 같은 문장


async def _deident_state(project_id: UUID, video_id: UUID, *, on: bool, status: str) -> None:
    """프로젝트 스위치와 영상의 비식별화 상태를 정한다. 러너와 영상 여정이 둘 다 읽는 값이다."""
    async with SessionLocal() as s:
        (await s.get(Project, project_id)).deident_enabled = on
        (await s.get(Video, video_id)).deident_status = status
        await s.commit()


@pytest.mark.parametrize(("on", "status", "cause"), [
    (False, "skipped", "deident_off"),
    (True, "skipped", "deident_before_on"),
    (True, "running", "deident_not_run"),
    (True, "failed", "deident_failed"),
])
async def test_deident_skip_says_why(seeded_project, on: bool, status: str, cause: str):
    """비식별화를 확인하지 않았으면 그 사유를 경우별 원인 코드로 남기는지 확인한다.

    "아직"이 맞는 경우는 지금 비식별화가 돌고 있는 영상뿐이다.
    네 경우 모두 "비식별화가 아직 실행되지 않았습니다"로 쓰면 비식별화를 쓰지 않는 프로젝트에서도 오지 않을 실행을 기다리게 한다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    await _deident_state(pid, vid, on=on, status=status)
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()
    st = next(x for x in run.stages if x["key"] == "deident")
    assert (st["status"], st["cause"]) == ("skipped", cause), st
    if cause != "deident_not_run":
        assert "아직" not in st["reason"], st


@pytest.mark.parametrize("on", [False, True])
async def test_deident_skip_matches_video_journey(
    seeded_project, client: AsyncClient, admin_token: str, on: bool
):
    """품질 검사의 사유와 영상 여정의 사유가 같은 문장인지 확인한다.

    같은 영상의 같은 사실을 두 화면이 다르게 표시하면 어느 쪽이 맞는지 알 수 없다.
    그래서 판정을 한 곳(`services.deident_gap`)에 두고 두 화면이 함께 부른다.
    `skipped` 하나가 사용 안 함과 켜기 전 처리를 함께 나타내므로 둘을 구분해 쓴다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    await _deident_state(pid, vid, on=on, status="skipped")
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()
    qc = next(x for x in run.stages if x["key"] == "deident")
    r = await client.get(f"/videos/{vid}/prep-timeline",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    journey = next(e for e in r.json()["entries"] if e["stage"] == "deident")
    # 켜기 전에 처리된 영상은 여정에서 실행이 필요한 단계(`needed`)라 사유가 `note`에 있다.
    said = journey["note"] if journey["status"] == "needed" else journey["skipped_reason"]
    assert qc["reason"] == said, (qc, journey)


class _BrokenModel:
    """호출하면 예외를 내는 모델. 일시 오류로 한 번 다시 불렀는데도 실패한 상황을 흉내 낸다."""

    key = "llm"

    async def review(self, req):
        """상류 오류를 낸다."""
        raise RuntimeError("upstream 503")


async def test_model_failure_is_recorded_not_lost(seeded_project, monkeypatch):
    """모델 호출이 실패하면 실행이 규칙만으로 남고 그 사실이 기록되는지 확인한다.

    연결을 정하지 않은 경우(설정)와는 다른 사유·코드로 남는다.
    어댑터가 던진 예외가 `run()`까지 올라가면 실행 행 자체가 없어져 실패가 기록에 남지 않는다.
    """
    from api_gateway.models import Connection

    pid, vid = seeded_project
    async with SessionLocal() as s:
        org_id = (await s.get(Project, pid)).org_id
        name = f"qc-broken-{uuid4().hex[:8]}"   # 조직 안 이름은 유일하므로 재실행해도 겹치지 않게 난수를 붙인다.
        s.add(Connection(org_id=org_id, name=name, provider="s3", config={"model": "m"}))
        await s.commit()
    await _enable_qc(pid, adapter_key="llm", connection_name=name)
    monkeypatch.setattr(runner.adapters, "get", lambda _key: _BrokenModel())

    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    assert run is not None, "모델 고장에 실행 기록이 사라졌다"
    assert run.adapter_key == "rules"
    for k in ("spelling", "tone", "inappropriate", "glossary"):
        st = _stage(run, k)
        assert st["status"] == "skipped" and st["cause"] == "model_failed", st
        assert st["reason"] == "AI 검사를 실행하지 못했습니다"


async def test_unconfigured_model_is_a_setting_not_a_failure(seeded_project):
    """연결을 정하지 않은 것을 고장이 아니라 설정으로 기록하는지 확인한다.

    사유는 설정 상수 문장이고 코드는 고장과 다르다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()
    st = _stage(run, "spelling")
    assert st["cause"] == "model_unconfigured"
    assert st["reason"] == qc_settings.MODEL_UNCONFIGURED


# 선택 항목 알림이 검사를 막지 않는다
#
# 체크리스트는 `info` 지적을 `noted`로 기록한다. `flagged`로 기록하면 경고와 같은 `!`로 보인다.
# 모델 호출 여부는 `info`를 세지 않는다. 세면 영상 메타가 비었다는 알림 하나로
# 맞춤법·문체·부적절 표현·용어집 검사가 모두 규칙에 걸린 것으로 생략된다.
# 판정(`verdict_of`)도 `info`를 세지 않으므로 그 건은 `pass`다.

_EMPTY_LABELS_CLEAN = {
    "caption": {"brief": " ".join(x["text"] for x in _CLEAN_SENTS), "brief_sentences": _CLEAN_SENTS},
}


async def _blank_genre(video_id: UUID) -> None:
    """영상 메타의 장르를 비워 영상 메타 단계가 `info` 지적만 내게 한다."""
    async with SessionLocal() as s:
        (await s.get(Video, video_id)).genre = None
        await s.commit()


async def test_info_only_stage_is_noted_not_flagged(seeded_project):
    """선택 항목만 빈 단계가 경고(`flagged`)가 아니라 `noted`로 기록되는지 확인한다."""
    pid, vid = seeded_project
    await _enable_qc(pid)
    await _blank_genre(vid)
    wid = await _mk_item(pid, vid, _EMPTY_LABELS_CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    assert run.verdict == "pass", "선택 항목이 비었다고 판정이 바뀌면 안 된다"
    assert _stage(run, "metadata")["status"] == "noted", _stage(run, "metadata")
    # 캡션에는 문제가 없다. 이 건에서 걸린 것은 없다.
    assert _stage(run, "caption")["status"] == "passed"


async def test_info_only_does_not_skip_the_model(seeded_project):
    """알림만 있으면 모델 단계를 규칙에 걸린 것으로 생략하지 않는지 확인한다.

    테스트 환경에는 모델 연결이 없으므로 모델 단계는 여전히 `skipped`다.
    다만 사유는 `rules_flagged`(규칙에 걸림)가 아니라 `model_unconfigured`(연결 없음)여야 한다.
    `rules_flagged`가 기록되면 연결이 있어도 모델을 부르지 않는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    await _blank_genre(vid)
    wid = await _mk_item(pid, vid, _EMPTY_LABELS_CLEAN)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    for k in ("spelling", "tone", "inappropriate", "glossary"):
        st = _stage(run, k)
        assert st.get("cause") != "rules_flagged", f"{k}: 선택 항목 알림 때문에 모델을 안 불렀다 — {st}"


async def test_real_rule_hit_still_skips_the_model(seeded_project):
    """실제 규칙 위반(개인정보)이 있으면 여전히 모델을 부르지 않는지 확인하는 대조군이다.

    이 테스트가 없으면 항상 모델을 부르도록 바꿔도 위 두 테스트가 통과한다.
    호출에는 비용이 들므로 어차피 보류될 건에는 호출하지 않는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)
    async with SessionLocal() as s:
        run = await runner.run(s, wid, "submit")
        await s.commit()

    assert _stage(run, "pii")["status"] == "flagged"
    for k in ("spelling", "tone", "inappropriate", "glossary"):
        assert _stage(run, k).get("cause") == "rules_flagged", k


# 다시 검사는 `recheck`로 남는다
#
# 다시 검사가 `approve`로 남으면 승인이 없던 시각에 `승인 시` 실행이 있었던 것처럼 기록된다.


async def test_recheck_is_recorded_as_recheck_and_moves_the_gate(
    seeded_project, client: AsyncClient, admin_token: str
):
    """다시 검사가 `recheck`로 남고 승인과 같이 게이트를 바꾸는지 확인한다.

    보류된 건의 캡션이 고쳐졌다고 가정하고 다시 검사하면 보류가 풀린다.
    승인 실행은 그때의 판정 그대로 남는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY)
    await _approve(wid, pid, vid)                      # 개인정보가 있어 보류된다
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        assert wi.qc_status == "held"
        wi.payload = _CLEAN                            # 캡션을 고쳤다고 가정한다
        await s.commit()

    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["qc_status"] == "passed"

    runs = {x.trigger: x for x in await _runs_of(wid)}
    assert sorted(runs) == ["approve", "recheck"]
    assert runs["approve"].verdict == "reject_suggested", "승인 실행은 그때 판정 그대로다"
    assert runs["recheck"].verdict == "pass"


async def test_recheck_keeps_the_hold_when_the_model_fails(
    seeded_project, client: AsyncClient, admin_token: str, monkeypatch,
):
    """다시 검사에서 모델 호출이 실패하면 규칙만으로 보류를 풀지 않는지 확인한다.

    규칙만 통과한 승인본을 통과로 확정하면 모델이 보지 못한 캡션이 사람 판정 없이 납품 대상이 된다.
    실행 기록은 남기지 않고, 실패한 시도는 워커와 같은 `caption_qc.failed`(trigger=recheck)로 센다.
    응답은 화면이 "AI 검사를 실행하지 못했습니다"라고 표시할 수 있게 그 사실을 싣는다.
    """
    from api_gateway.models import DomainEvent

    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY, start=170.0, end=200.0)
    await _approve(wid, pid, vid)                      # 개인정보가 있어 보류된다
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        assert wi.qc_status == "held"
        wi.payload = _CLEAN                            # 사전·캡션을 고쳐 규칙은 통과한다고 가정한다
        await s.commit()

    async def _down(db, item, conf, sentences):
        """연결은 정했지만 호출에 실패한 모델."""
        return None, "model_failed"

    monkeypatch.setattr(runner, "_ask_model", _down)
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qc_status"] == "held" and body["model_failed"] is True
    assert body["verdict"] is None
    assert [x.trigger for x in await _runs_of(wid)] == ["approve"], "실패한 다시 검사는 기록하지 않는다"
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "held"
        failed = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "caption_qc.failed", DomainEvent.target_id == wid,
        ))).all()
        assert [e.payload["trigger"] for e in failed] == ["recheck"]
        rechecked = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "caption_qc.rechecked", DomainEvent.target_id == wid,
        ))).one()
        assert rechecked.payload["model_failed"] is True

    # 모델이 복구되면 같은 버튼으로 풀린다. 실패가 보류를 영구로 만들지 않는다.
    monkeypatch.undo()
    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=h)
    assert r.json()["qc_status"] == "passed" and r.json()["model_failed"] is False


async def test_held_row_says_the_last_recheck_failed(
    seeded_project, client: AsyncClient, admin_token: str, monkeypatch,
):
    """다시 검사가 모델 실패로 끝나면 보류 행이 그 사실과 시각을 싣는지 확인한다.

    실패한 다시 검사는 실행 기록을 남기지 않으므로 행의 판정·지적은 이전 실행의 값이다.
    실패를 토스트로만 알리면 바뀐 기준으로는 더 이상 걸리지 않을 지적이 지금 판정처럼 남는다.
    그 뒤에 새 판정이 기록되면 실패 표시는 사라진다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY, start=210.0, end=240.0)
    await _approve(wid, pid, vid)
    h = {"Authorization": f"Bearer {admin_token}"}

    def _row():
        """보류 목록에서 이 작업의 행을 찾는다."""
        return next(x for x in held["items"] if x["work_item_id"] == str(wid))

    held = (await client.get(f"/projects/{pid}/qc/held", headers=h)).json()
    assert _row()["recheck_failed_at"] is None, "다시 검사한 적이 없는데 실패로 표시했다"

    async with SessionLocal() as s:
        (await s.get(WorkItem, wid)).payload = _CLEAN   # 규칙은 통과해 모델 단계까지 간다
        await s.commit()

    async def _down(db, item, conf, sentences):
        """연결은 정했지만 호출에 실패한 모델."""
        return None, "model_failed"

    monkeypatch.setattr(runner, "_ask_model", _down)
    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=h)
    assert r.json()["model_failed"] is True
    held = (await client.get(f"/projects/{pid}/qc/held", headers=h)).json()
    row = _row()
    assert row["recheck_failed_at"] is not None, "실패한 다시 검사를 행에 싣지 않았다"
    assert row["recheck_failed_at"] > row["checked_at"], "실패 시각이 표시한 판정보다 앞선다"

    # 다시 검사가 새 판정을 남기면 실패 표시는 사라진다. 규칙이 걸리면 모델을 부르지 않는다.
    async with SessionLocal() as s:
        (await s.get(WorkItem, wid)).payload = _DIRTY
        await s.commit()
    r = await client.post(f"/projects/{pid}/qc/{wid}/recheck", headers=h)
    assert r.json()["qc_status"] == "held" and r.json()["model_failed"] is False
    held = (await client.get(f"/projects/{pid}/qc/held", headers=h)).json()
    assert _row()["recheck_failed_at"] is None, "새 판정 뒤에도 옛 실패를 표시했다"


# 캡션 고쳐 다시 승인(보류를 푸는 첫째 방법)
#
# 개인정보처럼 캡션 자체가 틀린 보류는 다시 검사해도 같은 지적에 걸리고, 사유를 달아 풀면 고치지 않은 캡션이 납품 대상이 된다.
# 고칠 방법이 없으면 그 클립은 영구히 납품에서 빠진다.


async def _held_pii(pid: UUID, vid: UUID, *, start: float) -> UUID:
    """개인정보 캡션을 승인해 보류로 만든다. 고친 캡션이 제출 기준을 넘도록 기준을 낮춘다."""
    async with SessionLocal() as s:
        (await s.get(Project, pid)).caption_min_tokens = 5
        await s.commit()
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _DIRTY, start=start, end=start + 30.0)
    await _approve(wid, pid, vid)
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "held"
    return wid


async def test_revise_fixes_the_caption_and_reruns_the_approve_check(
    seeded_project, client: AsyncClient, admin_token: str,
):
    """관리자가 보류된 승인본의 캡션을 고치면 새 개정·수정 후 승인으로 남고 승인 검사가 다시 도는지 확인한다.

    게이트는 `pending`으로 돌아가고, 워커가 다른 승인과 같은 경로(`backlog`)로 검사해 통과하면 풀린다.
    당시 지적과 고친 문장은 감사 기록에 남는다.
    """
    from api_gateway.models import DomainEvent, Label, Review
    from api_gateway.services.caption_qc import backlog

    pid, vid = seeded_project
    wid = await _held_pii(pid, vid, start=210.0)
    h = {"Authorization": f"Bearer {admin_token}"}

    held = (await client.get(f"/projects/{pid}/qc/held", headers=h)).json()["items"]
    row = next(x for x in held if x["work_item_id"] == str(wid))
    assert row["sentences"] == [{"id": "s1", "text": _DIRTY_SENTS[0]["text"]}]

    r = await client.post(f"/projects/{pid}/qc/{wid}/revise", headers=h, json={
        "sentences": [{"id": "s1", "text": "  연락처를 가린 화면이 자막과 함께 보입니다.  "}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["qc_status"] == "pending"

    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        assert wi.status == "approved" and wi.qc_status == "pending"
        sents = wi.payload["caption"]["brief_sentences"]
        assert sents == [{"id": "s1", "text": "연락처를 가린 화면이 자막과 함께 보입니다."}]
        assert wi.payload["caption"]["brief"] == "연락처를 가린 화면이 자막과 함께 보입니다."
        assert wi.payload["tags"] == _DIRTY["tags"], "캡션 밖 라벨은 그대로다"
        label = (await s.scalars(select(Label).where(Label.work_item_id == wid))).one()
        review = (await s.scalars(select(Review).where(Review.work_item_id == wid))).one()
        assert review.verdict == "modify_and_approve" and review.label_id == label.label_id
        assert label.submitted_by == review.reviewer_id, "검수 중 개정으로 분류된다"
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "caption_qc.hold_revised", DomainEvent.target_id == wid,
        ))).one()
        assert ev.payload["changed_sentences"] == ["s1"]
        assert ev.payload["verdict_at_revise"] == "reject_suggested"
        assert ev.payload["findings_at_revise"], "고치기 전 지적을 남긴다"
        assert await backlog.is_owed(s, wid, "approve"), "승인 검사를 다시 기다린다"

    async with SessionLocal() as s:
        assert await runner.run_owed(s, wid, "approve") == "ran"
    async with SessionLocal() as s:
        assert (await s.get(WorkItem, wid)).qc_status == "passed"
    assert sorted(x.trigger for x in await _runs_of(wid)) == ["approve", "approve"]


async def test_revise_refuses_bad_edits(
    seeded_project, client: AsyncClient, admin_token: str,
):
    """없는 문장·바뀐 것 없음·제출 기준 미달·관리자 아님·보류 아님을 거절하고, 거절하면 아무것도 바뀌지 않는지 확인한다."""
    from tests._helpers import add_member

    pid, vid = seeded_project
    wid = await _held_pii(pid, vid, start=250.0)
    h = {"Authorization": f"Bearer {admin_token}"}
    url = f"/projects/{pid}/qc/{wid}/revise"

    async def _post(sents, headers=h):
        """고친 문장을 보낸다."""
        return await client.post(url, headers=headers, json={"sentences": sents})

    r = await _post([{"id": "s9", "text": "없는 문장을 고칩니다."}])
    assert (r.status_code, r.json()["detail"]) == (422, "sentence_not_found")
    r = await _post([{"id": "s1", "text": _DIRTY_SENTS[0]["text"]}])
    assert (r.status_code, r.json()["detail"]) == (422, "caption_unchanged")
    r = await _post([{"id": "s1", "text": "네."}])
    assert (r.status_code, r.json()["detail"]) == (422, "caption_too_short")
    assert (await _post([{"id": "s1", "text": "   "}])).status_code == 422
    assert (await _post([])).status_code == 422
    _uid, op = await add_member(client, admin_token, str(pid), "reviewer")
    r = await _post([{"id": "s1", "text": "연락처를 가린 화면입니다."}],
                    headers={"Authorization": f"Bearer {op}"})
    assert (r.status_code, r.json()["detail"]) == (403, "not_project_admin")
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        assert wi.qc_status == "held" and wi.payload == _DIRTY

    async with SessionLocal() as s:
        (await s.get(WorkItem, wid)).qc_status = "passed"
        await s.commit()
    r = await _post([{"id": "s1", "text": "연락처를 가린 화면입니다."}])
    assert (r.status_code, r.json()["detail"]) == (409, "qc_not_held")


async def test_rules_only_reads_the_latest_recheck(
    seeded_project, client: AsyncClient, admin_token: str
):
    """납품 쪽의 `규칙만 검사` 판정이 승인 뒤의 다시 검사 실행을 최신으로 보는지 확인한다.

    `approve`만 보면 다시 검사를 규칙만으로 통과한 건이 이전 승인 실행(모델)으로 읽혀 납품 버튼 옆 경고에서 빠진다.
    모델 연결 없이 모델 실행을 재현하려고 실행 두 행을 직접 넣는다.
    """
    pid, vid = seeded_project
    await _enable_qc(pid)
    wid = await _mk_item(pid, vid, _CLEAN, start=130.0, end=160.0)
    t0 = datetime.now(UTC)
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, wid)
        wi.status, wi.qc_status = "approved", "passed"
        s.add(CaptionQcRun(work_item_id=wid, trigger="approve", verdict="pass", findings=[],
                           adapter_key="bedrock", model="m", created_at=t0))
        s.add(CaptionQcRun(work_item_id=wid, trigger="recheck", verdict="pass", findings=[],
                           adapter_key="rules", model=None,
                           created_at=t0.replace(microsecond=0) + timedelta(seconds=5)))
        await s.commit()

    r = await client.get(f"/projects/{pid}/completed?limit=100",
                         headers={"Authorization": f"Bearer {admin_token}"})
    body = r.json()
    row = next(x for x in body["items"] if x["work_item_id"] == str(wid))
    assert row["qc_checked_by"] == "rules", row["qc_checked_by"]
    assert body["qc_unreviewed_total"] >= 1
