"""라벨링·검수 작업 큐와 캡션 품질검수 실행 이력을 담는 `work_items`·`caption_qc_runs` 테이블.

작업은 클립 단위(`clip_id`)다. `clip_id`가 NULL인 행은 영상 단위 작업이다.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base, TimestampMixin

# 작업 상태.
WorkItemStatus = Literal[
    "available", "claimed", "submitted", "approved", "rejected", "cancelled",
]

#: 종료된 작업의 상태 값. 더는 사람에게 배정하지 않고 집계 모집단에서도 뺀다.
#: 비식별화 신고로 제외된 클립과 삭제된 영상의 미완료 작업이 이 상태가 된다. 큐에 그대로 두면 납품에서 빠질 클립에 사람 시간이 계속 든다.
#: 행은 지우지 않고 상태로 남긴다. `submitted`·`rejected`까지 간 작업에는 사람이 쓴 시간이 들어 있어서,
#: 지우면 누가 무엇을 했는지가 감사 기록 한 줄로만 남는다.
#: 집계는 모집단(`analytics.stats._HUMAN_WORK`)에서 이 상태를 빼서 부풀지 않게 한다.
#: `approved` 작업은 이 상태로 바꾸지 않는다. 승인분은 납품에 남긴다.
WORK_CLOSED = "cancelled"

#: 캡션 품질검수 결과. 사람의 판정인 `status`와 별개인 기계의 판정이다.
#: `pending`은 검사를 아직 실행하지 않았거나 호출이 실패했다는 뜻이다. `held`만 납품을 막는다.
CaptionQcStatus = Literal["pending", "passed", "held"]


class WorkItem(Base, TimestampMixin):
    """라벨링·검수 작업 한 건. 라벨러 claim과 검수자 claim을 따로 둔다."""

    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available','claimed','submitted','approved','rejected','cancelled')",
            name="ck_work_status",
        ),
        CheckConstraint(
            "qc_status IN ('pending','passed','held')",
            name="ck_work_qc_status",
        ),
        # 유일성은 UniqueConstraint 대신 마이그레이션 0011의 부분 인덱스가 보장한다
        # (clip_id IS NULL이면 영상별 유일, clip_id가 있으면 클립별 유일).
    )

    work_item_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    video_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False
    )
    # 작업 대상 클립. reconciler가 채운다. NULL이면 영상 단위 작업이다.
    clip_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("clips.clip_id", ondelete="CASCADE")
    )
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[WorkItemStatus] = mapped_column(String(20), nullable=False, default="available")
    #: 캡션 품질검수 결과. 기본값이 `pending`이고 검사 전에 승인된 작업도 `pending`이다.
    #: 그래서 납품 게이트는 `!= 'held'`로 걸어야 한다.
    #: `== 'passed'`로 걸면 이미 승인된 작업이 모두 빠진다.
    qc_status: Mapped[CaptionQcStatus] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    claimed_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 사람 작업의 보유에는 끝나는 시각이 없어 비워 둔다. MI 요청분 표시(`maiu.service`)만 값을 쓴다.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 검수자의 배타 claim(라벨러 claimed_by와 별개). 판정이나 신고 때까지 이어진다.
    review_claimed_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    review_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 미사용 컬럼. 검수 보유에도 끝나는 시각이 없다.
    review_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 검수 초안. 판정 전까지 검수자가 작성한 내용(지적 문장·반려 사유·수정 후 승인 캡션)을 담는다.
    # 서버에 두어서 탭이 닫히거나 access 토큰이 끊겨도 고른 지적 문장과 고친 캡션이 남는다.
    # 라벨러의 `draft`와 필드를 나눈다. 한 필드를 둘이 쓰면 누구의 초안인지 구분할 수 없다.
    # 판정하면 비운다. 신고로 검수 claim을 풀 때도 비워서 다음 검수자가 다른 사람의 초안을
    # 물려받지 않게 한다.
    review_draft: Mapped[dict | None] = mapped_column(JSONB)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 배정 정렬 점수. 쓰지도 읽지도 않는다. RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 제출 전 임시저장(제출 payload와 분리, 30초마다 자동저장).
    # `none_as_null`: 회수할 때 draft=None을 JSON 'null' 대신 SQL NULL로 저장해 임시저장을 완전히 지운다.
    draft: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


#: 검사를 실행한 시점. 같은 작업을 제출 직후와 승인 직후에 두 번 검사한다.
#: `recheck`는 관리자가 보류 화면에서 누른 `다시 검사`다.
CaptionQcTrigger = Literal["submit", "approve", "recheck"]
#: 납품 게이트(`WorkItem.qc_status`)를 정하는 검사 시점.
#: `recheck`는 승인본을 다시 검사하므로 승인 실행과 같이 취급한다.
#: 러너의 게이트 갱신과 납품 쪽의 최신 실행 조회(`export_service`)가 이 튜플을 함께 쓴다.
#: 한쪽만 `approve`를 기준으로 삼으면 다시 검사로 보류가 풀린 건을 납품 경고가 옛 승인 실행 결과로 표시한다.
CAPTION_QC_GATE_TRIGGERS: tuple[str, ...] = ("approve", "recheck")
#: 품질검수 판정 3분류. 납품 게이트(`WorkItem.qc_status`)는 이 값에서 정해진다.
CaptionQcVerdict = Literal["pass", "needs_review", "reject_suggested"]


class CaptionQcRun(Base):
    """캡션 품질검수 실행 1회. 실행마다 행을 쌓는다.

    검수자는 '수정 후 승인'으로 텍스트를 고친다. 그 전후 비교가 이 기능의 목적이라서 이력으로 남긴다.
    한 행에 덮어쓰면 "제출본은 통과했는데 검수자가 고친 뒤 걸렸다"를 볼 수 없다.

    규칙만으로 판정한 실행은 모델을 부르지 않으므로 `model`·`prompt_hash`는 nullable이다.
    모델을 부르지 않은 실행은 빈 문자열 대신 NULL로 기록해 빈 값과 구분한다.
    """

    __tablename__ = "caption_qc_runs"
    __table_args__ = (
        CheckConstraint("trigger IN ('submit','approve','recheck')", name="ck_qc_run_trigger"),
        CheckConstraint(
            "verdict IN ('pass','needs_review','reject_suggested')", name="ck_qc_run_verdict"
        ),
    )

    run_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    work_item_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger: Mapped[CaptionQcTrigger] = mapped_column(String(20), nullable=False)
    verdict: Mapped[CaptionQcVerdict] = mapped_column(String(20), nullable=False)
    #: 판정 근거 목록: `[{check, severity, message, sentence_id?}]`.
    #: 문장 지적에는 `sentence_id`를 붙여 검수자의 문장 트리아지와 같은 식별자로 맞춘다.
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    adapter_key: Mapped[str] = mapped_column(String(50), nullable=False)
    #: 실제로 실행한 검사 단계 목록: `[{key, status, reason?, cause?}]`.
    #: `findings`는 걸린 항목만 담아서 통과와 미실행을 구분할 수 없다.
    #: `None`은 단계 기록이 없는 실행이다.
    #: 빈 목록으로 채우면 "하나도 실행하지 않았다"로 잘못 표시된다.
    stages: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: 이 실행에 걸린 시간(ms).
    #: `None`은 소요 시간을 측정하지 않은 실행이다.
    #: 0으로 채우면 즉시 끝난 것으로 보여 평균이 낮아진다.
    #: 규칙만 도는 실행은 1초 안에 끝나 초 단위로는 모두 0이 되므로 밀리초로 잰다.
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
