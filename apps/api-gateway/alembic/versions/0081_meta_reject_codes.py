"""영상 장르 컬럼과 반려 사유 코드 배열을 더하고 기존 값을 백필한다.

바뀌는 것:

- `videos.genre`: 방송사·프로그램명·방영일은 영상 컬럼인데 장르만 컬럼이 없어서, 라벨러가 클립마다
  `payload.meta.genre`에 넣어야 한다. 영상 단위 사실은 영상에 한 번 넣고 그 영상의 클립이
  물려받는다. 이 컬럼으로 네 항목이 모두 영상 컬럼이 된다.
- `reviews.reason_codes`: 반려 사유를 코드 배열로 둔다. 화면 칩 이름을 ` · `로 이어 붙인 자유 문자열을
  `comment`에 넣고 이력 화면이 다시 쪼개면, 어떤 반려 사유가 가장 많은지 셀 수 없다. 자유 입력 문장은 `comment`에 그대로 남는다.

백필 두 가지:

1. 반려 사유: 알려진 칩 이름 조각은 코드로 옮기고, 알려지지 않은 조각은 사유 문장으로 남긴다(지우지 않는다).
   코드가 하나도 나오지 않은 반려는 바꾸지 않는다.
2. 영상 메타: 라벨러가 클립마다 넣던 `payload.meta`를 영상 컬럼으로 올린다.
   영상 컬럼이 비어 있고 그 영상의 작업들이 한 값으로 일치할 때만 올린다.
   값이 갈리면 어느 값이 맞는지 알 수 없으므로 비워 두고, 운영자가 영상 상세에서 정한다.
   이후 납품·품질검수·화면은 영상 값만 읽고 라벨러 입력은 읽지 않는다.

앱 코드를 import하지 않는다: 칩 이름과 코드의 대응표는 `services/reject_reasons.py`에도 있지만 여기에 복사해 둔다.
마이그레이션은 그 시점의 스키마를 기준으로 돌아야 하는데, 앱 코드는 나중에 바뀐다.

`down_revision`은 0080(수정률)이다. 내용은 0080에 의존하지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

#: 칩 이름과 코드의 대응표. 반려 사유 백필용 사본이고 원본은 `services/reject_reasons.BUILTIN`이다.
#: `타임코드 오류`는 라벨러 화면의 `문장 구간`과 같은 항목이라 새 이름과 같은 코드로 옮긴다.
LABEL_TO_CODE: tuple[tuple[str, str], ...] = (
    ("화자명 오류", "speaker_name"),
    ("캡션 부정확", "caption_inaccurate"),
    ("메타 누락", "metadata_missing"),
    ("타임코드 오류", "sentence_range"),
    ("문장 구간 오류", "sentence_range"),
    ("비식별화 미흡", "deident_insufficient"),
    ("태그 부적절", "tag_inappropriate"),
)

#: 코드와 이름의 대응표. 되돌릴 때 사유 문장에 다시 적는 이름이다.
CODE_TO_LABEL: tuple[tuple[str, str], ...] = (
    ("speaker_name", "화자명 오류"),
    ("caption_inaccurate", "캡션 부정확"),
    ("metadata_missing", "메타 누락"),
    ("sentence_range", "문장 구간 오류"),
    ("deident_insufficient", "비식별화 미흡"),
    ("tag_inappropriate", "태그 부적절"),
)


def _values(pairs: tuple[tuple[str, str], ...]) -> str:
    """`VALUES (...)` 목록을 만든다. 값이 상수뿐이고 따옴표가 없어 바인드 대신 리터럴로 쓴다."""
    return ", ".join(f"('{a}', '{b}')" for a, b in pairs)


#: 반려 사유 백필 SQL. `{scope}`는 마이그레이션에서는 빈 문자열이다.
#: 테스트는 같은 DB의 다른 행을 건드리지 않도록 자기 행으로 좁힌다(`AND r.review_id = ANY(:ids)`).
BACKFILL_REVIEWS_SQL = f"""
WITH m(label, code) AS (VALUES {_values(LABEL_TO_CODE)}),
parts AS (
    SELECT r.review_id, btrim(p.part) AS part, p.ord
    FROM reviews r
    CROSS JOIN LATERAL unnest(string_to_array(r.comment, ' · ')) WITH ORDINALITY AS p(part, ord)
    WHERE r.verdict = 'reject'
      AND cardinality(r.reason_codes) = 0
      AND coalesce(r.comment, '') <> ''
      {{scope}}
),
mapped AS (
    SELECT parts.review_id, parts.part, parts.ord, m.code
    FROM parts LEFT JOIN m ON m.label = parts.part
),
codes AS (
    SELECT review_id, array_agg(code ORDER BY first_ord)::varchar(40)[] AS codes
    FROM (
        SELECT review_id, code, min(ord) AS first_ord
        FROM mapped WHERE code IS NOT NULL
        GROUP BY review_id, code
    ) q
    GROUP BY review_id
),
rest AS (
    SELECT review_id,
           string_agg(part, ' · ' ORDER BY ord) FILTER (WHERE code IS NULL AND part <> '') AS rest
    FROM mapped
    GROUP BY review_id
)
UPDATE reviews r
SET reason_codes = codes.codes, comment = rest.rest
FROM codes JOIN rest USING (review_id)
WHERE r.review_id = codes.review_id
"""

#: 영상 텍스트 필드 백필 대상. (payload 키, 컬럼, 길이) 순서이고, 컬럼 길이를 넘는 옛 입력은 자른다.
VIDEO_TEXT_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("broadcaster", "broadcaster", 100),
    ("program", "program_name", 200),
    ("genre", "genre", 100),
)

#: 영상 텍스트 필드 하나를 옛 라벨러 입력으로 채우는 SQL. 입력값이 한 가지로 일치할 때만 채운다.
VIDEO_TEXT_SQL = """
UPDATE videos v SET {col} = left(s.val, {size})
FROM (
    SELECT w.video_id, min(btrim(w.payload->'meta'->>'{key}')) AS val
    FROM work_items w
    WHERE jsonb_typeof(w.payload->'meta') = 'object'
      AND coalesce(btrim(w.payload->'meta'->>'{key}'), '') <> ''
      {scope}
    GROUP BY w.video_id
    HAVING count(DISTINCT btrim(w.payload->'meta'->>'{key}')) = 1
) s
WHERE v.video_id = s.video_id AND coalesce(btrim(v.{col}), '') = ''
"""

#: 방영일 후보를 고르는 SQL. 날짜 해석은 파이썬이 한다.
#: 형식만 맞고 없는 날(`2026-02-30`)을 SQL로 형변환하면 오류가 나서 마이그레이션 전체가 실패한다.
AIRED_AT_CANDIDATES_SQL = """
SELECT w.video_id, min(btrim(w.payload->'meta'->>'air_date')) AS val
FROM work_items w
JOIN videos v ON v.video_id = w.video_id
WHERE jsonb_typeof(w.payload->'meta') = 'object'
  AND coalesce(btrim(w.payload->'meta'->>'air_date'), '') <> ''
  AND v.aired_at IS NULL
  {scope}
GROUP BY w.video_id
HAVING count(DISTINCT btrim(w.payload->'meta'->>'air_date')) = 1
"""

_KST = timezone(timedelta(hours=9))


def parse_air_date(value: str | None) -> datetime | None:
    """라벨러가 넣던 방영일(`<input type=date>`의 `YYYY-MM-DD`)을 KST 자정 시각으로 바꾼다.

    화면은 방영일을 로컬 달력 날짜로 읽으므로 UTC 자정으로 두면 KST에서 하루가 밀린다.
    읽을 수 없는 값은 지어내지 않고 `None`으로 버린다.
    """
    if not value:
        return None
    try:
        d = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=_KST)


def backfill_video_meta(bind, scope: str = "", params: dict | None = None) -> None:
    """영상 메타 네 항목을 옛 라벨러 입력으로 채운다. `scope`는 테스트가 자기 영상으로 좁힐 때 쓴다."""
    for key, col, size in VIDEO_TEXT_FIELDS:
        bind.execute(
            sa.text(VIDEO_TEXT_SQL.format(col=col, size=size, key=key, scope=scope)),
            params or {},
        )
    rows = bind.execute(sa.text(AIRED_AT_CANDIDATES_SQL.format(scope=scope)), params or {}).all()
    for video_id, val in rows:
        at = parse_air_date(val)
        if at is not None:
            bind.execute(
                sa.text("UPDATE videos SET aired_at = :at WHERE video_id = :vid AND aired_at IS NULL"),
                {"at": at, "vid": video_id},
            )


def upgrade() -> None:
    op.add_column("videos", sa.Column("genre", sa.String(100), nullable=True))
    op.add_column(
        "reviews",
        sa.Column(
            "reason_codes",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
    )
    bind = op.get_bind()
    bind.execute(sa.text(BACKFILL_REVIEWS_SQL.format(scope="")))
    backfill_video_meta(bind)


def downgrade() -> None:
    # 컬럼을 지우면 사유가 사라지므로 코드를 이름으로 바꿔 사유 문장 앞에 다시 적는다.
    # 프로젝트가 더한 사유(`custom_N`)의 이름은 프로젝트 설정에서 찾고, 찾지 못하면 코드를 쓴다.
    op.execute(f"""
        WITH m(code, label) AS (VALUES {_values(CODE_TO_LABEL)})
        UPDATE reviews r SET comment = concat_ws(' · ', (
            SELECT string_agg(coalesce(m.label, s.label, u.c), ' · ' ORDER BY u.o)
            FROM unnest(r.reason_codes) WITH ORDINALITY AS u(c, o)
            LEFT JOIN m ON m.code = u.c
            LEFT JOIN LATERAL (
                SELECT e->>'label' AS label
                FROM work_items w
                JOIN projects p ON p.project_id = w.project_id
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(p.settings->'reject_reasons') = 'array'
                         THEN p.settings->'reject_reasons' ELSE '[]'::jsonb END
                ) e
                WHERE w.work_item_id = r.work_item_id AND e->>'code' = u.c
                LIMIT 1
            ) s ON true
        ), nullif(r.comment, ''))
        WHERE cardinality(r.reason_codes) > 0
    """)
    op.drop_column("reviews", "reason_codes")
    op.drop_column("videos", "genre")
