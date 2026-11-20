"""라벨링 작업 큐의 판정 규칙. 대상·배정·보유·닫기·보유 순서를 정한다.

판정 규칙은 이 모듈 한 곳에만 둔다.
쓰기(`commands`)·조회(`queries`)와 다른 도메인(`analytics`·`media`·`work.review`)은
이 모듈의 조건을 가져다 쓰고 따로 구현하지 않는다.
이 모듈은 같은 패키지의 다른 모듈을 import하지 않는다.
"""
from uuid import UUID

from fastapi import HTTPException, status
from reborn_contracts import RIGHTS_CLEARED
from sqlalchemy import and_, exists

from api_gateway.models import Clip, Project, ProjectMember, User, Video, WorkItem
from api_gateway.models.work import WORK_CLOSED


def _human_workable():
    """사람 큐(라벨링·검수)의 대상 조건을 만든다.

    대상은 닫히지 않은 클립 단위 work_item 중 영상이 삭제되지 않았고 클립이 정상 상태인 작업이다.
    영상 단위 work_item(clip_id IS NULL)은 MAIU 분석 디스패치 대기 표시라서 사람 큐에 넣지 않는다.
    사람 큐에 넣으면 사람이 가져간 순간 status가 바뀌어 디스패치 풀에서도 빠진다.
    영상 삭제는 soft delete(`deleted_at`)라서 작업 행이 남으므로 조건으로 뺀다.

    클립 상태는 작업을 만든 뒤에도 비식별화 신고로 바뀔 수 있어 큐에서 매번 확인한다.
    작업 생성(`ensure_for_clip`)과 같은 조건(`RIGHTS_CLEARED`)을 쓰므로,
    관리자가 신고를 재개해 클립이 정상으로 돌아오면 같은 작업이 큐로 돌아온다.

    호출하는 쿼리마다 `Video` 조인 여부가 달라 상관 서브쿼리로 둔다.
    """
    return and_(
        WorkItem.clip_id.is_not(None),
        WorkItem.status != WORK_CLOSED,
        ~exists().where(
            (Video.video_id == WorkItem.video_id) & Video.deleted_at.is_not(None)
        ),
        exists().where(
            (Clip.clip_id == WorkItem.clip_id) & Clip.rights_status.in_(RIGHTS_CLEARED)
        ),
    )



#: 닫을 수 있는 미완 작업 상태. 승인된 작업은 납품에 남기므로 빼고,
#: 이미 닫힌 작업(`cancelled`)은 다시 닫지 않는다.
_CLOSABLE = ("available", "claimed", "submitted", "rejected")

#: 관리자 확인 표시를 붙이는 반려 횟수. 작업 흐름은 바꾸지 않고 표시만 한다.
REPEATED_REJECTS = 3
#: 관리자 확인 표시를 세는 미완 작업 상태.
_OPEN_FOR_FLAG = ("available", "claimed", "submitted")


def needs_admin_check_where():
    """반려가 `REPEATED_REJECTS`번 이상 쌓인 미완 작업의 SQL 조건을 만든다."""
    return WorkItem.status.in_(_OPEN_FOR_FLAG) & (WorkItem.rejection_count >= REPEATED_REJECTS)



def _claimable_where(project_id: UUID):
    """새로 배정할 수 있는 작업의 조건을 만든다. `next_work`와 대기 수 집계가 함께 쓴다.

    대상은 보유자가 없는 `available` 작업뿐이다. 보유에는 자동 회수가 없으므로
    다른 사람이 보유한 작업은 내주지 않는다.
    `claimed_by IS NULL`인 `claimed`는 MI 요청분이라 이 조건에 들지 않는다.
    조건을 두 곳에 두면 화면의 대기 수가 실제로 배정할 수 있는 수와 달라진다.
    """
    return (WorkItem.project_id == project_id, _human_workable(), WorkItem.status == "available")



def holder_left():
    """작업을 점유한 사용자가 지금 이 프로젝트에 없으면 참인 조건을 만든다.

    활성 멤버 행이 없고 최고관리자도 아닌 사용자를 프로젝트에 없는 사람으로 본다.
    최고관리자는 멤버가 아니어도 작업을 가져가고 제출하므로 빼지 않는다(`authz.require_role`).
    반려(`review.decide`)가 이 조건으로 재작업을 만든 사람 대신 대기열로 돌릴지 정한다.
    `claimed_by`가 비어 있으면 거짓이다.
    """
    return and_(
        WorkItem.claimed_by.is_not(None),
        ~exists().where(
            (ProjectMember.project_id == WorkItem.project_id)
            & (ProjectMember.user_id == WorkItem.claimed_by)
            & (ProjectMember.status == "active")
        ),
        ~exists().where(
            (User.user_id == WorkItem.claimed_by) & (User.global_role == "super_admin")
        ),
    )



def _held_by(project_id: UUID, user_id: UUID):
    """호출한 사용자가 이 프로젝트에서 보유한 라벨링 작업의 조건을 만든다.

    `next_work`가 이어서 열 작업을 찾을 때와 대기 수 응답의 보유 수가 같은 조건을 쓴다.
    보유에는 끝나는 시각이 없다. 제출하거나 비식별화 미흡을 신고할 때까지 보유가 이어진다.
    """
    return (
        WorkItem.project_id == project_id,
        WorkItem.claimed_by == user_id,
        WorkItem.status == "claimed",
    )



def reject_if_not_claimable(project: Project) -> None:
    """프로젝트가 신규 가져오기를 받을 수 없으면 409를 낸다.

    라벨링(`next_work`)과 검수(`next_review`)가 새 작업을 줄 때 함께 쓴다.
    `completed`와 DB에 남은 `archived`는 끝난 프로젝트로 다룬다.
    `paused`는 새 배정만 막는다. 보유한 작업을 다시 여는 일과 제출까지 막으면
    일시정지 중에 보유한 작업을 끝낼 수 없다. 그래서 다음 작업 배정은 보유한 작업을 먼저 찾고,
    새 작업을 줄 때만 이 함수를 부른다.

    Raises:
        HTTPException: 일시정지된 경우(409 project_paused),
            끝난 프로젝트인 경우(409 project_not_active).
    """
    if project.status == "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, "project_paused")
    if project.status in ("archived", "completed"):
        raise HTTPException(status.HTTP_409_CONFLICT, "project_not_active")



def held_order(*, claimed_at, key, rework=None) -> tuple:
    """보유 목록의 정렬식을 만든다. 라벨링·검수 보유 목록과 다음 작업 배정이 이 함수로 정렬한다.

    순서는 재작업 먼저, 그다음 가져온 순이다. 재작업 개념이 없는 목록은 `rework`를 주지 않는다.
    다음 작업은 이 순서의 첫 건을 다시 연다.

    Args:
        claimed_at: 가져온 시각 열.
        key: 순서가 모두 같을 때 결과를 고정하는 기본 키 열.
        rework: 재작업이면 참인 식. 재작업이 없는 목록이면 None.

    Returns:
        `order_by`에 펼쳐 넣을 정렬식.
    """
    head = (rework.desc(),) if rework is not None else ()
    return (*head, claimed_at.asc().nulls_last(), key)



#: 라벨링 보유 작업의 정렬식. 반려로 돌아온 작업이 재작업이다.
HELD_ORDER = held_order(
    rework=WorkItem.rejection_count > 0,
    claimed_at=WorkItem.claimed_at,
    key=WorkItem.work_item_id,
)
