"""조직 단위 자격증명 레지스트리인 `connections` 테이블.

같은 클라우드 계정의 자격증명은 여러 프로젝트가 함께 쓰므로 조직 단위로 둔다.
운영자가 프리셋에 필요한 값(엔드포인트·버킷·자격증명)을 여기에 채운다.

시크릿은 암호문으로만 저장한다. `secret_ciphertext`는 `services/crypto.encrypt`가 만든
Fernet 암호문이고 평문은 DB에 없다. DB가 유출돼도 시크릿이 유출되지 않도록 복호화 키는 env에 둔다.
`NULL`은 아직 시크릿을 넣지 않았다는 뜻이고 빈 시크릿과 다르다.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base


class Connection(Base):
    """조직에 속한 자격증명 연결 하나."""

    __tablename__ = "connections"
    __table_args__ = (
        # 한 조직 안에서 이름은 유일하다. 프리셋과 프로젝트가 이름으로 연결을 가리키기 때문이다.
        UniqueConstraint("org_id", "name", name="uq_connection_org_name"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.org_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: 프로바이더(`s3`·`ncp`·`gcs`·`maiu`·LLM 공급자). 계약 `ConnectionProvider`와 같은 값이어야 한다.
    #: 값 검사는 API 계약(Literal)이 한다.
    #: 프로바이더가 늘 때마다 마이그레이션하지 않으려고 DB CHECK는 두지 않는다.
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: 비밀이 아닌 설정값(엔드포인트·버킷·region·workspace 등).
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: 시크릿의 Fernet 암호문. `NULL`은 시크릿 미설정이며 빈 시크릿과 다르다.
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: 마지막 점검 결과. `NULL`은 아직 점검하지 않았다는 뜻이다(정상과 구분한다).
    #: 주기 점검(`worker_io.tasks.check_connections`)과 사람이 누른 `테스트`가 함께 채운다.
    #: 현재 상태 하나만 둔다. 문제가 시작된 시점은 `domain_events`(`connection.health_changed`)에
    #: 상태가 바뀐 시점으로 남는다.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(10))
    last_detail: Mapped[str | None] = mapped_column(Text)
    #: 원격 서버 점검이 확인한 서버 기능. 원격 서버를 점검하지 않아 쓰지 않는다.
    #: RebornStudio Pro와 같은 스키마를 유지하려고 컬럼을 남긴다.
    last_capabilities: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id")
    )
