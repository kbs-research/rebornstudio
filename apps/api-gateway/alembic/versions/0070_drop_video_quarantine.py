"""영상 `quarantine` 값을 스키마에서 뺀다. 이 상태를 만드는 코드가 없다.

`videos.copyright_status`와 `rights_decisions.decision`이 `quarantine`을 허용하지만 이 값을 쓰는 코드가 없다.
판정 API(`VideoBulkDecideRequest`)는 `cleared|rejected`만 받고, 나머지 쓰기는 모두 고정 값(`rejected`·`pending`)이다.
MI 디스패치 게이트의 조건 `copyright_status NOT IN ('rejected','quarantine')`은 격리된 영상을 분석에서 빼는 것처럼
읽히지만 `quarantine`인 영상이 없어서 아무 영향이 없다.

작업 격리와 구분한다: `work_items`의 격리(검수 N회 반려 시 자동 격리)는 별개 기능이다.
이름만 같고, 이 마이그레이션이 지우는 것은 영상 저작권 판정의 `quarantine`이다.

되돌리기: `downgrade`가 값을 다시 넣는다.

주의: `quarantine` 행이 있으면 이 마이그레이션은 실패한다. 의도한 동작이다.
조용히 데이터를 바꾸는 것보다 배포를 멈추는 편이 안전하다.
"""
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_STATUS_NEW = "copyright_status IN ('pending','cleared','rejected')"
_STATUS_OLD = "copyright_status IN ('pending','cleared','rejected','quarantine')"
# 클립 판정(`cleared_with_edit`·`legal_review`)도 같은 테이블에 남으므로 영상 상태보다 허용 값이 많다.
_DECISION_NEW = "decision IN ('cleared','rejected','cleared_with_edit','legal_review')"
_DECISION_OLD = ("decision IN ('cleared','rejected','quarantine',"
                 "'cleared_with_edit','legal_review')")


def upgrade() -> None:
    op.drop_constraint("ck_video_copyright_status", "videos", type_="check")
    op.create_check_constraint("ck_video_copyright_status", "videos", _STATUS_NEW)
    op.drop_constraint("ck_rights_decision", "rights_decisions", type_="check")
    op.create_check_constraint("ck_rights_decision", "rights_decisions", _DECISION_NEW)


def downgrade() -> None:
    op.drop_constraint("ck_video_copyright_status", "videos", type_="check")
    op.create_check_constraint("ck_video_copyright_status", "videos", _STATUS_OLD)
    op.drop_constraint("ck_rights_decision", "rights_decisions", type_="check")
    op.create_check_constraint("ck_rights_decision", "rights_decisions", _DECISION_OLD)
