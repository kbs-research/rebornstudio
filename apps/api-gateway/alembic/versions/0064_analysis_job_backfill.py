"""`maiu_job_state`의 행을 `analysis_job`으로 백필한다.

파이프라인은 `analysis_job`에 쓴다. 0063 뒤에 바뀐 행은 미러링 코드가 이미 옮겼지만,
그 뒤로 한 번도 바뀌지 않은 행은 새 테이블에 없다. 옮기지 않으면 그 영상들의 분석 이력이 화면에서 사라진다.

`ON CONFLICT DO NOTHING`이라 여러 번 돌려도 안전하다. 미러링 코드가 이미 옮긴 행은 새 값이 더 최신이므로 덮지 않는다.

옛 테이블은 지우지 않는다. 되돌릴 곳으로 남기고 0093이 지운다.
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

# MI 단계를 중립 상태로 바꾸는 표. `services/analysis/status_map.py`의 표와 같아야 한다.
# 나중에 앱 코드가 바뀌어도 이 마이그레이션은 그대로 돌아야 하므로 앱 코드를 import하지 않고 SQL로 다시 적는다.
_NEUTRAL_SQL = """
    CASE status
        WHEN 'pending'     THEN 'pending'
        WHEN 'done'        THEN 'done'
        WHEN 'failed'      THEN 'failed'
        ELSE 'running'
    END
"""


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO analysis_job (
            project_id, video_id, clip_id, adapter_key, status, stage, external_ref,
            attempt, deferred, in_flight_count, error_msg, payload, trace_id,
            created_at, updated_at
        )
        SELECT
            project_id, video_id, NULL, 'maiu',
            {_NEUTRAL_SQL}, status, maiu_asset_id,
            retry_count, deferred, in_flight_count, error_msg,
            -- 벤더 식별자 나머지는 payload 로. 원래 payload 도 잃지 않게 합친다.
            COALESCE(payload, '{{}}'::jsonb)
                || jsonb_build_object('maiu_analysis_id', maiu_analysis_id),
            trace_id, created_at, updated_at
        FROM maiu_job_state
        ON CONFLICT ON CONSTRAINT uq_analysis_job_unit DO NOTHING
    """)


def downgrade() -> None:
    # 백필은 되돌리지 않는다. 옛 테이블이 그대로 있어 원본이 남아 있고,
    # 지우면 백필 뒤에 파이프라인이 새로 쓴 행까지 함께 사라진다.
    pass
