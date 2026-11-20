"""품질 등급 체계 `standing`·`standing_locked`·`honeypots`를 스키마에서 뺀다.

품질 등급(trusted/watch/restricted)·점수·허니팟은 쓰지 않는 기능이다.
도구는 사람을 좋다·나쁘다로 매기지 않고, 수를 보여 주고 정렬하게만 한다.
이 값을 읽고 쓰는 코드는 없다. `Honeypot` ORM 모델도, `standing` 재계산 서비스도 없고,
`test_scripts_import`가 그 심볼을 부르지 않는지 확인한다. 그래서 expand-contract의 contract 단계다.

- `project_members.standing`·`standing_locked`와 CHECK `ck_member_standing`: 컬럼을 지운다.
- `honeypots` 테이블: 참조하는 FK가 없고 행이 0건이라 통째로 지운다.

되돌리기: `downgrade`가 컬럼·CHECK·테이블을 다시 만든다. 데이터는 돌아오지 않는다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

_STANDING_CHECK = "standing IN ('trusted','normal','watch','restricted')"


def upgrade() -> None:
    op.drop_constraint("ck_member_standing", "project_members", type_="check")
    op.drop_column("project_members", "standing_locked")
    op.drop_column("project_members", "standing")
    op.drop_table("honeypots")


def downgrade() -> None:
    op.add_column(
        "project_members",
        sa.Column("standing", sa.String(20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "project_members",
        sa.Column("standing_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_check_constraint("ck_member_standing", "project_members", _STANDING_CHECK)
    op.create_table(
        "honeypots",
        sa.Column("honeypot_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", UUID(as_uuid=True),
                  sa.ForeignKey("work_items.work_item_id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("reference_payload", JSONB, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_honeypots_project", "honeypots", ["project_id"])
