"""준비 단계의 시도 이력을 담는 `media_stage_runs` 테이블.

상태가 바뀔 때마다 행을 추가만 하는 이력 테이블이다.
진행 판정은 상태 컬럼(`transcode_status`·`analysis_job.status` 등)이 한다.
그래서 이 테이블에 기록이 빠져도 처리는 그대로 진행되고 관측 정보만 빠진다.

상태 컬럼과 `extra`는 현재 값만 남긴다. 이 테이블은 "트랜스코딩은 평균 몇 분인가",
"이 영상은 왜 3번 실패했나" 같은 질문에 답할 기록을 남긴다. GPU를 쓰면 소요 시간과 재시도 횟수가 곧 비용이다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 준비 단계 이름. prep_stage 축의 기계 단계와 1:1로 대응한다.
# DB CHECK(`ck_msr_stage`)는 'deident'와 'scene_split'도 허용하지만 이 Literal에는 없다.
MediaRunStage = Literal["upload", "probe", "transcode", "publish", "register", "analyze", "summarize"]
# 시도 한 건의 상태.
MediaRunStatus = Literal["running", "ok", "failed"]


class MediaStageRun(Base):
    """준비 단계 시도 한 건. 같은 단계를 다시 시도하면 `attempt`를 올린 새 행을 만든다."""

    __tablename__ = "media_stage_runs"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('upload','probe','transcode','publish','register',"
            "'scene_split','analyze','summarize','deident')",
            name="ck_msr_stage",
        ),
        CheckConstraint("status IN ('running','ok','failed')", name="ck_msr_status"),
        # 타임라인 조회(영상별 최신순)와 소요 통계(단계·상태별)에 각각 쓰는 인덱스.
        Index("ix_msr_video_id", "video_id", "id"),
        Index("ix_msr_stage_status", "stage", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    # 같은 단계의 시도 순번. 같은 단계가 몇 번 다시 시도됐는지 이 값으로 센다.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # 부가 정보(일시 오류 여부·산출 스펙 등). 스키마를 늘리지 않고 여기에 담는다.
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
