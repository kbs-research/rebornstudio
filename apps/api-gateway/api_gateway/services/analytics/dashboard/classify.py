"""대시보드 지표가 함께 쓰는 비율 계산과 판정 조건.

사람 표·처리 능력·사업 KPI가 같은 사람과 같은 작업을 같은 수로 표시하도록
판정을 이 모듈 한 곳에 둔다. 다른 하위 모듈이 이 모듈을 import한다.
이 모듈은 형제 모듈을 import하지 않는다.
"""
from uuid import UUID

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Label, Review, WorkItem


def _approval_base(approved: int, rejected: int) -> int:
    """승인율의 분모(판정 수 = 승인+반려)를 돌려준다. 승인율과 화면의 괄호가 함께 쓴다."""
    return approved + rejected


def _approval_rate(approved: int, rejected: int) -> float | None:
    """승인율(approved / (approved+rejected) * 100)을 계산한다.

    판정된 건이 없으면 아직 검수 전이므로 `None`을 돌려준다. `0%`로 두면 승인이 하나도 없었던
    것으로 읽히고 KPI 임계(80%) 아래의 품질 미달 색으로 표시된다.
    """
    base = _approval_base(approved, rejected)
    return round(approved / base * 100, 1) if base else None


def _rejection_base(submitted: int, approved: int, rejected: int) -> int:
    """반려율의 분모(`max(제출, 승인+반려)`)를 돌려준다. 반려율과 화면의 괄호가 함께 쓴다.

    기간 전에 낸 작업이 기간 안에 판정되거나 제출 기록이 없는 판정이 섞이면 판정 수가
    제출 수보다 크다. 그때 제출로 나누면 100%를 넘으므로 판정 수로 나눈다.
    """
    return max(submitted, approved + rejected)


def _rejection_rate(submitted: int, approved: int, rejected: int) -> float | None:
    """반려율(반려 ÷ 작업량)을 계산한다. 제출이 없으면 `None`을 돌려준다.

    "작업량 대비 반려율"이라서 분모는 `제출`이다. 50건 중 1건이 반려되면 2%다.
    검수 전 제출도 분모에 들어가 초기에는 낮게 나오므로 화면은 `28.6% (2/7)`처럼 분자와 분모를
    함께 표시한다.

    판정된 건(`승인+반려`)을 분모로 쓰면 `approval_rate`와 합이 늘 100%라서 새 정보가 없고,
    값이 검수 진행 속도에 따라 흔들린다.

    Args:
        submitted: 제출 건수(작업량).
        approved: 승인 건수. `rejected`와 함께 분모의 하한을 정하는 데만 쓴다.
        rejected: 반려 건수. 분자다.

    Returns:
        0.0~1.0 비율. 제출도 판정도 없으면 `None`이다(0%와 구분한다).

    Note:
        분모는 `_rejection_base`가 정한다. 판정 수가 제출 수보다 크면 판정 수로 나눈다.
    """
    base = _rejection_base(submitted, approved, rejected)
    return (rejected / base) if base else None


def _rate_fields(submitted: int, approved: int, rejected: int) -> dict[str, float | int | None]:
    """사람 한 명의 승인율·반려율과 각 분모를 행에 실을 꼴로 돌려준다.

    화면은 비율 아래 괄호에 분자와 분모를 쓴다. 화면이 분모를 다시 계산하면 판정 수가 제출 수보다
    큰 행에서 비율과 괄호가 다른 수를 가리키므로 비율을 낸 분모를 함께 돌려준다.
    기대값은 판정 표 `packages/reborn-contracts/labeler-rates.json`에 있다.
    """
    return {
        "approval_rate": _approval_rate(approved, rejected),
        "approval_base": _approval_base(approved, rejected),
        "rejection_rate": _rejection_rate(submitted, approved, rejected),
        "rejection_base": _rejection_base(submitted, approved, rejected),
    }


def _edit_rate(edit_distance: int, draft_chars: int) -> float | None:
    """수정률(편집 거리 합 ÷ AI 초안 글자 수 합 × 100, 소수 1자리)을 계산한다.

    측정한 제출이 없으면 `None`을 돌려준다(0%와 구분한다).
    건별 비율의 평균 대신 합의 비율을 쓴다. 평균을 내면 초안 20자짜리 한 건의 50%와
    400자짜리 한 건의 5%가 같은 무게가 된다. 합의 비율은 수정률의 정의(AI 초안 대비 수정 문자 수
    비율)와 같다. 측정 대상(작업의 첫 제출, 정규화, 편집 거리)은 `work.edit_rate` 한 곳이 정한다.

    단위는 `_approval_rate`와 같은 0~100이다. 초안보다 길게 덧붙이면 100을 넘을 수 있다.
    """
    return round(edit_distance / draft_chars * 100, 1) if draft_chars else None


def _is_reviewer_revision():
    """라벨이 검수자가 검수 중에 넣은 리비전이면 참인 조건을 만든다.

    "수정 후 승인"은 `submitted_by=검수자`로 새 라벨을 만들고 `Review.label_id`가 그 라벨을
    가리킨다. 판정한 검수자와 라벨 제출자가 같으면 검수 중에 생긴 리비전이라서 라벨러의
    제출로 세지 않는다. 표시용 컬럼 없이 이미 있는 두 값의 관계로 판정한다.
    """
    return exists().where(
        and_(Review.label_id == Label.label_id, Review.reviewer_id == Label.submitted_by)
    )


async def _current_loads(db: AsyncSession, project_id: UUID) -> tuple[dict, dict]:
    """지금 보유 중인 작업을 도메인별(라벨링, 검수)로 사용자마다 센다.

    점유를 나타내는 필드가 도메인마다 다르다. 라벨링은 `claimed` 상태의 `claimed_by`, 검수는
    `submitted` 상태의 `review_claimed_by`다(작업 상태는 `submitted`로 유지된다).
    보유에는 끝나는 시각이 없으므로 시각으로 거르지 않는다.
    사람 표(`labeler_performance`)와 `내 작업`이 같은 수를 쓰도록 현재 부하의 정의를 한 곳에 둔다.
    """
    label = {uid: n for uid, n in (await db.execute(
        select(WorkItem.claimed_by, func.count())
        .where(
            WorkItem.project_id == project_id, WorkItem.status == "claimed",
            WorkItem.claimed_by.is_not(None),
        )
        .group_by(WorkItem.claimed_by)
    )).all()}
    review = {uid: n for uid, n in (await db.execute(
        select(WorkItem.review_claimed_by, func.count())
        .where(
            WorkItem.project_id == project_id, WorkItem.status == "submitted",
            WorkItem.review_claimed_by.is_not(None),
        )
        .group_by(WorkItem.review_claimed_by)
    )).all()}
    return label, review
