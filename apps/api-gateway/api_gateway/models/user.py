"""전역 사용자(이메일 로그인)와 관리자가 발급한 비밀번호 재설정 토큰을 담는 `users`·`password_reset_tokens` 테이블."""
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """전역 사용자. 프로젝트별 역할은 `ProjectMember`가 따로 가진다."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("global_role IN ('super_admin','user')", name="ck_user_global_role"),
        CheckConstraint(
            "approval_status IN ('active','suspended')",
            name="ck_user_approval_status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    global_role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")

    # 인증
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 비밀번호를 마지막으로 바꾸거나 재설정한 시각. 이 시각 이전에 발급한 access token은 무효다.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 계정 상태. 계정은 최고관리자가 활성으로 만들고, 막을 때는 정지한다.
    # `approved_by`·`approved_at`은 계정을 만든 사람과 시각이다.
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    approved_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 프로필
    avatar_url: Mapped[str | None] = mapped_column(Text)
    # 시작 안내를 본 시각. 읽지도 쓰지도 않는다. RebornStudio Pro와 같은 스키마를 유지하려고 컬럼만 둔다.
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 개인정보(선택 입력). 가입·프로필에서 입력하고 운영 연락에 쓴다. 선택 동의 시각이 있을 때만 저장한다.
    phone_number: Mapped[str | None] = mapped_column(String(30))
    birth_date: Mapped[date | None] = mapped_column(Date)
    address: Mapped[str | None] = mapped_column(Text)
    # 필수 항목 수집·이용 동의 시각(/privacy 약관).
    # None이면 업무 API를 쓸 수 없다. 동의 화면에서 동의한 뒤에 쓸 수 있다.
    privacy_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 선택 항목(전화번호·생년월일·주소) 수집·이용 동의 시각.
    # None이면 선택 항목을 저장하지 않는다. 철회하면 선택 항목을 지우고 None으로 되돌린다.
    optional_privacy_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(Base):
    """최고관리자가 발급한 1회용 비밀번호 재설정 토큰.

    토큰은 sha256 해시로만 저장한다. 그래서 서버도 발급한 링크를 다시 읽을 수 없다.
    DB를 읽을 수 있는 사람도 해시로는 다른 사람의 계정에 들어갈 수 없다.
    화면은 발급 응답에서 링크를 한 번만 표시한다. 다시 보려면 재발급한다.

    사용자당 한 행이다(PK가 user_id). 새로 발급하면 행을 덮어써 이전 토큰이 무효가 된다.
    토큰을 쓰면 행을 지우지 않고 `used_at`을 적는다. 그래야 링크를 다시 연 사람에게
    "이미 쓴 링크"와 "만료된 링크"를 구분해 알릴 수 있다.

    누가 누구에게 언제 발급했는지는 `domain_events`(`user.password_reset_issued`)에 남는다.
    이 테이블은 사람마다 마지막으로 발급한 토큰 하나만 담는다.
    """

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: 토큰의 sha256 hex. 확인할 때 평문을 비교하지 않고 이 값으로 조회한다.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 발급한 최고관리자. 링크를 사람이 전달하므로 누가 발급했는지가 감사의 출발점이다.
    #: 빈 값은 서버 명령(`api_gateway.cli.recover_admin`)으로 발급한 토큰이다.
    issued_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: 토큰으로 비밀번호를 바꾼 시각. 값이 있으면 다시 쓸 수 없다.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
