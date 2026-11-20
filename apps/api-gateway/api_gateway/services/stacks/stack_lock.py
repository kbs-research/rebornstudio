"""프로젝트 스택 선택(분석 대상·저장소 대상)의 잠금 판정과 행 잠금.

분석 대상(`project_stack`)과 저장소 대상(`project_storage`)은 같은 기준으로 잠긴다.
첫 영상 행이 생기면 그 영상의 원본·산출·에셋이 이미 고른 저장소와 MI 프로젝트에 있으므로
둘 다 바꾸지 않는다. 기준을 한 곳에 두어 두 선택이 같은 시점에 잠기게 한다.

잠금 판정(영상 수)과 저장 사이에 첫 영상이 생기면 그 영상은 이전 선택으로 기록된다.
프로젝트에는 새 선택이 저장되어 둘이 어긋난다. 그래서 두 쪽이 같은 프로젝트 행을 잠근다.
- 스택 저장은 쓰기 직전에 프로젝트 행을 배타 잠금(FOR UPDATE)하고 영상 수를 다시 센다
  (`lock_for_change`).
- 영상 행을 만드는 쪽은 프로젝트 설정을 읽기 전에 같은 행을 공유 잠금(FOR KEY SHARE)한다
  (`hold_for_new_video`).
어느 쪽이 먼저 잠그든 뒤에 온 쪽은 앞의 커밋을 기다린 뒤 최신 상태를 읽는다.
대상을 비우는 저장이 먼저 커밋되면 영상을 만드는 쪽은 비워진 설정을 읽는다. 그래서 잠근 뒤 다시 읽은
설정에 기록해야 할 대상이 없으면 영상을 만들지 않는다. 만들면 대상이 빈 채 잠긴 프로젝트가 된다.

잠금 순서는 프로젝트 행이 먼저다. 스택 저장은 프로젝트 행만 잠근다. 영상을 만드는 쪽은
프로젝트 행을 잠근 뒤에 영상·묶음 행을 쓴다. 그래서 두 쪽이 서로를 기다리는 교착이 생기지 않는다.
외부 점검처럼 오래 걸리는 일은 잠그기 전에 끝낸다. 잠금은 트랜잭션이 끝날 때 풀린다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Project, Video
from api_gateway.services.analysis import presets

#: 저장소 대상이 기록되지 않은 프로젝트의 등록을 거부할 때의 오류 코드.
STORAGE_UNSET: Final = "storage_stack_unset"
#: 분석 대상이 기록되지 않은 프로젝트의 등록·분석 요청을 거부할 때의 오류 코드.
ANALYSIS_UNSET: Final = "analysis_stack_unset"


@dataclass(frozen=True)
class LockState:
    """스택 선택의 잠금 여부와 그 근거가 되는 영상 수."""

    #: 프로젝트의 영상 행 수. 삭제한 영상과 올리는 중인 영상도 센다.
    videos: int
    #: `videos` 중 삭제한 영상 수. 영상 목록은 삭제한 영상을 빼고 세므로 화면이 이 수를 따로
    #: 표시한다.
    deleted: int = 0

    @property
    def locked(self) -> bool:
        """영상 행이 하나라도 있으면 True."""
        return self.videos > 0


async def lock_state(db: AsyncSession, project_id: UUID) -> LockState:
    """프로젝트의 잠금 상태를 센다. 첫 영상 행이 생기는 순간부터 잠긴다.

    영상 행은 업로드를 시작할 때 생기고 지워도 남는다(soft delete). 그 영상의 원본·에셋은
    이미 고른 저장소와 MI 프로젝트에 있으므로 행이 하나라도 있으면 선택을 바꾸지 않는다.
    """
    n, deleted = (await db.execute(
        select(func.count(), func.count().filter(Video.deleted_at.is_not(None)))
        .select_from(Video).where(Video.project_id == project_id))).one()
    return LockState(videos=int(n or 0), deleted=int(deleted or 0))


async def lock_for_change(db: AsyncSession, project: Project) -> LockState:
    """스택 선택을 쓰기 직전에 프로젝트 행을 배타 잠금하고 다시 읽은 뒤 영상 수를 센다.

    호출자는 이 함수 뒤에 다시 읽은 `project.settings`와 비교해 쓰고 커밋한다.
    변경을 거부할 때는 롤백해 잠금을 바로 푼다.
    """
    await db.refresh(project, with_for_update=True)
    return await lock_state(db, project.project_id)


async def hold_for_new_video(db: AsyncSession, project_id: UUID) -> Project | None:
    """영상 행을 만들기 전에 프로젝트 행을 공유 잠금하고 최신 값으로 다시 읽어 돌려준다.

    FOR KEY SHARE는 스택 저장의 배타 잠금과만 충돌한다. 영상 등록끼리나 다른 설정 수정은 서로
    기다리지 않는다. 같은 세션이 이미 읽어 둔 프로젝트 객체도 최신 설정으로 덮어쓴다.

    다시 읽은 설정에 기록해야 할 저장소·분석 대상이 없으면 409를 던진다. 등록 라우트의 게이트
    (`project_stack.gate`)는 잠그기 전에 검사한다. 그래서 검사 뒤에 대상을 비우는 저장이 먼저
    커밋될 수 있다.
    판정에는 화면과 같은 `presets.storage_target_missing`·`presets.target_missing`을 쓴다.
    거부할 때는 롤백해 잠금을 바로 푼다.

    Returns:
        잠근 프로젝트. 프로젝트가 없으면 `None`.

    Raises:
        HTTPException: 저장소 대상 기록이 없는 경우(409 storage_stack_unset),
            분석 대상 기록이 없는 경우(409 analysis_stack_unset).
    """
    project = await db.scalar(
        select(Project).where(Project.project_id == project_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if project is None:
        return None
    preset = presets.of_project(project.settings)
    checks = (
        (presets.storage_target_missing, STORAGE_UNSET),
        (presets.target_missing, ANALYSIS_UNSET),
    )
    for missing, code in checks:
        if missing(preset, project.settings):
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, code)
    return project
