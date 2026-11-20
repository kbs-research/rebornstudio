"""장면 분할 잡 프로토콜의 생산(build_jobs), 소비(DB 없는 러너), 판정(complete) 테스트.

`transcode`와 같은 구조라서 확인 항목도 같다.
워커가 페이로드만으로 끝까지 도는지, 이전 시도의 보고를 버리는지,
일시 오류가 시도 횟수를 쓰지 않는지 확인한다.
이 단계에만 있는 항목은 결과가 클립이 된다는 점이다.
"""
import uuid
from datetime import UTC, datetime

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, Project, Video, WorkItem
from api_gateway.services.media import scene_split as service
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy import update as sa_update
from tests._helpers import new_project, upload_video

# 워커가 돌려보내는 형태다. `reborn_transcode.scene_split.run_job`의 반환값과 같다.
_SEGMENTS = [{"start": 0.0, "end": 38.4}, {"start": 38.4, "end": 75.0},
             {"start": 75.0, "end": 120.0}]
_BASIS = {"raw_count": 412, "merged_count": 3, "rule": "샷 경계 · 최대 40초 · 무음 구간 우선"}


@pytest.fixture(autouse=True)
async def _isolate_scene_jobs():
    """앞선 테스트가 남긴 잡과 영상을 정리한다. 모든 테스트가 in-flight 상한을 공유한다.

    잡 행만 지우면 앞 테스트가 넣은 영상이 여전히 대상 조건을 만족한다.
    그러면 다음 `build_jobs`가 그 영상부터 고르고, 상한(4)이 차면 이번 테스트의 영상이 실리지 않는다.
    이 경우 단독 실행은 통과하고 파일 전체 실행만 실패한다.
    """
    async with SessionLocal() as s:
        await s.execute(delete(AnalysisJob).where(
            AnalysisJob.adapter_key == service.ADAPTER_KEY))
        # 앞 테스트의 영상을 소프트 삭제해 대상 조건에서 뺀다.
        ours = select(Project.project_id).where(
            Project.settings["analysis_preset"].astext == "ncp-shots")
        await s.execute(
            sa_update(Video).where(Video.project_id.in_(ours))
            .values(deleted_at=datetime.now(UTC))
        )
        await s.commit()
    yield


async def _seed(client: AsyncClient, admin_token: str, *, duration: float = 120.0) -> uuid.UUID:
    """자체 장면 분할 스택 프로젝트에 준비가 끝난 영상 하나를 만든다.

    프리셋은 운영자가 쓰는 API 경로로 고른다. 그래야 실제 경로에서 이 파이프라인이
    도는지 확인할 수 있다.
    """
    pid = await new_project(client, admin_token, f"split-{uuid.uuid4().hex[:6]}",
                            analysis_preset="ncp-shots")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        v.duration_seconds = duration
        v.transcode_status = "skipped"
        v.deident_status = "skipped"
        v.publish_status = "done"
        await s.commit()
    return vid


async def _build_one(vid: uuid.UUID) -> dict | None:
    """장면 분할 잡을 만들고 이 영상의 잡 페이로드를 돌려준다. 없으면 None이다."""
    async with SessionLocal() as s:
        jobs = await service.build_jobs(s)
    return next((j for j in jobs if j["video_id"] == str(vid)), None)


async def _job(vid: uuid.UUID) -> AnalysisJob:
    """이 영상의 장면 분할 AnalysisJob 행을 돌려준다."""
    async with SessionLocal() as s:
        return (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id == vid,
            AnalysisJob.adapter_key == service.ADAPTER_KEY))).one()


async def _complete(vid: uuid.UUID, token: str, **kw) -> str:
    """장면 분할 완료를 보고하고 판정 결과를 돌려준다."""
    async with SessionLocal() as s:
        return await service.complete(s, video_id=vid, token=token, **kw)


# ── 생산 ──────────────────────────────────────────────────────────────────────


async def test_payload_carries_everything_the_worker_needs(client, admin_token):
    """워커는 DB에 연결하지 않으므로 페이로드에 처리에 필요한 값이 모두 있는지 확인한다."""
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    assert job is not None
    assert job["input_url"].startswith("http")
    assert job["duration_seconds"] == 120.0
    assert job["token"], "펜싱 토큰이 없으면 낡은 보고를 가릴 수 없다"


async def test_mi_stack_projects_are_not_touched(client, admin_token):
    """MI 스택 프로젝트의 영상은 장면 분할 대상으로 고르지 않는다. MI가 분석하면서 구간을 자른다.

    고르면 한 영상이 두 번 잘리고 라벨러가 그 차이를 모른 채 작업한다.
    """
    pid = await new_project(client, admin_token, f"mi-{uuid.uuid4().hex[:6]}")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.duration_seconds, v.transcode_status = 120.0, "skipped"
        v.deident_status, v.publish_status = "skipped", "done"
        await s.commit()
    assert await _build_one(vid) is None


async def test_unfinished_prep_is_not_cut(client, admin_token):
    """준비가 끝나지 않은 영상은 자르지 않는다.

    분석본이 최종 위치에 없으면 타임코드 기준이 달라진다.
    """
    vid = await _seed(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.publish_status = "running"
        await s.commit()
    assert await _build_one(vid) is None


async def test_already_cut_videos_are_not_cut_again(client, admin_token):
    """클립이 있는 영상은 다시 자르지 않는다. 다시 자르면 같은 시간대에 클립이 두 개씩 생긴다."""
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    await _complete(vid, job["token"], ok=True, segments=_SEGMENTS, basis=_BASIS)
    async with SessionLocal() as s:
        await s.execute(delete(AnalysisJob).where(AnalysisJob.video_id == vid))
        await s.commit()
    assert await _build_one(vid) is None, "클립이 있는데 또 집었다"


async def test_unknown_duration_is_not_picked(client, admin_token):
    """길이가 없으면 잡을 만들지 않고 길이가 생길 때까지 기다린다.

    잡을 만들면 곧바로 실패로 확정된다. 그러면 잡이 있다는 이유로 다시는 대상이 되지 않는다.
    """
    vid = await _seed(client, admin_token, duration=0.0)
    assert await _build_one(vid) is None
    async with SessionLocal() as s:
        assert await s.scalar(select(AnalysisJob.id).where(
            AnalysisJob.video_id == vid,
            AnalysisJob.adapter_key == service.ADAPTER_KEY)) is None


async def test_missing_file_on_a_waiting_job_fails_without_waking_the_worker(
        client, admin_token):
    """대기 잡의 영상 파일이 사라졌으면 워커를 호출하지 않고 잡을 실패로 둔다.

    실패로 두지 않으면 매 주기 잡만 만들고 화면은 계속 진행 중으로 표시한다.
    """
    vid = await _seed(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        s.add(AnalysisJob(project_id=v.project_id, video_id=vid,
                          adapter_key=service.ADAPTER_KEY, status="pending", attempt=1))
        v.source_uri = None
        v.analysis_uri = None
        await s.commit()
    assert await _build_one(vid) is None
    job = await _job(vid)
    assert (job.status, job.error_msg) == ("failed", service.NO_FILE)


# ── 소비: DB 없는 러너가 이 페이로드로 실제로 돈다 ─────────────────────────────


async def test_worker_runs_on_the_payload_alone(client, admin_token, monkeypatch):
    """자격 증명과 DB 없이 페이로드만으로 러너가 결과를 낸다.

    검출만 스텁하고 나머지 경로는 실제 코드로 돌린다. ffmpeg와 네트워크 경로는 엔진 쪽 테스트가 확인한다.
    """
    from reborn_transcode import scene_split as engine

    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    monkeypatch.setattr(
        engine, "detect", lambda *a, **k: ([30.0, 60.0, 90.0], [(29.0, 32.0)]))

    result = engine.run_job(job)
    assert result["segments"], "구간이 안 나왔다"
    assert result["raw_count"] >= len(result["segments"])
    assert result["rule"] == "샷 경계 · 최대 40초 · 무음 구간 우선"


# ── 판정 ──────────────────────────────────────────────────────────────────────


async def test_success_creates_clips_and_records_the_basis(client, admin_token):
    """성공하면 클립을 만들고 병합 규칙과 병합 전후 수를 기록한다."""
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    assert await _complete(vid, job["token"], ok=True,
                           segments=_SEGMENTS, basis=_BASIS) == "done"

    async with SessionLocal() as s:
        clips = list(await s.scalars(
            select(Clip).where(Clip.video_id == vid).order_by(Clip.start_seconds)))
    assert [(float(c.start_seconds), float(c.end_seconds)) for c in clips] == [
        (0.0, 38.4), (38.4, 75.0), (75.0, 120.0)]
    # 이 스택은 요약·음성 인식을 하지 않으므로 해당 필드를 채우지 않는다.
    assert all(not c.auto_caption and not c.speakers for c in clips)
    row = await _job(vid)
    assert row.status == "done"
    assert row.payload["basis"]["raw_count"] == 412
    assert row.payload["clips_created"] == 3
    assert row.token is None and row.lease_until is None


async def test_clips_become_labeling_work(client, admin_token):
    """장면 분할로 만든 클립마다 가져갈 수 있는 라벨링 작업이 생기는지 확인한다.

    클립은 만들 때부터 정상 상태다. 작업을 만드는 곳이 분석(MI) 회수뿐이면
    샷 검출 프로젝트의 클립은 라벨러의 대기열에 끝내 들어오지 않는다.
    """
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    assert await _complete(vid, job["token"], ok=True,
                           segments=_SEGMENTS, basis=_BASIS) == "done"
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Clip.clip_id, WorkItem.status)
            .join(WorkItem, WorkItem.clip_id == Clip.clip_id, isouter=True)
            .where(Clip.video_id == vid))).all()
    assert len(rows) == 3
    assert all(status == "available" for _, status in rows), rows



async def _tickets(vid: uuid.UUID) -> int:
    """영상의 영상 단위 작업(분석 요청 티켓) 수를 센다."""
    async with SessionLocal() as s:
        return len((await s.scalars(select(WorkItem.work_item_id).where(
            WorkItem.video_id == vid, WorkItem.clip_id.is_(None)))).all())


async def test_clips_retire_the_video_ticket(client, admin_token):
    """장면 분할이 클립을 만들면 영상 단위 분석 요청 티켓을 지우고, 클립이 없으면 남긴다.

    티켓이 남으면 클립 시간에 잡히지 않는 작업 수에 들어가 진척 카드의 분모가 어긋난다.
    """
    vid = await _seed(client, admin_token)
    assert await _tickets(vid) == 1
    job = await _build_one(vid)
    assert await _complete(vid, job["token"], ok=True,
                           segments=_SEGMENTS, basis=_BASIS) == "done"
    assert await _tickets(vid) == 0

    empty = await _seed(client, admin_token)
    job = await _build_one(empty)
    assert await _complete(empty, job["token"], ok=True, segments=[], basis=_BASIS) == "done"
    assert await _tickets(empty) == 1

async def test_stale_token_is_discarded(client, admin_token):
    """이전 시도의 보고를 펜싱 토큰 검사로 버린다.

    리스 만료로 회수·재투입된 뒤 이전 워커가 다시 응답하는 경우다.
    받아들이면 다시 도는 시도를 덮어써서 클립이 두 번 생긴다.
    """
    vid = await _seed(client, admin_token)
    await _build_one(vid)
    assert await _complete(vid, "낡은토큰", ok=True,
                           segments=_SEGMENTS, basis=_BASIS) == "stale"
    async with SessionLocal() as s:
        assert await s.scalar(select(Clip.clip_id).where(Clip.video_id == vid)) is None


async def test_transient_failure_does_not_burn_an_attempt(client, admin_token):
    """일시 오류는 시도 횟수를 쓰지 않는다.

    429·타임아웃·만료된 presign을 영구 실패로 세면 긴 영상일수록 실패로 끝난다.
    """
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    before = (await _job(vid)).attempt
    assert await _complete(vid, job["token"], ok=False,
                           error="검출 시간 초과", transient=True) == "requeued"
    row = await _job(vid)
    assert row.attempt == before, "일시 오류가 시도를 태웠다"
    assert row.status == "pending" and row.token is None


async def test_permanent_failure_settles_at_the_cap(client, admin_token):
    """영구 오류는 상한까지만 다시 돈다. 그 뒤에는 `failed`로 확정한다."""
    vid = await _seed(client, admin_token)
    for _ in range(service.MAX_ATTEMPTS + 1):
        job = await _build_one(vid)
        if job is None:
            break
        await _complete(vid, job["token"], ok=False, error="검출 실패", transient=False)
    row = await _job(vid)
    assert row.status == "failed"
    assert row.error_msg == "검출 실패"


async def test_reclaim_revives_and_the_next_sweep_picks_it_up(client, admin_token):
    """리스가 끝나면 잡을 대기로 되돌리고 다음 주기가 다시 가져가는지 확인한다.

    대기로 되돌린 잡을 아무도 가져가지 않으면 화면은 계속 "자르는 중"으로 남는다.
    """
    from datetime import timedelta

    vid = await _seed(client, admin_token)
    first = await _build_one(vid)
    async with SessionLocal() as s:
        row = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id == vid,
            AnalysisJob.adapter_key == service.ADAPTER_KEY))).one()
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()

    async with SessionLocal() as s:
        assert await service.reclaim_expired(s) >= 1
    assert (await _job(vid)).token is None, "토큰을 안 지우면 옛 워커가 새 시도를 덮는다"

    again = await _build_one(vid)
    assert again is not None, "되살린 행을 아무도 안 집었다"
    assert again["token"] != first["token"]


async def _expire(vid: uuid.UUID) -> None:
    """리스 만료 시각을 지난 것으로 바꾼다. 워커가 보고 없이 멈춘 상태를 흉내 낸다."""
    from datetime import timedelta

    async with SessionLocal() as s:
        row = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id == vid,
            AnalysisJob.adapter_key == service.ADAPTER_KEY))).one()
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()


async def _start(vid: uuid.UUID, token: str) -> bool:
    """워커의 시작 하트비트를 넣는다. 시작한 시도의 회수만 시도 횟수로 센다."""
    async with SessionLocal() as s:
        return await service.extend_lease(s, vid, token)


async def test_reclaim_counts_as_an_attempt_and_closes_the_run(client, admin_token):
    """시작한 뒤 회수된 시도는 시도 횟수로 센다.

    세지 않으면 워커를 멈추게 하는 입력이 끝없이 다시 돈다.
    회수한 시도의 이력도 닫는다. 닫지 않으면 타임라인이 멈춘 시도를 "진행 중"으로 표시한다.
    """
    from api_gateway.models import MediaStageRun

    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    assert await _start(vid, job["token"])
    before = (await _job(vid)).attempt
    await _expire(vid)
    async with SessionLocal() as s:
        assert await service.reclaim_expired(s) >= 1

    row = await _job(vid)
    assert row.attempt == before + 1, "회수가 시도를 안 셌다 — 결정적으로 죽는 입력이 영원히 돈다"
    assert row.status == "pending" and row.token is None
    async with SessionLocal() as s:
        runs = list(await s.scalars(select(MediaStageRun).where(
            MediaStageRun.video_id == vid, MediaStageRun.stage == "scene_split")))
    assert runs and all(r.status != "running" for r in runs), "회수된 시도가 열린 채 남았다"
    assert runs[-1].detail.get("transient") is True


async def test_reclaim_settles_at_the_cap(client, admin_token):
    """회수가 반복되면 상한에서 멈춘다. 보고 없이 멈추는 잡을 계속 되살리지 않는다."""
    vid = await _seed(client, admin_token)
    for _ in range(service.MAX_ATTEMPTS + 1):
        job = await _build_one(vid)
        if job is None:
            break
        assert await _start(vid, job["token"])
        await _expire(vid)
        async with SessionLocal() as s:
            await service.reclaim_expired(s)
    row = await _job(vid)
    assert row.status == "failed", f"회수만 거듭하다 상한에서 안 멈췄다: {row.status}/{row.attempt}"
    # 사유에 멈춘 이유(재시도 횟수 초과)를 적는다.
    assert "횟수를 넘어" in (row.error_msg or "")


# ── 화면 ──────────────────────────────────────────────────────────────────────


async def test_timeline_reads_our_job_not_the_vendor_gap(client, admin_token):
    """타임라인의 장면 분할 항목이 자체 장면 분할 잡의 상태를 표시한다.

    MI 스택에서는 이 항목이 `건너뜀 · 벤더가 자름`이다.
    자체 장면 분할 스택에서는 실제로 도는 단계라서 화면이 실행 상태를 표시해야 한다.
    """
    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    rows = (await client.get(f"/videos/{vid}/prep-timeline",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    step = next(r for r in rows["entries"] if r["stage"] == "scene_split")
    assert step["status"] == "running", step

    await _complete(vid, job["token"], ok=True, segments=_SEGMENTS, basis=_BASIS)
    rows = (await client.get(f"/videos/{vid}/prep-timeline",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    step = next(r for r in rows["entries"] if r["stage"] == "scene_split")
    assert step["status"] == "ok", step


async def test_mi_stage_is_not_confused_by_our_row(client, admin_token):
    """장면 분할 행을 MI 단계로 판정하지 않는다.

    `analysis_job`은 어댑터 중립 테이블이라 한 영상에 여러 행이 있다.
    어댑터 필터가 없으면 장면 분할 행을 골라 "MI가 running"이라고 표시한다.
    그래서 MI 단계 판정은 영상당 행이 하나라고 가정하지 않고 어댑터로 거른다.
    자체 장면 분할 스택의 타임라인에는 MI 단계 줄이 없고, 장면 분할 행은 장면 분할 줄에 반영된다.
    """
    vid = await _seed(client, admin_token)
    await _build_one(vid)
    rows = (await client.get(f"/videos/{vid}/prep-timeline",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    by_stage = {r["stage"]: r for r in rows["entries"]}
    assert not {"register", "analyze", "summarize"} & set(by_stage), "MI 단계 줄이 있다"
    assert by_stage["scene_split"]["status"] == "running"


async def test_the_stack_is_selectable_through_the_api(client, admin_token):
    """자체 장면 분할 스택이 카탈로그에 있고 `available`인지 확인한다.

    파이프라인이 모두 준비돼 있어도 그 스택을 고를 수 없으면 파이프라인이 실행되지 않는다.
    """
    rows = (await client.get("/projects/analysis-presets",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    ours = next(r for r in rows if r["scene_adapter_key"] == service.ADAPTER_KEY
                and r["available"])
    assert ours["storage_key"] == "ncp"


async def test_our_stack_does_not_go_to_the_vendor(client, admin_token):
    """자체 장면 분할 스택의 영상은 MI로 보내지 않는다.

    보내면 한 프로젝트에 두 벤더의 클립이 섞이고 라벨러가 그 차이를 모른 채 작업한다.
    MI는 시간당 과금이라 쓰지 않을 분석에 비용도 나간다.
    """
    from api_gateway.models import AnalysisJob as _Job
    from api_gateway.services.maiu import service as maiu_service
    from reborn_contracts.maiu import MaiuDispatchRequest

    vid = await _seed(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        user = await s.get(__import__("api_gateway.models", fromlist=["User"]).User,
                           v.uploaded_by)
        result = await maiu_service.dispatch(
            s, v.project_id, MaiuDispatchRequest(limit=10), user)
    assert result.created == []
    async with SessionLocal() as s:
        rows = list(await s.scalars(select(_Job).where(
            _Job.video_id == vid, _Job.adapter_key == "maiu")))
    assert rows == [], "MI 잡이 생겼다 — 두 벤더 클립이 섞인다"


# ── 리스와 하트비트, 일시 오류 연속 상한 ─────────────────────────────────────────


async def test_dispatch_lease_covers_the_queue_wait(client, admin_token):
    """투입 때 리스는 큐 대기를 덮고, 시작 하트비트가 평소 리스로 줄인다.

    큐 대기까지 짧은 리스로 덮으면 앞선 긴 인코딩이 워커를 점유한 동안 대기 잡이 회수된다.
    """
    from datetime import timedelta

    from api_gateway.services.media import transcode

    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    lease = (await _job(vid)).lease_until
    assert lease >= datetime.now(UTC) + timedelta(seconds=transcode._QUEUE_WAIT_S - 60)
    assert job["heartbeat_every_s"] < service.LEASE_S
    assert job["timeout_s"] != service.LEASE_S, "실행 상한과 리스가 같으면 정상 잡이 회수된다"

    assert not await _start(vid, "낡은토큰"), "다른 토큰의 하트비트가 리스를 늘렸다"
    assert await _start(vid, job["token"])
    lease = (await _job(vid)).lease_until
    assert lease <= datetime.now(UTC) + timedelta(seconds=service.LEASE_S + 5)


async def test_job_url_outlives_queue_wait_plus_execution_cap():
    """입력 URL은 큐 대기 상한 뒤 시작한 잡이 실행 상한까지 읽어도 유효하다.

    검출은 원본을 처음부터 끝까지 스트리밍으로 읽어 처리 도중에 만료되면 일시 오류가 된다.
    """
    from api_gateway.services.media import transcode

    assert service.JOB_URL_TTL_S >= transcode._QUEUE_WAIT_S + service.EXEC_CAP_S


async def test_unstarted_reclaim_does_not_burn_an_attempt(client, admin_token):
    """시작 하트비트가 오지 않은 시도의 회수는 시도로 세지 않는다. 영상 탓이 아니기 때문이다."""
    vid = await _seed(client, admin_token)
    await _build_one(vid)
    before = (await _job(vid)).attempt
    await _expire(vid)
    async with SessionLocal() as s:
        assert await service.reclaim_expired(s) >= 1
    row = await _job(vid)
    assert (row.status, row.attempt) == ("pending", before)


async def _wake(vid: uuid.UUID) -> None:
    """재시도 시각을 지난 시각으로 돌린다. 백오프 간격이 지난 상태와 같다."""
    from datetime import timedelta

    from sqlalchemy.orm.attributes import flag_modified

    async with SessionLocal() as s:
        row = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id == vid,
            AnalysisJob.adapter_key == service.ADAPTER_KEY))).one()
        row.payload = {**(row.payload or {}),
                       "retry_after": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        flag_modified(row, "payload")
        await s.commit()


async def test_transient_failures_back_off_and_stop_at_the_limit(client, admin_token):
    """일시 오류 뒤에는 재시도 시각까지 보내지 않고, 연속 상한에 이르면 실패로 확정한다."""
    from api_gateway.services.media import transcode

    vid = await _seed(client, admin_token)
    job = await _build_one(vid)
    assert await _complete(vid, job["token"], ok=False, error="입력을 읽지 못했습니다",
                           transient=True) == "requeued"
    row = await _job(vid)
    assert row.payload["transient_streak"] == 1 and row.payload["retry_after"]
    assert await _build_one(vid) is None, "재시도 시각 전에 다시 보냈다"

    for _ in range(transcode._TRANSIENT_LIMIT - 1):
        await _wake(vid)
        job = await _build_one(vid)
        assert job is not None
        result = await _complete(vid, job["token"], ok=False, error="입력을 읽지 못했습니다",
                                 transient=True)
    assert result == "failed"
    row = await _job(vid)
    assert row.status == "failed"
    assert "일시 오류가 10회 이어져" in (row.error_msg or "")
