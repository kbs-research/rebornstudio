"""캡션 품질검수가 제출·승인 요청 밖에서 도는지 보는 테스트.

제출·승인은 검사를 기다리지 않고 먼저 커밋한다.
검사는 커밋 뒤 워커가 `backlog.find_owed`로 대상을 찾아 `runner.run_owed`로 실행한다.
이 파일은 워커 태스크 대신 두 서비스 함수를 직접 부른다.
모델은 부르지 않는다. 느린 모델과 끊긴 요청은 `runner._run`을 바꿔 흉내 낸다.
"""
import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import (
    CaptionQcRun,
    DomainEvent,
    Export,
    Label,
    Project,
    Review,
    Video,
    WorkItem,
)
from api_gateway.services.caption_qc import backlog, runner
from api_gateway.services.caption_qc import settings as qc_settings
from api_gateway.services.exports import export_service
from httpx import AsyncClient
from sqlalchemy import select, update
from tests._helpers import (
    add_member,
    caption_payload,
    claim_review,
    new_project,
    seed_clip_work,
    upload_video,
)

#: 규칙 검사에 걸리지 않는 캡션. 메타·부가 라벨까지 채워 승인 검사가 통과로 끝난다.
_CLEAN = caption_payload(
    "앵커가 스튜디오에서 오늘의 주요 뉴스를 전합니다.",
    tags={"scene_type": ["스튜디오"], "sentiment": "중립"},
    description={"persons": ["앵커"], "location": "스튜디오", "actions": ["보도"]},
)
#: 개인정보(전화번호)가 있는 캡션. 승인 검사에서 보류된다.
_DIRTY = caption_payload("연락처 010-1234-5678 이 자막에 보입니다.")


@pytest_asyncio.fixture(loop_scope="session")
async def flow(client: AsyncClient, admin_token: str) -> dict:
    """품질검수를 켠 프로젝트와 라벨러·검수자, 메타를 채운 영상 하나를 만든다."""
    return await _new_flow(client, admin_token)


@pytest_asyncio.fixture(loop_scope="session")
async def other_flow(client: AsyncClient, admin_token: str) -> dict:
    """`flow`와 다른 프로젝트를 같은 모양으로 만든다. 작업 사이의 간섭을 볼 때 쓴다."""
    return await _new_flow(client, admin_token)


async def _new_flow(client: AsyncClient, admin_token: str) -> dict:
    """품질검수를 켠 프로젝트 하나와 그 라벨러·검수자·영상을 만든다."""
    pid = await new_project(client, admin_token, "qcasync")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.broadcaster, v.program_name, v.genre = "샘플 방송", "9시 뉴스 샘플", "보도"
        v.aired_at = datetime.now(UTC)
        v.duration_seconds = 1800.0
        p = await s.get(Project, UUID(pid))
        p.settings = {**(p.settings or {}), qc_settings.SETTINGS_KEY: {"enabled": True}}
        await s.commit()
    _, annot = await add_member(client, admin_token, pid, "annotator")
    rev_uid, rev = await add_member(client, admin_token, pid, "reviewer")
    return {"pid": pid, "vid": vid, "annot": annot, "rev": rev, "rev_uid": rev_uid, "seq": 0}


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _claimed(client: AsyncClient, flow: dict) -> str:
    """클립 작업 하나를 만들어 라벨러가 가져가게 한다. 구간은 호출마다 겹치지 않게 늘린다."""
    flow["seq"] += 1
    start = 40.0 * flow["seq"]
    await seed_clip_work(flow["pid"], flow["vid"], start=start, end=start + 30.0)
    r = await client.post(f"/projects/{flow['pid']}/work/next", headers=_h(flow["annot"]))
    assert r.status_code == 200, r.text
    return r.json()["work_item_id"]


async def _submit(client: AsyncClient, flow: dict, payload: dict) -> tuple[str, float]:
    """작업 하나를 가져와 제출하고 (작업 id, 응답까지 걸린 초)를 돌려준다."""
    wid = await _claimed(client, flow)
    t0 = time.monotonic()
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(flow["annot"]), json={"payload": payload},
    )
    assert r.status_code == 200, r.text
    return wid, time.monotonic() - t0


async def _approve(client: AsyncClient, flow: dict, wid: str) -> float:
    """검수자가 가져가 승인하고 응답까지 걸린 초를 돌려준다."""
    await claim_review(client, flow["rev"], flow["pid"], wid)
    t0 = time.monotonic()
    r = await client.post(
        f"/work/{wid}/review", headers=_h(flow["rev"]), json={"decision": "approve"},
    )
    assert r.status_code == 200, r.text
    return time.monotonic() - t0


async def _drain(wid: str) -> list[str]:
    """이 작업에 걸린 검사를 워커처럼 찾아 실행하고 결과 목록을 돌려준다.

    다른 테스트가 남긴 대상은 실행하지 않는다.
    """
    async with SessionLocal() as s:
        owed = [x for x in await backlog.find_owed(s, limit=500) if str(x[0]) == wid]
    out = []
    for w, trigger in owed:
        async with SessionLocal() as s:
            out.append(await runner.run_owed(s, w, trigger))
    return out


async def _runs(wid: str) -> list[CaptionQcRun]:
    """이 작업의 실행 기록을 시각 순으로 읽는다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(CaptionQcRun).where(CaptionQcRun.work_item_id == UUID(wid))
            .order_by(CaptionQcRun.created_at)
        )).all())


async def _item(wid: str) -> WorkItem:
    """작업 행을 새 세션에서 읽는다. 커밋된 것만 보인다."""
    async with SessionLocal() as s:
        return await s.get(WorkItem, UUID(wid))


def _slow_engine(monkeypatch, seconds: float) -> list:
    """검사 본체를 느린 가짜로 바꾸고 호출 기록을 돌려준다. 모델 응답 대기를 흉내 낸다."""
    calls: list = []

    async def _slow(db, work_item_id, trigger):
        """기다린 뒤 아무것도 기록하지 않는다."""
        calls.append((work_item_id, trigger))
        await asyncio.sleep(seconds)

    monkeypatch.setattr(runner, "_run", _slow)
    return calls


# 제출·승인은 모델을 기다리지 않는다


async def test_submit_commits_before_any_qc_and_returns_at_once(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """모델이 느려도 제출이 바로 커밋되고 응답하는지 확인한다. 요청 안에서는 검사가 돌지 않는다.

    요청 안에서 검사하면 응답 시간이 모델 대기만큼 늘고, 그동안 api 커넥션과 행 잠금을 점유한다.
    """
    calls = _slow_engine(monkeypatch, 3.0)
    wid, took = await _submit(client, flow, _CLEAN)
    assert calls == []
    assert took < 2.0
    async with SessionLocal() as s:
        assert await s.scalar(select(Label.label_id).where(Label.work_item_id == UUID(wid)))
    assert (await _item(wid)).status == "submitted"


async def test_approve_commits_before_any_qc_and_returns_at_once(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """모델이 느려도 승인이 바로 커밋되고 응답하는지 확인한다. 게이트는 검사 전이라 `pending`이다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    calls = _slow_engine(monkeypatch, 3.0)
    took = await _approve(client, flow, wid)
    assert calls == []
    assert took < 2.0
    item = await _item(wid)
    assert (item.status, item.qc_status) == ("approved", "pending")


async def test_interrupted_engine_does_not_undo_the_submission(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """검사 도중 프로세스가 끊겨도 제출이 남고 끊긴 검사를 다음 투입이 다시 실행하는지 확인한다.

    배포로 api가 재시작되면 진행 중인 코루틴이 취소된다.
    검사가 커밋보다 앞에 있으면 제출 트랜잭션까지 되감겨 라벨러가 다시 제출해야 한다.
    """
    async def _cancelled(db, work_item_id, trigger):
        """재시작으로 취소된 검사를 흉내 낸다."""
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "_run", _cancelled)
    wid, _ = await _submit(client, flow, _CLEAN)
    assert (await _item(wid)).status == "submitted"
    monkeypatch.undo()
    assert await _drain(wid) == ["ran"]
    assert [r.trigger for r in await _runs(wid)] == ["submit"]


# 승인 검사가 게이트를 정한다


async def test_worker_approve_run_moves_the_gate(client: AsyncClient, flow: dict):
    """승인 뒤 워커가 검사하면 판정대로 게이트가 정해지는지(걸리면 `held`, 깨끗하면 `passed`) 확인한다."""
    dirty, _ = await _submit(client, flow, _DIRTY)
    clean, _ = await _submit(client, flow, _CLEAN)
    for wid in (dirty, clean):
        await _drain(wid)                 # 제출 검사
        await _approve(client, flow, wid)
        assert (await _item(wid)).qc_status == "pending"
        assert await _drain(wid) == ["ran"]
    assert (await _item(dirty)).qc_status == "held"
    assert (await _item(clean)).qc_status == "passed"
    assert [r.trigger for r in await _runs(dirty)] == ["submit", "approve"]


# 회수


async def test_owed_items_are_found_once_and_recorded_once(client: AsyncClient, flow: dict):
    """기록이 없는 제출은 대상이 되고 기록이 생기면 빠지며, 두 번 실행해도 기록이 하나인지 확인한다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    async with SessionLocal() as s:
        owed = await backlog.find_owed(s, limit=500)
    assert (UUID(wid), "submit") in owed
    async with SessionLocal() as s:
        assert await runner.run_owed(s, UUID(wid), "submit") == "ran"
    async with SessionLocal() as s:
        assert await runner.run_owed(s, UUID(wid), "submit") == "skipped"
    async with SessionLocal() as s:
        assert (UUID(wid), "submit") not in await backlog.find_owed(s, limit=500)
    assert len(await _runs(wid)) == 1


async def test_concurrent_duplicates_leave_one_record(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """같은 건이 두 워커에서 동시에 돌아도 기록과 게이트 갱신이 하나만 남는지 확인한다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await _approve(client, flow, wid)
    real = runner._ask_model

    async def _slow_model(*a, **k):
        """모델 대기를 흉내 내 두 실행이 겹치게 한다."""
        await asyncio.sleep(0.5)
        return await real(*a, **k)

    monkeypatch.setattr(runner, "_ask_model", _slow_model)

    async def _one():
        """새 세션으로 승인 검사를 한 번 실행한다."""
        async with SessionLocal() as s:
            return await runner.run_owed(s, UUID(wid), "approve")

    got = await asyncio.gather(_one(), _one())
    assert sorted(got) == ["duplicate", "ran"]
    assert [r.trigger for r in await _runs(wid)].count("approve") == 1


async def test_disabled_project_is_not_a_target(client: AsyncClient, flow: dict):
    """품질검수를 끈 프로젝트의 제출이 대상이 아니고 화면 상태도 `none`인지 확인한다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(flow["pid"]))
        p.settings = {**p.settings, qc_settings.SETTINGS_KEY: {"enabled": False}}
        await s.commit()
    try:
        async with SessionLocal() as s:
            assert (UUID(wid), "submit") not in await backlog.find_owed(s, limit=500)
            assert not await backlog.is_owed(s, UUID(wid), "submit")
        body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["annot"]))).json()
        assert body == {"state": "none", "run": None}
    finally:
        async with SessionLocal() as s:
            p = await s.get(Project, UUID(flow["pid"]))
            p.settings = {**p.settings, qc_settings.SETTINGS_KEY: {"enabled": True}}
            await s.commit()


async def test_old_approvals_are_not_swept(client: AsyncClient, flow: dict):
    """조회 기간보다 오래된 승인이 대상이 아닌지 확인한다. 품질검수를 켜는 순간 이전 승인분을 모두 검사하지 않는다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await _approve(client, flow, wid)
    async with SessionLocal() as s:
        await s.execute(
            update(Review).where(Review.work_item_id == UUID(wid))
            .values(reviewed_at=datetime.now(UTC) - backlog.LOOKBACK - timedelta(hours=1))
        )
        await s.commit()
    async with SessionLocal() as s:
        assert (UUID(wid), "approve") not in await backlog.find_owed(s, limit=500)


async def test_failures_are_retried_up_to_the_cap(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """기록을 남기지 못한 실행을 감사 이벤트로 세고 상한에 이르면 더 보내지 않는지 확인한다."""
    wid, _ = await _submit(client, flow, _CLEAN)

    async def _boom(db, work_item_id, trigger):
        """DB 오류처럼 기록 전에 실패하는 검사를 흉내 낸다."""
        raise RuntimeError("기록 실패")

    monkeypatch.setattr(runner, "_run", _boom)
    for _ in range(backlog.MAX_FAILURES):
        assert await _drain(wid) == ["failed"]
    assert await _drain(wid) == []
    async with SessionLocal() as s:
        n = await s.scalar(select(DomainEvent).where(
            DomainEvent.target_id == UUID(wid), DomainEvent.event_type == backlog.FAILED_EVENT,
        ).with_only_columns(DomainEvent.event_id).limit(1))
        assert n is not None
    body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["annot"]))).json()
    assert body == {"state": "failed", "run": None}


def _flaky_model(monkeypatch, fail_times: int) -> list:
    """앞의 `fail_times`번은 호출 실패, 그 뒤는 지적 없는 모델 결과를 내는 가짜 모델을 건다."""
    from api_gateway.services.caption_qc import adapters

    calls: list = []

    async def _ask(db, item, conf, sentences):
        """호출 순서에 따라 실패하거나 결과를 낸다."""
        calls.append(item.work_item_id)
        if len(calls) <= fail_times:
            return None, "model_failed"
        return adapters.QcResult(findings=[], model="fake-model", prompt_hash="h"), None

    monkeypatch.setattr(runner, "_ask_model", _ask)
    return calls


async def _failures(wid: str) -> int:
    """이 작업의 실패 감사 이벤트 수를 센다."""
    async with SessionLocal() as s:
        return len((await s.scalars(select(DomainEvent.event_id).where(
            DomainEvent.target_id == UUID(wid), DomainEvent.event_type == backlog.FAILED_EVENT,
        ))).all())


async def test_model_failure_is_retried_before_the_verdict(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """모델 호출이 실패하면 규칙 판정으로 확정하지 않고 다시 시도하며, 성공하면 모델 판정이 남는지 확인한다.

    한 번의 일시 장애로 확정하면 모델이 보지 않은 캡션이 통과한다.
    재시도 동안 게이트는 `pending`이고 내보내기는 그 프로젝트의 시작을 미룬다.
    """
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await _approve(client, flow, wid)
    calls = _flaky_model(monkeypatch, 2)
    for _ in range(2):
        assert await _drain(wid) == ["retry"]
        item = await _item(wid)
        assert item.qc_status == "pending"
        async with SessionLocal() as s:
            assert item.project_id in await backlog.projects_awaiting_gate(s)
    assert await _drain(wid) == ["ran"]
    approve = [r for r in await _runs(wid) if r.trigger == "approve"]
    assert len(approve) == 1 and approve[0].model == "fake-model"
    assert (await _item(wid)).qc_status == "passed"
    assert len(calls) == 3 and await _failures(wid) == 2


async def test_third_model_failure_settles_on_rules(
    client: AsyncClient, flow: dict, monkeypatch,
):
    """세 번째 시도에서도 모델 호출이 실패하면 `model_failed` 단계와 규칙 판정으로 확정하는지 확인한다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    _flaky_model(monkeypatch, 99)
    results = [await _drain(wid) for _ in range(backlog.MAX_FAILURES)]
    assert results == [["retry"]] * (backlog.MAX_FAILURES - 1) + [["ran"]]
    run = (await _runs(wid))[-1]
    assert (run.trigger, run.adapter_key, run.model) == ("submit", "rules", None)
    assert any(st.get("cause") == "model_failed" for st in run.stages)
    assert await _drain(wid) == []
    body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["annot"]))).json()
    assert body["state"] == "recorded"


def test_lease_hands_out_each_item_once():
    """한 번 보낸 건을 표시 시간 동안 다시 보내지 않는지 확인한다. Redis가 없는 환경은 모두 보낸다."""
    from uuid import uuid4

    from api_gateway import redis_infra

    pair = (uuid4(), "submit")
    first = backlog.lease([pair])
    assert first == [pair]
    second = backlog.lease([pair])
    if redis_infra.get_redis() is None:
        assert second == [pair]
    else:
        assert second == []


# 다른 작업의 제출·승인과 섞이지 않는다

#: 대상 목록을 끝까지 읽는 상한. 같은 DB에 있는 다른 테스트의 대상에 밀려 잘리지 않게 넉넉히 둔다.
_ALL = 5000


def _boom_engine(monkeypatch) -> None:
    """기록 전에 실패하는 검사를 건다. DB 오류처럼 실행 기록을 남기지 못한 실패를 흉내 낸다."""
    async def _boom(db, work_item_id, trigger):
        """기록하기 전에 예외를 낸다."""
        raise RuntimeError("기록 실패")

    monkeypatch.setattr(runner, "_run", _boom)


async def _fail_to_cap(monkeypatch, wid: str) -> None:
    """이 작업의 검사를 실패 상한까지 실패시키고 더 보내지 않는 것을 확인한다."""
    _boom_engine(monkeypatch)
    for _ in range(backlog.MAX_FAILURES):
        assert await _drain(wid) == ["failed"]
    assert await _drain(wid) == []
    monkeypatch.undo()


async def test_finished_submit_stays_finished_after_another_projects_submission(
    client: AsyncClient, flow: dict, other_flow: dict,
):
    """기록된 제출 검사가 다른 프로젝트의 제출 뒤에도 끝난 채로 남고 새 제출만 대상인지 확인한다.

    기준 시각이 작업과 묶이지 않으면 DB 전체의 마지막 제출 시각이 기준이 된다.
    그러면 다른 프로젝트의 제출 한 번에 끝난 검사가 다시 `검사 중`으로 돌아가고 워커가 같은 건을 계속 다시 돌린다.
    """
    x, _ = await _submit(client, flow, _CLEAN)
    assert await _drain(x) == ["ran"]
    y, _ = await _submit(client, other_flow, _CLEAN)
    async with SessionLocal() as s:
        owed = await backlog.find_owed(s, limit=_ALL)
        assert (UUID(x), "submit") not in owed
        assert (UUID(y), "submit") in owed
        assert not await backlog.is_owed(s, UUID(x), "submit")
        assert await backlog.is_owed(s, UUID(y), "submit")
        assert await backlog.state_of(s, await s.get(WorkItem, UUID(x))) is None
        assert await backlog.state_of(s, await s.get(WorkItem, UUID(y))) == "running"
    body = (await client.get(f"/work/{x}/qc", headers=_h(flow["rev"]))).json()
    assert body["state"] == "recorded"
    assert await _drain(x) == []
    assert len(await _runs(x)) == 1


async def test_finished_approve_stays_finished_after_another_projects_approval(
    client: AsyncClient, flow: dict, other_flow: dict,
):
    """기록된 승인 검사가 다른 프로젝트의 승인 뒤에도 끝난 채로 남고 새 승인만 대상인지 확인한다."""
    x, _ = await _submit(client, flow, _CLEAN)
    await _drain(x)
    await _approve(client, flow, x)
    assert await _drain(x) == ["ran"]
    y, _ = await _submit(client, other_flow, _CLEAN)
    await _drain(y)
    await _approve(client, other_flow, y)
    async with SessionLocal() as s:
        owed = await backlog.find_owed(s, limit=_ALL)
        assert (UUID(x), "approve") not in owed
        assert (UUID(y), "approve") in owed
        assert not await backlog.is_owed(s, UUID(x), "approve")
        assert await backlog.running_ids(s, [UUID(x), UUID(y)]) == {UUID(y)}
        assert await backlog.state_of(s, await s.get(WorkItem, UUID(x))) is None
    assert await _drain(x) == []
    assert [r.trigger for r in await _runs(x)] == ["submit", "approve"]


async def test_submit_failure_cap_holds_after_another_projects_submission(
    client: AsyncClient, flow: dict, other_flow: dict, monkeypatch,
):
    """실패 상한에 이른 제출 검사를 다른 프로젝트의 제출 뒤에도 다시 보내지 않는지 확인한다.

    실패 횟수를 DB 전체의 마지막 제출 뒤로만 세면 다른 제출이 들어올 때마다 0으로 돌아가 상한이 지켜지지 않는다.
    """
    x, _ = await _submit(client, flow, _CLEAN)
    await _fail_to_cap(monkeypatch, x)
    await _submit(client, other_flow, _CLEAN)
    async with SessionLocal() as s:
        assert await backlog.failures_of(s, UUID(x), "submit") == backlog.MAX_FAILURES
        assert not await backlog.is_owed(s, UUID(x), "submit")
        assert (UUID(x), "submit") not in await backlog.find_owed(s, limit=_ALL)
        assert await backlog.state_of(s, await s.get(WorkItem, UUID(x))) == "failed"
    assert await _drain(x) == []


async def test_approve_failure_cap_holds_after_another_projects_approval(
    client: AsyncClient, flow: dict, other_flow: dict, monkeypatch,
):
    """실패 상한에 이른 승인 검사를 다른 프로젝트의 승인 뒤에도 다시 보내지 않고 내보내기를 막지 않는지 확인한다."""
    x, _ = await _submit(client, flow, _CLEAN)
    await _drain(x)
    await _approve(client, flow, x)
    await _fail_to_cap(monkeypatch, x)
    y, _ = await _submit(client, other_flow, _CLEAN)
    await _drain(y)
    await _approve(client, other_flow, y)
    async with SessionLocal() as s:
        assert await backlog.failures_of(s, UUID(x), "approve") == backlog.MAX_FAILURES
        assert not await backlog.is_owed(s, UUID(x), "approve")
        assert await backlog.running_ids(s, [UUID(x)]) == set()
        assert UUID(flow["pid"]) not in await backlog.projects_awaiting_gate(s)
        assert await backlog.state_of(s, await s.get(WorkItem, UUID(x))) == "failed"
    assert await _drain(x) == []


async def test_quick_resubmission_is_handed_out_again(client: AsyncClient, flow: dict):
    """끝난 검사의 투입 표시를 지워 반려 뒤 바로 다시 제출한 버전을 다음 투입 주기에 보내는지 확인한다.

    표시를 남겨 두면 새 제출본이 앞선 투입의 표시 시간(`LEASE_SECONDS`)이 끝날 때까지 기다린다.
    """
    from api_gateway import redis_infra

    if redis_infra.get_redis() is None:
        pytest.skip("투입 표시는 Redis에만 있다")
    wid, _ = await _submit(client, flow, _CLEAN)
    pair = (UUID(wid), "submit")
    try:
        assert backlog.lease([pair]) == [pair]
        async with SessionLocal() as s:
            assert await runner.run_owed(s, UUID(wid), "submit") == "ran"
        await claim_review(client, flow["rev"], flow["pid"], wid)
        r = await client.post(
            f"/work/{wid}/review", headers=_h(flow["rev"]),
            json={"decision": "reject", "reason": "다시"},
        )
        assert r.status_code == 200, r.text
        r = await client.post(
            f"/work/{wid}/submit", headers=_h(flow["annot"]), json={"payload": _DIRTY},
        )
        assert r.status_code == 200, r.text
        async with SessionLocal() as s:
            assert pair in await backlog.find_owed(s, limit=_ALL)
        assert backlog.lease([pair]) == [pair]
        assert backlog.lease([pair]) == []
    finally:
        backlog.release(*pair)


# 판정 이후의 검사


async def test_stale_submit_is_skipped_after_the_decision(client: AsyncClient, flow: dict):
    """제출 검사가 돌기 전에 검수자가 승인하면 제출 검사는 건너뛰고 승인 검사만 도는지 확인한다.

    제출 검사는 검수자에게 보여 줄 근거라서 판정이 끝나면 쓰지 않는다.
    판정 뒤의 `payload`는 검수자가 고친 버전일 수 있다. 그 값을 검사해 제출본 검사로 기록하면 기록이 틀린다.
    """
    wid, _ = await _submit(client, flow, _CLEAN)
    await _approve(client, flow, wid)
    async with SessionLocal() as s:
        assert await runner.run_owed(s, UUID(wid), "submit") == "skipped"
    assert await _drain(wid) == ["ran"]
    assert [r.trigger for r in await _runs(wid)] == ["approve"]


async def test_resubmission_is_owed_again(client: AsyncClient, flow: dict):
    """반려 뒤 다시 제출하면 새 제출본이 다시 대상이 되는지 확인한다. 이전 제출 기록으로 넘어가지 않는다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await claim_review(client, flow["rev"], flow["pid"], wid)
    r = await client.post(
        f"/work/{wid}/review", headers=_h(flow["rev"]),
        json={"decision": "reject", "reason": "다시"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/work/{wid}/submit", headers=_h(flow["annot"]), json={"payload": _DIRTY},
    )
    assert r.status_code == 200, r.text
    body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["rev"]))).json()
    assert body == {"state": "running", "run": None}     # 이전 제출본 결과를 보여 주지 않는다
    assert await _drain(wid) == ["ran"]
    assert [r.trigger for r in await _runs(wid)] == ["submit", "submit"]


# 화면 상태


async def test_work_qc_says_running_then_recorded(client: AsyncClient, flow: dict):
    """기록 전에는 `running`, 기록 뒤에는 `recorded`와 그 실행을 주는지 확인한다. 화면이 다시 계산하지 않는다."""
    wid, _ = await _submit(client, flow, _DIRTY)
    body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["annot"]))).json()
    assert body == {"state": "running", "run": None}
    await _drain(wid)
    body = (await client.get(f"/work/{wid}/qc", headers=_h(flow["annot"]))).json()
    assert body["state"] == "recorded"
    assert body["run"]["trigger"] == "submit"
    assert body["run"]["verdict"] == "reject_suggested"


async def test_completed_row_says_running_until_the_approve_run(
    client: AsyncClient, admin_token: str, flow: dict,
):
    """승인 직후 완료 목록이 그 건을 `검사 중`으로 싣는지 확인한다. 검사 기능이 없던 승인분과 구분된다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await _approve(client, flow, wid)

    async def _row():
        """완료 목록에서 이 작업의 행을 찾는다."""
        r = await client.get(
            f"/projects/{flow['pid']}/completed", headers=_h(admin_token),
            params={"limit": 200},
        )
        assert r.status_code == 200, r.text
        return next(x for x in r.json()["items"] if x["work_item_id"] == wid)

    row = await _row()
    assert (row["qc_running"], row["qc_checked_by"]) == (True, None)
    await _drain(wid)
    row = await _row()
    assert (row["qc_running"], row["qc_checked_by"]) == (False, "rules")


# 납품 게이트 대기 시간


async def _export(flow: dict, *, age: timedelta = timedelta(0)) -> UUID:
    """내보내기 요청 행을 만든다. `age`만큼 오래된 요청으로 둔다."""
    async with SessionLocal() as s:
        e = Export(project_id=UUID(flow["pid"]), created_by=UUID(flow["rev_uid"]),
                   status="pending", created_at=datetime.now(UTC) - age)
        s.add(e)
        await s.commit()
        return e.export_id


async def _claim_one(seen_other: set[UUID]) -> UUID | None:
    """내보내기 처리의 잡기 단계만 한 번 돈다. 다른 테스트의 대기 행은 건너뛴다."""
    async with SessionLocal() as s:
        waiting = await backlog.projects_awaiting_gate(s)
        return await export_service._claim_next(s, seen_other, waiting)


async def test_export_waits_for_the_approve_run_then_starts(client: AsyncClient, flow: dict):
    """승인 검사를 기다리는 승인분이 있으면 그 프로젝트의 내보내기 시작을 미루는지 확인한다.

    미루지 않으면 방금 승인한 건이 검사 전 `pending`으로 패키지에 실린다.
    검사가 끝나면 바로 시작한다. 행을 빼지 않으므로 게이트(`!= held`)와 보고서의 분모는 그대로다.
    """
    wid, _ = await _submit(client, flow, _DIRTY)
    await _drain(wid)
    await _approve(client, flow, wid)
    eid = await _export(flow)
    async with SessionLocal() as s:
        others = set((await s.scalars(
            select(Export.export_id).where(Export.status == "pending", Export.export_id != eid)
        )).all())
    assert await _claim_one(others) is None
    await _drain(wid)
    assert await _claim_one(others) == eid
    assert (await _item(wid)).qc_status == "held"


async def test_export_does_not_wait_forever(client: AsyncClient, flow: dict):
    """기다리는 시간이 지나면 검사 전이어도 내보내기를 시작하는지 확인한다. 검사하지 않은 것은 납품을 막지 않는다."""
    wid, _ = await _submit(client, flow, _CLEAN)
    await _drain(wid)
    await _approve(client, flow, wid)
    eid = await _export(flow, age=export_service._QC_WAIT + timedelta(seconds=5))
    async with SessionLocal() as s:
        others = set((await s.scalars(
            select(Export.export_id).where(Export.status == "pending", Export.export_id != eid)
        )).all())
    assert await _claim_one(others) == eid
    assert (await _item(wid)).qc_status == "pending"


async def test_export_row_says_it_waits_for_the_qc_gate(
    client: AsyncClient, admin_token: str, flow: dict,
):
    """승인 검사 때문에 시작을 미루는 내보내기가 그 사실과 미루기가 끝나는 시각을 싣는지 확인한다.

    서버는 최대 `_QC_WAIT`(10분)까지 시작을 미룬다.
    화면이 자체 기준(요청 뒤 5분)으로 처리 지연을 판단하면 서버가 기다리는 중에도 처리 서버가 멈췄다고 안내하고 추가 버튼을 다시 연다.
    그래서 화면이 서버 값으로 대기 사유를 그리게 한다. 검사가 끝나면 그 값이 사라져 화면은 평소 기준으로 돌아간다.
    """
    wid, _ = await _submit(client, flow, _DIRTY)
    await _drain(wid)
    await _approve(client, flow, wid)
    r = await client.post(f"/projects/{flow['pid']}/exports", headers=_h(admin_token))
    assert r.status_code == 201, r.text
    created = r.json()
    eid = created["export_id"]
    assert created["qc_wait_until"] is not None

    async def _row() -> dict:
        """이력에서 이 내보내기 행을 찾는다."""
        r = await client.get(f"/projects/{flow['pid']}/exports", headers=_h(admin_token))
        assert r.status_code == 200, r.text
        return next(x for x in r.json() if x["export_id"] == eid)

    row = await _row()
    assert row["status"] == "pending"
    until = datetime.fromisoformat(row["qc_wait_until"])
    made = datetime.fromisoformat(row["created_at"])
    assert until - made == export_service._QC_WAIT

    await _drain(wid)                                  # 승인 검사가 끝났다
    assert (await _row())["qc_wait_until"] is None


async def test_export_row_without_qc_has_no_wait(client: AsyncClient, admin_token: str):
    """품질검수를 끈 프로젝트의 내보내기는 미루지 않으므로 대기 시각이 없는지 확인한다."""
    pid = await new_project(client, admin_token, "qcwaitoff")
    r = await client.post(f"/projects/{pid}/exports", headers=_h(admin_token))
    assert r.status_code == 201, r.text
    assert r.json()["qc_wait_until"] is None


@pytest.mark.parametrize("trigger", ["submit", "approve"])
async def test_unknown_item_is_not_owed(trigger: str):
    """없는 작업이 대상이 아니고, 대기열에 남은 메시지가 지워진 작업을 가리켜도 조용히 끝나는지 확인한다."""
    from uuid import uuid4

    async with SessionLocal() as s:
        assert await runner.run_owed(s, uuid4(), trigger) == "skipped"
