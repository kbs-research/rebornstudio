"""내보내기에 패키지를 올린 버킷(`storage_bucket`)을 더한다.

프로젝트마다 저장소 버킷을 고를 수 있어서 패키지 키(`manifest_s3_key`)만으로는 어느 버킷에
올렸는지 알 수 없다. 내려받기 주소는 이 버킷으로 서명한다.

확장만 한다. 기존 행은 비워 둔다. 그 패키지는 플랫폼 기본 버킷에 올렸고, 읽는 쪽이 비어 있으면
기본 버킷을 쓴다. 이전 코드는 이 컬럼을 읽지도 쓰지도 않는다.
되돌리기: 컬럼을 지운다. 프로젝트 버킷에 올린 패키지는 기본 버킷으로 서명되어 내려받기가 실패한다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """`exports.storage_bucket` 컬럼을 더한다."""
    op.add_column("exports", sa.Column("storage_bucket", sa.Text(), nullable=True))


def downgrade() -> None:
    """`exports.storage_bucket` 컬럼을 지운다."""
    op.drop_column("exports", "storage_bucket")
