"""조직·프로젝트·프로젝트 멤버십을 담는 `organizations`·`projects`·`project_members` 테이블."""
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base, TimestampMixin


class Organization(Base, TimestampMixin):
    """기본 조직 한 행. 프로젝트와 자격증명 연결(`connections`)이 이 행에 속한다.

    조직 관리 화면이 없고 서버가 이 행을 찾아 쓴다(`services.org.organization.default_org_id`).
    상수 식 유일 인덱스가 두 번째 행을 막는다.
    """

    __tablename__ = "organizations"
    __table_args__ = (Index("uq_organizations_single", text("(true)"), unique=True),)

    org_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Project(Base, TimestampMixin):
    """프로젝트. 사업 목표와 작업 정책(배정 만료·동시 보유 한도·원본 보관·비식별화)을 담는다."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_project_org_slug"),
        CheckConstraint(
            "status IN ('active','paused','completed','archived')",
            name="ck_project_status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.org_id"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # 프로젝트 작업 가이드(설명·가이드라인·주의사항, 마크다운).
    guide_md: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # 사업 목표
    contract_deadline: Mapped[date | None] = mapped_column(Date)
    target_hours: Mapped[float | None] = mapped_column(Float)
    target_deident_pct: Mapped[float] = mapped_column(Float, nullable=False, default=90.0)

    # 작업 정책
    # 미사용 컬럼. 보유에 끝나는 시각이 없어 읽지 않는다. RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
    default_assignment_ttl_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, server_default=text("24"))
    # 미사용 컬럼. 문장 구간 지정 기능이 없어 읽지 않는다. 값은 고정 기본값 true로 남는다.
    sentence_timecode_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))
    # 미사용 컬럼 둘. 한 사람이 역할마다 한 건씩 맡으므로 동시 보유 한도를 읽지 않는다.
    # RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
    default_max_concurrent_assignments: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    review_max_concurrent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5"))
    #: 미사용 컬럼. 읽는 코드가 없고 DTO·PATCH 허용 키·계약에도 없어 화면에서 켤 수 없다.
    #: 지울 때는 expand/contract 순서로 별도 마이그레이션을 쓴다.
    conversations_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    caption_min_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # 원본 보관 정책. 되돌릴 수 없는 선택이라 컬럼으로 둔다.
    # false(기본): 준비가 끝나면 원본을 어디에도 남기지 않는다. 인코딩 설정이 틀렸거나
    #   비식별화 모델이 좋아져도 다시 만들 수 없어 원본을 다시 올려야 한다.
    # true: 원본을 보관 프리픽스로 옮긴다. 재처리할 수 있는 대신 원본이 클라우드에 남는다.
    # 이 설계의 근거가 개인정보 보호라서 기본값은 false다.
    # 대신 프로젝트를 만들 때 화면이 이 설정을 눈에 띄게 묻는다.
    retain_original: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))

    # 비식별화를 쓰면 True. 기본값은 False다.
    # 켜면 비식별화가 항상 마스터를 만들고 publish가 그 마스터를 분석본으로 확정한다.
    # 그래서 MI는 원본(raw) 대신 비식별 사본만 읽는다.
    deident_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))

    # MAIU 연동
    maiu_workspace_name: Mapped[str | None] = mapped_column(String(100))
    maiu_project_id: Mapped[str | None] = mapped_column(String(100))

    # 확장 정책
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.user_id"))
    # soft delete 시각.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMember(Base):
    """프로젝트 멤버십. 역할과 참여 상태를 담는다."""

    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('project_admin','reviewer','annotator')",
            name="ck_member_role",
        ),
        CheckConstraint(
            "status IN ('pending','active','suspended','left','rejected')",
            name="ck_member_status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    expertise_genres: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    expertise_topics: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # 미사용 컬럼. 멤버별 동시 보유 한도 예외를 읽지 않는다. RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
    max_concurrent_assignments: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 참여 기간. 읽지도 쓰지도 않는다. RebornStudio Pro와 같은 스키마를 유지하려고 컬럼만 둔다.
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # 조기 이탈 정보(status='left'와 사유·시각).
    leave_reason: Mapped[str | None] = mapped_column(Text)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 프로젝트 가이드 확인 시각.
    guide_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
