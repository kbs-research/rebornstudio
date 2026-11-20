"""스택 선택 저장과 첫 영상 등록이 동시에 올 때의 경합 테스트.

잠금 판정(영상 수)과 저장 사이에 첫 영상이 생기면 그 영상은 이전 선택으로 기록되고 프로젝트에는 새 선택이 저장된다.
이 테스트는 두 DB 세션을 실제로 겹쳐 돌린다.
영상 등록이 프로젝트 설정을 읽은 뒤 행을 쓰기 전에 멈춘 사이 저장이 들어오면,
저장은 그 영상이 커밋되기를 기다렸다가 잠김(409)으로 끝나야 한다.

외부 점검(`_verify`)은 이 테스트의 대상이 아니라서 아무것도 하지 않게 바꾼다.
"""
import asyncio
from uuid import UUID

import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Project, User, Video
from api_gateway.services import platform_credentials as pc
from api_gateway.services.media import ingest_batch, storage
from api_gateway.services.media import video as video_service
from api_gateway.services.stacks import project_stack, project_storage, stack_lock
from fastapi import HTTPException
from httpx import AsyncClient
from reborn_contracts import (
    ProjectAnalysisStackDTO,
    ProjectStorageStackDTO,
    VideoCreateRequest,
)
from sqlalchemy import func, select
from tests import _storage
from tests._helpers import new_project

#: 분석 대상이 가리키는 MI 연결 id. 외부 점검(`_verify`)을 건너뛰므로 DB에 없어도 된다.
_MI = "00000000-0000-4000-8000-00000000ace1"
#: 처음 저장해 둔 분석 대상과, 저장이 바꾸려는 분석 대상.
_ANALYSIS_A = pc.AnalysisTarget(_MI, "ws-race", "101")
_ANALYSIS_B = ProjectAnalysisStackDTO(
    analysis_connection_id=_MI, maiu_workspace="ws-race", maiu_project_id="202")


async def _admin_id(client: AsyncClient, token: str) -> UUID:
    """로그인한 관리자의 사용자 id를 읽는다."""
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    return UUID(me.json()["user_id"])


async def _seed(pid: str) -> None:
    """프로젝트에 저장소 대상(테스트 저장소)과 분석 대상 A를 기록해 둔다."""
    conn = await _storage.connection_for(pid)
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        st = pc.with_storage_target(p.settings, pc.StorageTarget(conn, _storage.BUCKET))
        p.settings = pc.with_analysis_target(st, _ANALYSIS_A)
        await s.commit()


async def _create_video(pid: str, uid: UUID) -> UUID:
    """다른 DB 세션에서 영상 행을 만든다(업로드 시작과 같은 경로)."""
    async with SessionLocal() as s:
        user = await s.get(User, uid)
        dto = await video_service.create(
            s, UUID(pid), VideoCreateRequest(title="race", copyright_policy="pre_cleared"), user)
        return dto.video_id


async def _save(kind: str, pid: str, uid: UUID) -> str:
    """다른 DB 세션에서 스택 선택을 바꿔 저장한다. 결과는 `saved` 또는 거절 사유다."""
    async with SessionLocal() as s:
        user = await s.get(User, uid)
        project = await s.get(Project, UUID(pid))
        try:
            if kind == "storage":
                await project_storage.save(s, project, ProjectStorageStackDTO(
                    storage_connection_id=await _storage.connection_for(pid),
                    bucket="race-other-bucket"), user)
            else:
                await project_stack.save(s, project, _ANALYSIS_B, user)
        except HTTPException as e:
            return str(e.detail)
        return "saved"


@pytest.mark.parametrize("kind", ["storage", "analysis"])
async def test_a_save_racing_the_first_video_is_refused(
    client: AsyncClient, admin_token: str, monkeypatch, kind: str,
):
    """첫 영상이 설정을 읽은 뒤 행을 쓰기 전에 저장이 오면 저장이 그 영상을 기다린 뒤 409로 끝나는지 확인한다.

    영상은 처음 선택으로 기록되고 프로젝트 선택도 그대로다.
    저장이 먼저 반영되면 영상과 프로젝트가 서로 다른 저장소·MI 프로젝트를 가리킨다.
    """
    async def _no_verify(*_a, **_k) -> None:
        """외부 점검을 건너뛴다."""

    monkeypatch.setattr(project_storage, "_verify", _no_verify)
    monkeypatch.setattr(project_stack, "_verify", _no_verify)

    entered, release = asyncio.Event(), asyncio.Event()
    real = ingest_batch.ensure_batch

    async def _paused(db, **kw):
        """영상 등록이 설정을 읽은 뒤 행을 쓰기 전에 멈춘다."""
        entered.set()
        await release.wait()
        return await real(db, **kw)

    monkeypatch.setattr(ingest_batch, "ensure_batch", _paused)

    pid = await new_project(client, admin_token, f"race-{kind}")
    await _seed(pid)
    uid = await _admin_id(client, admin_token)

    video_task = asyncio.create_task(_create_video(pid, uid))
    await asyncio.wait_for(entered.wait(), 10)
    save_task = asyncio.create_task(_save(kind, pid, uid))
    await asyncio.sleep(0.5)
    release.set()
    vid = await asyncio.wait_for(video_task, 10)
    outcome = await asyncio.wait_for(save_task, 10)

    assert outcome == f"{kind}_stack_locked"
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        v = await s.get(Video, vid)
        assert pc.storage_target_of(p.settings) == pc.StorageTarget(
            await _storage.connection_for(pid), _storage.BUCKET)
        assert pc.analysis_target_of(p.settings) == _ANALYSIS_A
        assert v.extra[storage.PIN_KEY]["bucket"] == _storage.BUCKET


async def _clear(kind: str, pid: str) -> None:
    """저장소·분석 대상 기록을 지운다. 등록이 게이트를 지난 뒤 비우는 저장이 먼저 커밋된 상태다."""
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        if kind == "storage":
            p.settings = pc.with_storage_target(p.settings, None)
        else:
            p.settings = pc.with_analysis_target(p.settings, None)
        await s.commit()


@pytest.mark.parametrize("kind", ["storage", "analysis"])
@pytest.mark.parametrize("path", ["upload", "storage"])
async def test_a_clearing_save_before_the_first_video_refuses_the_video(
    client: AsyncClient, admin_token: str, monkeypatch, kind: str, path: str,
):
    """등록이 프로젝트를 읽은 뒤 대상을 비우는 저장이 먼저 커밋되면 등록이 409로 끝나고 영상이 생기지 않는지 확인한다.

    등록하는 DB 세션은 대상이 있던 때의 프로젝트를 이미 읽어 두었다. 행을 잠그며 다시 읽은 최신 설정에는 대상이 없으므로
    등록이 거부되어야 한다. 그대로 만들면 대상이 빈 채 잠긴 프로젝트가 되고, 설정에서 다시 저장할 수 없다.
    두 DB 세션을 실제로 겹쳐 돌리고, 등록이 행을 잠그기 직전에 멈춘 사이 비우는 저장을 커밋한다.
    """
    pid = await new_project(client, admin_token, f"clear-{kind}-{path}")
    await _seed(pid)
    uid = await _admin_id(client, admin_token)
    # 대상 기록을 요구하는 서버(외부 연동이 켜진 서버)를 흉내 낸다.
    monkeypatch.setattr(settings, "maiu_enabled", True)

    entered, release = asyncio.Event(), asyncio.Event()
    real = stack_lock.hold_for_new_video

    async def _paused(db, project_id):
        """등록이 프로젝트를 읽은 뒤 행을 잠그기 전에 멈춘다."""
        entered.set()
        await release.wait()
        return await real(db, project_id)

    monkeypatch.setattr(stack_lock, "hold_for_new_video", _paused)

    async def _register() -> None:
        """다른 DB 세션에서 영상을 등록한다."""
        async with SessionLocal() as s:
            user = await s.get(User, uid)
            if path == "upload":
                await video_service.create(
                    s, UUID(pid), VideoCreateRequest(title="race", copyright_policy="pre_cleared"),
                    user)
            else:
                await video_service.register_from_storage(
                    s, UUID(pid), ["raw/race.mp4"], "pre_cleared", user)

    task = asyncio.create_task(_register())
    await asyncio.wait_for(entered.wait(), 10)
    await _clear(kind, pid)
    release.set()
    with pytest.raises(HTTPException) as e:
        await asyncio.wait_for(task, 10)
    assert e.value.status_code == 409 and e.value.detail == f"{kind}_stack_unset"
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count()).select_from(Video)
                           .where(Video.project_id == UUID(pid)))
    assert n == 0
