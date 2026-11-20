"""폴더째 등록을 서버 작업으로 만든다. ingest_batches에 가져오기 작업 상태를, browse 매칭에 인덱스를 더한다.

브라우저가 버킷의 모든 페이지를 돌며 키를 모아 등록하면 파일이 수만 개인 폴더는 수백 번 왕복하고,
탭을 닫으면 멈추며, 중간에 실패하면 처음부터 다시 해야 한다.
그래서 API는 묶음 행에 작업 사양(import_spec)만 남기고, worker beat가 서버에서 버킷을 순회하며 등록한다.
DB를 큐로 쓰는 방식은 transcode와 같다. 묶음 행이 진행 상태를 그대로 보여 준다.

browse의 "이미 등록됨" 매칭은 등록할 때 저장하는 extra->>'key'에 표현식 인덱스를 걸고 정확 일치로 찾는다.
페이지마다 프로젝트 전체 URI를 Python으로 훑으면 비용이 O(파일 × 영상)이다.

Revision ID: 0046
Revises: 0045
"""
import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 작업 사양(prefix·정책·근거·메타)은 잡 페이로드라서 컬럼 5개로 나누지 않고 JSONB 하나에 둔다.
    op.add_column("ingest_batches", sa.Column("import_spec", sa.dialects.postgresql.JSONB()))
    op.add_column("ingest_batches", sa.Column("import_status", sa.String(20)))
    op.add_column("ingest_batches", sa.Column("import_error", sa.Text()))
    op.create_check_constraint(
        "ck_ingest_batch_import_status",
        "ingest_batches",
        "import_status IS NULL OR import_status IN ('pending','running','done','failed')",
    )
    # beat가 폴링할 대상만 고르는 부분 인덱스. 대부분의 묶음은 NULL이라 인덱스가 거의 커지지 않는다.
    op.create_index(
        "ix_ingest_batches_import_pending",
        "ingest_batches",
        ["import_status"],
        postgresql_where=sa.text("import_status IN ('pending','running')"),
    )
    # browse의 "이미 등록됨" 정확 일치 매칭. storage에서 등록한 영상은 metadata->>'key'에 원본 키가
    # 있다(ORM 속성 이름은 extra, 실제 컬럼 이름은 metadata).
    op.create_index(
        "ix_videos_extra_source_key",
        "videos",
        ["project_id", sa.text("(metadata ->> 'key')")],
        postgresql_where=sa.text("(metadata ->> 'key') IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_videos_extra_source_key", table_name="videos")
    op.drop_index("ix_ingest_batches_import_pending", table_name="ingest_batches")
    op.drop_constraint("ck_ingest_batch_import_status", "ingest_batches")
    op.drop_column("ingest_batches", "import_error")
    op.drop_column("ingest_batches", "import_status")
    op.drop_column("ingest_batches", "import_spec")
