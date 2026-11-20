"""Job 없이 등록된 영상을 Job에 넣는다.

막는 문제: `videos.upload_batch_id`는 nullable이라 `upload_batch_id`를 보내지 않은 호출(스크립트, 직접 API 호출)로
등록된 영상에는 Job이 없을 수 있다. Job 보드(`ingest_batch.list_for_project`)는 `upload_batch_id IS NOT NULL`로
거르므로 그 영상은 보드에 나타나지 않는다. 그러면 목록과 배지는 그 영상을 세는데 보드에는 없어서
한 화면에 "영상 1건"과 "Job 없음"이 함께 표시된다.
지금은 `ensure_batch`가 서버에서 id를 만들어 새 고아가 생기지 않는다. 이 마이그레이션은 남아 있는 고아를 처리한다.

묶는 방법: 원래 어떻게 묶였는지는 남아 있지 않다. 그래서 확실히 아는 기준인 프로젝트, 등록한 날(UTC), 출처로만 묶는다.
이름도 그 사실만 쓴다(`묶음 미상 · YYYY-MM-DD`).
출처는 `metadata->>'registered_from'`으로 나눈다. `register_keys`가 `storage_browse`로 남기고, 이 표시가 없으면 업로드 경로다.

화면 문구와 식별 표식을 나눈다: `note`는 Job 행의 부제로 화면에 그대로 나오므로 사람이 읽을 문장을 넣는다.
downgrade가 이 마이그레이션이 만든 행만 지우려면 별도 표식이 있어야 한다. 그래서 `import_spec`에
`{"backfill": "step-351"}`을 둔다. 이 필드는 폴더째 등록의 잡 사양이고 DTO는 그중 `prefix`만 꺼내므로 화면에 나오지 않는다.
라벨이나 노트로 찾지 않는다. 둘 다 사용자가 화면에서 고칠 수 있고, 고치면 downgrade가 그 행을 찾지 못한다.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# downgrade가 찾는 표식. 화면에 나오지 않고 사용자가 고칠 수 없는 `import_spec`에 둔다.
_MARK = "step-351"
# 부제에 그대로 나오는 문장. 무엇을 근거로 묶었는지만 적는다.
_NOTE = "등록 당시 묶음 기록이 없어 등록일·출처로 묶었습니다"


def upgrade() -> None:
    # 임시 테이블로 id를 한 번만 만든다. CTE로 하면 PG가 인라인하면서 `gen_random_uuid()`를
    # 참조마다 다시 평가할 수 있다. 그러면 INSERT와 UPDATE가 서로 다른 id를 쓴다.
    op.execute("""
        CREATE TEMP TABLE _orphan_batch ON COMMIT DROP AS
        SELECT gen_random_uuid() AS batch_id,
               g.project_id, g.day, g.src, g.created_by
          FROM (
            SELECT v.project_id,
                   (v.created_at AT TIME ZONE 'UTC')::date AS day,
                   CASE WHEN v.metadata->>'registered_from' = 'storage_browse'
                        THEN 'ncp' ELSE 'upload' END       AS src,
                   min(v.uploaded_by::text)::uuid          AS created_by
              FROM videos v
             WHERE v.upload_batch_id IS NULL
             GROUP BY 1, 2, 3
          ) g
    """)
    op.execute(f"""
        INSERT INTO ingest_batches
               (batch_id, project_id, created_by, source, label, note,
                import_spec, created_at)
        SELECT batch_id, project_id, created_by, src,
               '묶음 미상 · ' || to_char(day, 'YYYY-MM-DD'),
               '{_NOTE}',
               jsonb_build_object('backfill', '{_MARK}'),
               day::timestamptz
          FROM _orphan_batch
    """)
    op.execute("""
        UPDATE videos v
           SET upload_batch_id = b.batch_id
          FROM _orphan_batch b
         WHERE v.upload_batch_id IS NULL
           AND v.project_id = b.project_id
           AND (v.created_at AT TIME ZONE 'UTC')::date = b.day
           AND CASE WHEN v.metadata->>'registered_from' = 'storage_browse'
                    THEN 'ncp' ELSE 'upload' END = b.src
    """)


def downgrade() -> None:
    # 영상의 묶음 연결을 먼저 끊는다. FK가 걸려 있어 묶음부터 지우면 삭제가 막힌다.
    op.execute(f"""
        UPDATE videos SET upload_batch_id = NULL
         WHERE upload_batch_id IN (
            SELECT batch_id FROM ingest_batches
             WHERE import_spec->>'backfill' = '{_MARK}'
         )
    """)
    op.execute(
        f"DELETE FROM ingest_batches WHERE import_spec->>'backfill' = '{_MARK}'")
