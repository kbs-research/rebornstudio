"""분석 작업 상태를 어댑터와 무관한 단위로 기록하는 `analysis_job` 테이블.

영상당 한 행인 테이블은 클립마다 분석할 때 생기는 여러 상태(예: 39개)를 한 행에 담아야 한다.
그래서 다음 세 가지를 표현하려고 클립 단위까지 담는 이 테이블을 둔다.

- 부분 성공: 39개 중 37개 성공은 완료와 실패 사이의 별도 상태다.
- 클립 단위 재시도: 영상 단위로 재시도하면 둘만 실패해도 서른아홉 개를 다시 돌린다.
- 벤더 중립: 테이블 이름에 벤더가 들어 있으면 스택을 바꿨을 때 이름이 실제와 달라진다.

`clip_id`가 단위를 정한다. `NULL`이면 영상 단위, 값이 있으면 클립 단위다.
두 단위 모두 "이 분석이 어디까지 진행됐나"라는 같은 질문에 답하므로 한 테이블에 둔다.
테이블을 나누면 그 답이 두 곳에 흩어진다.

`status`는 어댑터와 관계없이 같은 뜻으로 쓴다. 화면과 판정이 이 값을 읽는다.
MI의 `registering`·`summarizing` 같은 어댑터 고유 단계는 `stage`에 두고 진단에만 쓴다.
판정이 `stage`를 읽으면 벤더 고유 값이 계약에 들어오므로 두 값을 나눈다.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base

# 어댑터와 무관한 상태 값. 벤더 고유 단계는 `stage`에 둔다.
# `partial`은 일부 클립만 성공한 상태다. `done`으로 합치면 실패한 2개가,
# `failed`로 합치면 성공한 37개가 화면에 보이지 않는다.
AnalysisJobStatus = Literal["pending", "running", "done", "partial", "failed"]


class AnalysisJob(Base):
    """분석 작업 한 건. 영상 단위(`clip_id IS NULL`)이거나 클립 단위다."""

    __tablename__ = "analysis_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','done','partial','failed')",
            name="ck_analysis_job_status",
        ),
        # 같은 (영상·클립·어댑터)에 행이 둘이면 어느 것이 유효한지 알 수 없다.
        # Postgres는 NULL을 서로 다른 값으로 보므로 `NULLS NOT DISTINCT`를 건다.
        # 이 옵션이 없으면 `clip_id IS NULL`인 영상 단위 행이 얼마든지 중복된다.
        UniqueConstraint(
            "video_id", "clip_id", "adapter_key",
            name="uq_analysis_job_unit",
            postgresql_nulls_not_distinct=True,
        ),
        # 러너가 다음에 처리할 행을 고를 때 쓰는 인덱스.
        Index("ix_analysis_job_pick", "status", "created_at"),
        # 회수 스윕이 만료된 running 행을 고를 때 쓰는 인덱스.
        # 리스가 있는 행은 워커로 보낸 일부 행뿐이라 부분 인덱스로 둔다.
        Index("ix_analysis_job_lease", "lease_until",
              postgresql_where=text("lease_until IS NOT NULL")),
        Index("ix_analysis_job_project", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False,
    )
    video_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("videos.video_id", ondelete="CASCADE"), nullable=False
    )
    #: 작업 단위를 정하는 클립. `NULL`이면 영상 단위, 값이 있으면 클립 단위다.
    clip_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("clips.clip_id", ondelete="CASCADE")
    )
    #: 작업을 처리하는 스택의 키. `services/analysis/registry.py`의 키를 쓴다.
    #: 벤더는 테이블 이름 대신 이 값으로 기록한다.
    #: 등록부에 없는 키면 화면이 어댑터의 기능을 읽지 못하므로 등록부에 있는 키만 쓴다.
    adapter_key: Mapped[str] = mapped_column(String(40), nullable=False)

    status: Mapped[AnalysisJobStatus] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    #: 어댑터 고유 단계(MI의 `registering`·`summarizing` 등). 진단에만 쓴다.
    #: 판정이 이 값을 읽으면 벤더 고유 값이 계약에 들어온다.
    stage: Mapped[str | None] = mapped_column(String(40))
    #: 어댑터 쪽 핸들(MI면 asset id). 나머지 벤더 식별자는 `payload`에 둔다.
    external_ref: Mapped[str | None] = mapped_column(Text)

    #: 시도 횟수. 재시도는 이 행 단위로 해서 실패한 클립만 다시 돌린다.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_flight_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)

    #: 펜싱 토큰. 워커로 보낸 작업에만 있다.
    #: 완료 보고가 현재 시도의 것인지 구분한다. 토큰이 없으면 리스 만료로 회수된 뒤
    #: 늦게 도착한 이전 워커의 결과까지 그대로 받는다.
    token: Mapped[str | None] = mapped_column(String(40))
    #: 리스 만료 시각. 워커가 비정상 종료되면 행이 `running`으로 남는다. 회수 스윕이 이 시각이 지난 행을 회수한다.
    #: MI처럼 폴링으로 도는 어댑터는 `NULL`이다.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, default=uuid4)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
