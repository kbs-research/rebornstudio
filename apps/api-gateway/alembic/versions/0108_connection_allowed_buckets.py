"""저장소 연결 설정에 지금 쓰는 버킷을 허용 버킷 목록(`allowed_buckets`)으로 채운다.

## 무엇이 바뀌나

프로젝트 관리자는 저장소 연결이 허용한 버킷만 프로젝트 저장소로 고르고 탐색·등록한다
(`services/stacks/project_storage.require_allowed`). 허용 범위는 연결 설정의 `bucket`과
`allowed_buckets` 목록의 합이다. 목록이 없으면 `bucket` 하나만 허용한다.
스키마는 바꾸지 않는다. 연결 설정 JSON에 키 하나를 더한다.

## 이행

저장소 연결(`ncp`) 중 `allowed_buckets` 키가 없는 연결에 목록을 채운다. 목록에는 그 연결 설정의
`bucket`과, 그 연결을 저장소 대상으로 고른 프로젝트(`settings.stack.storage_connection_id`)의
`settings.stack.bucket`을 모은다. 지운 프로젝트의 기록도 넣는다. 채울 버킷이 하나도 없는
연결은 건드리지 않는다. 채운 연결마다 `connection.updated` 감사 기록을 남긴다.
`s3` 연결은 프로젝트 저장소로 고를 수 없어 채우지 않는다.

배포 직후에도 기존 프로젝트가 쓰는 버킷이 모두 허용 범위 안에 있으므로 탐색·등록이 멈추지 않는다.
이미 키가 있는 연결은 사람이 정한 값이라 바꾸지 않는다. 그래서 다시 돌려도 결과가 같다.

## 되돌리기

채운 목록을 지우지 않는다. 사람이 나중에 고친 값과 구분할 수 없고, 이전 코드는 이 키를 읽지 않는다.
0093(RebornStudio Pro와 같은 스키마)까지 되돌려도 스키마는 같다. RebornStudio Pro도 같은 키를 같은 뜻으로 읽는다.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None

#: 허용 버킷 목록을 두는 연결 설정 키.
KEY = "allowed_buckets"


def fill(bind, only: list[str] | None = None) -> list[str]:
    """허용 버킷 목록이 없는 저장소 연결에 그 연결의 버킷과 프로젝트들이 쓰는 버킷을 채운다.

    `only`를 주면 그 연결 id만 본다. 테스트가 다른 연결을 건드리지 않고 이행을 돌릴 때 쓴다.

    Returns:
        목록을 채운 연결 id.
    """
    query = """
        SELECT c.id::text AS id,
               ARRAY(
                   SELECT DISTINCT b FROM (
                       SELECT btrim(c.config->>'bucket') AS b
                       UNION ALL
                       SELECT btrim(p.settings->'stack'->>'bucket')
                       FROM projects p
                       WHERE p.settings->'stack'->>'storage_connection_id' = c.id::text
                   ) used
                   WHERE coalesce(b, '') <> ''
                   ORDER BY b
               ) AS buckets
        FROM connections c
        WHERE c.provider = 'ncp'
          AND NOT (coalesce(c.config, '{}'::jsonb) ? :key)
    """
    params: dict = {"key": KEY}
    if only is not None:
        query += " AND c.id::text = ANY(:only)"
        params["only"] = list(only)
    filled: list[str] = []
    for row in bind.execute(sa.text(query), params).mappings().all():
        buckets = list(row["buckets"] or [])
        if not buckets:
            continue
        bind.execute(sa.text(
            "UPDATE connections SET config = coalesce(config, '{}'::jsonb) "
            "|| jsonb_build_object(CAST(:key AS text), CAST(:v AS jsonb)) WHERE id::text = :id"
        ), {"key": KEY, "v": json.dumps(buckets), "id": row["id"]})
        bind.execute(sa.text(
            "INSERT INTO domain_events (event_id, event_type, target_kind, target_id, payload, kind) "
            "VALUES (gen_random_uuid(), 'connection.updated', 'connection', CAST(:id AS uuid), "
            "CAST(:p AS jsonb), 'audit')"
        ), {"id": row["id"], "p": json.dumps({
            "config_keys_changed": [KEY],
            "bucket_changes": {KEY: {"before": None, "after": buckets}},
            "migration": revision,
        })})
        filled.append(row["id"])
    return filled


def upgrade() -> None:
    """허용 버킷 목록이 없는 저장소 연결 전부에 목록을 채운다(`fill`)."""
    fill(op.get_bind())


def downgrade() -> None:
    """채운 목록을 그대로 둔다. 이전 코드는 이 키를 읽지 않는다."""
