"""클립 진척 요약(승인·진행 중·작업 전)과 역할별 클립 응답.

라벨러에게는 정상 상태의 클립만 보여 주고 상태 구분과 다른 사람의 작업 상태는 싣지 않는다.
비식별화 신고 중이거나 제외된 구간은 작업자에게 노출하지 않는다.
다른 사람의 승인·반려는 그 사람의 기록이라 감춘다.

진척 판정(`clip_progress`)과 역할별 응답(`clip_audience`)은 이 파일에서만 정한다.
클립 목록과 단건 조회가 각자 역할을 확인하면 한 경로에서 가림 처리가 빠진다.
"""
from collections.abc import Collection
from typing import Literal
from uuid import UUID

from reborn_contracts import RIGHTS_CLEARED, ClipDTO, ClipProgress
from sqlalchemy import select

from api_gateway.models import User, WorkItem
from api_gateway.services import authz

#: 클립 응답을 받는 쪽의 구분. `full`은 운영 역할이라 가리지 않는다.
ClipAudience = Literal["labeler", "full"]

#: 가져간 뒤 승인 전인 작업 상태. 라벨링 중·검수 대기·재작업(반려)이 여기 든다.
IN_PROGRESS_WORK = ("claimed", "submitted", "rejected")

#: 승인될 수 없는 클립 상태. 진척 요약에서 뺀다.
EXCLUDED_RIGHTS = "rejected"

def clip_progress(rights_status: str, work_status: str | None) -> ClipProgress | None:
    """클립 한 건의 진척 요약을 정한다. 제외된 클립이면 None을 돌려준다.

    작업 티켓이 없거나 라벨링 대기(`available`)면 `not_started`다.
    종료(`cancelled`)된 티켓도 아무도 작업하지 않는 상태라 `not_started`로 본다.
    """
    if rights_status == EXCLUDED_RIGHTS:
        return None
    if work_status == "approved":
        return "approved"
    if work_status in IN_PROGRESS_WORK:
        return "in_progress"
    return "not_started"


async def clip_audience(db, project_id: UUID, user: User) -> ClipAudience:
    """클립 응답을 어떻게 가릴지 역할로 정한다. super_admin은 가리지 않는다.

    라벨러는 `annotator` 역할이다. 관리자와 검수자는 판정이 업무라서 가리지 않는다.
    """
    if user.global_role == "super_admin":
        return "full"
    role = await authz.my_role(db, project_id, user)
    if role == "annotator":
        return "labeler"
    return "full"


def visible_rights(audience: ClipAudience) -> tuple[str, ...] | None:
    """`audience`에게 보여 줄 클립 상태 목록을 돌려준다. `None`이면 거르지 않는다.

    목록 쿼리가 SQL에서 미리 거를 때 쓴다. 페이지를 자른 뒤에 거르면
    페이지가 덜 차서 화면에 다음 페이지가 없는 것으로 표시된다.
    라벨러에게는 허가 계열 상태만 남긴다.
    """
    if audience == "labeler":
        return RIGHTS_CLEARED
    return None


def redact_clip_for_labeler(dto: ClipDTO, mine: Collection[UUID]) -> ClipDTO | None:
    """라벨러용 클립 응답을 만든다. 신고 중·제외 클립이면 `None`을 돌려준다.

    클립 상태는 `cleared` 하나로 줄인다. `auto_cleared`와 `cleared`는 운영 내부의 구분이다.
    작업 상태와 진척은 호출한 라벨러가 가져간 작업(`mine`)일 때만 남긴다.
    다른 사람의 작업은 아직 아무도 작업하지 않은 클립처럼 보인다.

    Args:
        mine: 이 라벨러가 가져간 작업 티켓 id 집합.
    """
    if dto.rights_status not in RIGHTS_CLEARED:
        return None
    own = dto.work_item_id is not None and dto.work_item_id in mine
    return dto.model_copy(update={
        "rights_status": "cleared",
        "work_status": dto.work_status if own else None,
        "work_item_id": dto.work_item_id if own else None,
        "progress": clip_progress("cleared", dto.work_status if own else None),
    })


def redact_clips_for_labeler(dtos: list[ClipDTO], mine: Collection[UUID]) -> list[ClipDTO]:
    """라벨러용 클립 목록을 만든다. 신고 중·제외 클립을 빼고 나머지를 가린다."""
    out = (redact_clip_for_labeler(d, mine) for d in dtos)
    return [d for d in out if d is not None]


async def my_tickets(db, clip_ids: list[UUID], user: User) -> set[UUID]:
    """주어진 클립 중 `user`가 가져간 작업 티켓 id를 모은다. 라벨러 가림의 `mine` 인자로 쓴다."""
    if not clip_ids:
        return set()
    rows = await db.scalars(
        select(WorkItem.work_item_id).where(
            WorkItem.clip_id.in_(clip_ids), WorkItem.claimed_by == user.user_id,
        )
    )
    return set(rows)
