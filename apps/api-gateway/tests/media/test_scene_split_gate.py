"""자체 장면 분할이 준비가 끝난 영상만 자르는지, 선행 조건 실패 뒤 다시 열리는지 확인하는 테스트.

규격 검증 전에는 변환·비식별화·재생본이 모두 기본값 `skipped`라 끝난 것처럼 보인다.
그 상태를 준비 완료로 판정하면 두 가지 문제가 생긴다.
길이가 없는 영상(버킷 등록)은 곧바로 실패로 확정되고 다시는 대상이 되지 않는다.
길이가 있는 영상(브라우저 업로드)은 비식별화 전 원본에서 잘린다.
MI 분석 요청 게이트와 같은 판정 함수를 쓰는지도 확인한다.
"""
import uuid
from datetime import UTC, datetime

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, DomainEvent, Project, Video
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.media import clip as clip_service
from api_gateway.services.media import scene_split as service
from api_gateway.services.media import video as video_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import delete, select, true
from sqlalchemy import update as sa_update
from tests._helpers import add_member, new_project, put_video_file, upload_video

_SEGMENTS = [{"start": 0.0, "end": 30.0}, {"start": 30.0, "end": 69.0}]


@pytest.fixture(autouse=True)
async def _isolate_scene_jobs():
    """앞선 테스트가 남긴 장면 분할 잡과 영상을 대상에서 뺀다.

    투입 상한(4)을 모든 테스트가 같이 쓴다. 앞 테스트의 영상이 대상으로 남아 있으면
    이번 테스트의 영상이 상한 밖으로 밀려 파일 전체 실행에서만 실패한다.
    """
    async with SessionLocal() as s:
        await s.execute(delete(AnalysisJob).where(
            AnalysisJob.adapter_key == service.ADAPTER_KEY))
        ours = select(Project.project_id).where(
            Project.settings["analysis_preset"].astext == "ncp-shots")
        await s.execute(
            sa_update(Video).where(Video.project_id.in_(ours))
            .values(deleted_at=datetime.now(UTC))
        )
        await s.commit()
    yield


def _h(token: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _shots_project(client: AsyncClient, admin_token: str, *, deident: bool) -> str:
    """자체 장면 분할 스택 프로젝트를 만든다. 비식별화 켬·끔은 설정 그대로 DB에 둔다."""
    pid = await new_project(client, admin_token, f"gate-{uuid.uuid4().hex[:6]}",
                            analysis_preset="ncp-shots")
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = deident
        await s.commit()
    return pid


async def _browser_upload(client: AsyncClient, admin_token: str, pid: str,
                          *, duration: float) -> uuid.UUID:
    """브라우저 업로드 경로로 영상을 올린다. 화면은 등록할 때 브라우저가 읽은 길이를 보낸다."""
    vid = (await client.post(
        f"/projects/{pid}/videos", headers=_h(admin_token),
        json={"title": "browser", "copyright_policy": "pre_cleared",
              "duration_seconds": duration},
    )).json()["video_id"]
    r = await put_video_file(client, admin_token, vid)
    assert r.status_code == 200, r.text
    return uuid.UUID(vid)


async def _set(vid: uuid.UUID, **values) -> None:
    """영상 열을 바로 바꾼다. 워커가 단계를 끝낸 결과를 흉내 낸다."""
    async with SessionLocal() as s:
        await s.execute(sa_update(Video).where(Video.video_id == vid).values(**values))
        await s.commit()


async def _ready(client: AsyncClient, admin_token: str, *, deident: bool) -> uuid.UUID:
    """준비가 모두 끝난 영상 하나를 만든다."""
    pid = await _shots_project(client, admin_token, deident=deident)
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    await _set(vid, probe_status="passed", duration_seconds=69.0,
               transcode_status="skipped",
               deident_status="done" if deident else "skipped", publish_status="done")
    return vid


async def _picked(vid: uuid.UUID) -> dict | None:
    """투입 주기를 한 번 돌리고 이 영상의 잡 페이로드를 돌려준다. 이 영상이 선택되지 않았으면 None이다."""
    async with SessionLocal() as s:
        jobs = await service.build_jobs(s)
    return next((j for j in jobs if j["video_id"] == str(vid)), None)


async def _job(vid: uuid.UUID) -> AnalysisJob | None:
    """이 영상의 장면 분할 잡 행을 돌려준다."""
    async with SessionLocal() as s:
        return await s.scalar(select(AnalysisJob).where(
            AnalysisJob.video_id == vid, AnalysisJob.adapter_key == service.ADAPTER_KEY))


async def _failed_job(vid: uuid.UUID, *, error: str, attempt: int) -> None:
    """실패로 확정된 장면 분할 잡을 심는다."""
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        s.add(AnalysisJob(project_id=v.project_id, video_id=vid,
                          adapter_key=service.ADAPTER_KEY, status="failed",
                          attempt=attempt, error_msg=error))
        await s.commit()


# ── 준비 판정 ────────────────────────────────────────────────────────────────


async def test_browser_upload_is_not_cut_before_deident(client, admin_token):
    """길이가 있는 업로드 영상을 규격 검증·비식별화가 끝난 뒤에만 자른다.

    운영의 실제 처리 순서를 그대로 따른다. 업로드 완료(검증 대기, 세 단계 기본값 `skipped`, 길이 있음),
    검증 통과(비식별화 열림), 비식별화 진행, 비식별화·재생본 완료 순서다. 마지막 단계에서만 잘라야 한다.
    """
    pid = await _shots_project(client, admin_token, deident=True)
    vid = await _browser_upload(client, admin_token, pid, duration=69.0)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert (v.probe_status, v.deident_status, float(v.duration_seconds)) == (
            "pending", "skipped", 69.0)
    assert await _picked(vid) is None, "검증 전 원본을 잘랐다"
    assert await _job(vid) is None

    # 규격 검증 통과. probe는 통과를 기록한 같은 트랜잭션에서 다음 단계를 연다.
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        await open_next(s, v)
        await s.commit()
        await s.refresh(v)
        opened = (v.transcode_status, v.deident_status)
    assert "pending" in opened, f"검증 통과 뒤 열린 단계가 없다: {opened}"
    assert await _picked(vid) is None, "비식별화 전에 잘랐다"

    await _set(vid, transcode_status="skipped", deident_status="running")
    assert await _picked(vid) is None, "비식별화 중에 잘랐다"

    await _set(vid, deident_status="done", publish_status="done")
    job = await _picked(vid)
    assert job is not None, "준비가 끝났는데 집지 않았다"


async def test_registered_video_is_not_failed_before_probe(client, admin_token):
    """버킷 등록 직후(검증 대기·길이 없음)에는 잡을 만들지 않는다.

    만들면 길이가 없어 곧바로 실패로 확정된다. 그러면 잡이 있다는 이유로 다시는 대상이 되지 않는다.
    """
    pid = await _shots_project(client, admin_token, deident=True)
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    assert await _picked(vid) is None
    assert await _job(vid) is None, "검증 전 영상에 잡을 만들었다"


@pytest.mark.parametrize("deident_status", ["skipped", "pending", "queued", "running", "failed"])
async def test_deident_on_blocks_until_done(client, admin_token, deident_status):
    """비식별화를 켠 프로젝트는 비식별화가 끝나야 자른다. 기본값 `skipped`도 미완료로 판정한다."""
    vid = await _ready(client, admin_token, deident=True)
    await _set(vid, deident_status=deident_status)
    assert await _picked(vid) is None, f"비식별화 {deident_status}인데 잘랐다"


async def test_deident_done_is_cut(client, admin_token):
    """비식별화를 켠 프로젝트에서 비식별화가 끝난 영상은 자른다."""
    vid = await _ready(client, admin_token, deident=True)
    assert await _picked(vid) is not None


async def test_deident_off_skipped_is_cut(client, admin_token):
    """비식별화를 끈 프로젝트의 `skipped`는 쓰지 않기로 한 상태라 자른다."""
    vid = await _ready(client, admin_token, deident=False)
    assert await _picked(vid) is not None


# ── 선행 조건 실패의 복구 ────────────────────────────────────────────────────


async def test_precondition_failure_reopens_when_ready(client, admin_token):
    """길이가 없어 실패한 잡은 길이가 생기고 준비가 끝나면 다시 열려 투입된다.

    시도 1, 길이 사유인 실패 행을 심는다. 이런 행은 데이터를 손으로 고치지 않아도
    다음 투입 주기에 다시 열려야 한다.
    """
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error=service.NO_DURATION, attempt=1)

    payload = await _picked(vid)
    assert payload is not None, "선행 조건이 갖춰졌는데 다시 열지 않았다"
    row = await _job(vid)
    assert (row.status, row.attempt) == ("running", 1)
    assert row.token == payload["token"] and row.error_msg is None


async def test_precondition_failure_waits_while_not_ready(client, admin_token):
    """선행 조건 실패 잡도 준비가 끝나지 않았으면 그대로 둔다."""
    vid = await _ready(client, admin_token, deident=True)
    await _set(vid, deident_status="running")
    await _failed_job(vid, error=service.NO_DURATION, attempt=1)
    assert await _picked(vid) is None
    assert (await _job(vid)).status == "failed"


async def test_processing_failure_is_not_reopened(client, admin_token):
    """처리 오류로 상한에 이른 실패는 자동으로 다시 열지 않는다. 같은 입력이면 같은 오류가 난다."""
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error="검출 실패", attempt=service.MAX_ATTEMPTS)
    assert await _picked(vid) is None
    assert (await _job(vid)).status == "failed"


async def test_reopened_job_cuts_once(client, admin_token):
    """다시 연 잡이 끝나면 클립을 한 번만 만들고 그 영상을 다시 대상으로 고르지 않는다."""
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error=service.NO_DURATION, attempt=1)
    payload = await _picked(vid)
    async with SessionLocal() as s:
        assert await service.complete(s, video_id=vid, token=payload["token"], ok=True,
                                      segments=_SEGMENTS, basis={}) == "done"
    assert await _picked(vid) is None
    async with SessionLocal() as s:
        clips = list(await s.scalars(select(Clip).where(Clip.video_id == vid)))
    assert len(clips) == 2


# ── 운영자 다시 실행 ─────────────────────────────────────────────────────────


def _retry_url(pid: str, vid: uuid.UUID) -> str:
    """다시 실행 경로를 만든다."""
    return f"/projects/{pid}/videos/{vid}/scene-split/retry"


async def _pid(vid: uuid.UUID) -> str:
    """영상의 프로젝트 ID를 돌려준다."""
    async with SessionLocal() as s:
        return str((await s.get(Video, vid)).project_id)


async def test_retry_reopens_failed_job_and_audits(client, admin_token):
    """처리 오류로 멈춘 잡을 운영자가 다시 열면 시도가 1로 돌아가고 다음 주기가 집는다."""
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error="검출 실패", attempt=service.MAX_ATTEMPTS)
    pid = await _pid(vid)

    r = await client.post(_retry_url(pid, vid), headers=_h(admin_token))
    assert r.status_code == 204, r.text
    row = await _job(vid)
    assert (row.status, row.attempt, row.error_msg) == ("pending", 1, None)
    async with SessionLocal() as s:
        ev = await s.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "video.scene_split_retried",
            DomainEvent.target_id == vid))
    assert ev is not None, "감사 이벤트가 없다"
    assert ev.payload["previous_error"] == "검출 실패"
    assert ev.payload["previous_attempt"] == service.MAX_ATTEMPTS
    # 다음 투입 전까지 타임라인은 대기로 표시한다. 실패로 남으면 다시 실행 버튼이 계속 보여 운영자가 다시 누른다.
    rows = (await client.get(f"/videos/{vid}/prep-timeline", headers=_h(admin_token))).json()
    step = next(e for e in rows["entries"] if e["stage"] == "scene_split")
    assert step["status"] == "pending", step
    assert await _picked(vid) is not None, "다시 연 잡을 집지 않았다"


async def test_retry_requires_project_admin(client, admin_token):
    """project_admin이 아니면 다시 실행 요청을 403으로 거절한다."""
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error="검출 실패", attempt=service.MAX_ATTEMPTS)
    pid = await _pid(vid)
    _, tok = await add_member(client, admin_token, pid, "annotator")

    r = await client.post(_retry_url(pid, vid), headers=_h(tok))
    assert r.status_code == 403, r.text
    assert (await _job(vid)).status == "failed"


async def test_retry_refuses_when_clips_exist(client, admin_token):
    """클립이 이미 있으면 409로 거절한다. 다시 자르면 같은 시간대에 클립이 중복으로 생긴다."""
    vid = await _ready(client, admin_token, deident=True)
    await _failed_job(vid, error="검출 실패", attempt=service.MAX_ATTEMPTS)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert await clip_service.create_scene_clip(
            s, video=v, start_seconds=0.0, end_seconds=10.0,
            auto_caption={}, speakers=[], scene_extra={})
        await s.commit()
    pid = await _pid(vid)

    r = await client.post(_retry_url(pid, vid), headers=_h(admin_token))
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "scene_split_has_clips"


async def test_retry_refuses_when_not_failed_or_not_ready(client, admin_token):
    """실패한 잡이 없거나 자를 준비가 끝나지 않았으면 409로 거절한다."""
    vid = await _ready(client, admin_token, deident=True)
    pid = await _pid(vid)
    r = await client.post(_retry_url(pid, vid), headers=_h(admin_token))
    assert (r.status_code, r.json()["detail"]) == (409, "scene_split_not_failed")

    await _failed_job(vid, error="검출 실패", attempt=service.MAX_ATTEMPTS)
    await _set(vid, deident_status="running")
    r = await client.post(_retry_url(pid, vid), headers=_h(admin_token))
    assert (r.status_code, r.json()["detail"]) == (409, "scene_split_not_ready")
    assert (await _job(vid)).status == "failed"


# ── MI 게이트와 같은 판정 ─────────────────────────────────────────────────────


async def test_both_gates_share_one_readiness_rule(client, admin_token, monkeypatch):
    """MI 분석 요청 게이트와 장면 분할 게이트가 같은 준비 판정 함수를 쓴다.

    공용 판정을 "모두 막힘"으로 바꾸면 두 게이트가 함께 닫혀야 한다.
    한쪽이 조건을 따로 쓰면 그쪽만 열린 채 남아 이 테스트가 실패한다.
    """
    from api_gateway.models import User
    from api_gateway.services.maiu import service as maiu_service
    from reborn_contracts.maiu import MaiuDispatchRequest

    maiu_client.set_client(FakeMaiuClient())
    try:
        split_vid = await _ready(client, admin_token, deident=False)
        mi_pid = await new_project(client, admin_token, f"mi-{uuid.uuid4().hex[:6]}")
        mi_vid = uuid.UUID(await upload_video(client, admin_token, mi_pid))
        await _set(mi_vid, probe_status="passed", duration_seconds=69.0,
                   transcode_status="skipped", deident_status="skipped",
                   publish_status="done")

        async def mi_dispatched() -> bool:
            """MI 분석 요청을 보내 잡이 만들어졌으면 True."""
            async with SessionLocal() as s:
                v = await s.get(Video, mi_vid)
                user = await s.get(User, v.uploaded_by)
                result = await maiu_service.dispatch(
                    s, v.project_id, MaiuDispatchRequest(video_ids=[mi_vid]), user)
            return bool(result.created)

        monkeypatch.setattr(video_service, "_prep_unfinished_expr", lambda: true())
        assert await _picked(split_vid) is None, "장면 분할 게이트가 공용 판정을 쓰지 않는다"
        assert not await mi_dispatched(), "MI 게이트가 공용 판정을 쓰지 않는다"

        monkeypatch.undo()
        assert await _picked(split_vid) is not None
        assert await mi_dispatched(), "공용 판정을 되돌렸는데 MI 게이트가 열리지 않았다"
    finally:
        maiu_client._client_singleton = None
