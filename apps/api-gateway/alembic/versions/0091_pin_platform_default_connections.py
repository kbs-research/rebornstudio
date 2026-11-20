"""시스템 기본 연결로 해석하던 빈 값을 그 연결 id로 고정하고 두 설정 키를 지운다.

바뀌는 것: 자격증명의 출처가 프로젝트가 고른 연결 하나가 된다.
비어 있으면 시스템 기본 연결로 읽던 필드를 시스템 설정의 두 기본 연결(`storage_connection_id`=S,
`analysis_connection_id`=A)로 채운다.

- 프로젝트 `settings.stack`: 버킷이 있고 저장소 연결이 비면 S로, 연결이 있고 버킷이 비면 그 연결 설정의 버킷으로 채운다.
  MI 프로젝트가 있고 분석 연결이 비면 A로, 워크스페이스가 비면 그 연결 설정의 워크스페이스로 채운다.
  영상이 있는데 기록이 없으면 S와 그 버킷, A와 그 워크스페이스·MI 프로젝트로 기록한다
  (MI 프로젝트는 `ncp-mi` 방식만).
- 영상 `metadata.storage`: 기록이 없으면 S와 그 버킷으로, 연결이 비면 S로, 버킷이 비면 그 연결의 버킷으로 채운다.
- 버킷 기록이 비어 기본 버킷으로 읽던 산출물: 포스터(`poster_thumbnail_bucket`), 클립 썸네일
  (`scene_thumbnail_bucket`), 납품 패키지(`exports.storage_bucket`), 문의 첨부(`attachments[].bucket`)는 S의 버킷으로 채운다.
- MI 분석 잡 `payload.maiu`: 기록이 없으면 A와 그 워크스페이스·MI 프로젝트로, 연결이 비면 A로,
  워크스페이스·MI 프로젝트가 비면 그 연결 설정의 값으로 채운다.
- 두 설정 키를 지우고, 지운 값과 채운 행 수를 감사 기록 `system.platform_defaults_retired`에 남긴다.

라우팅이 바뀌지 않는 이유: 채우는 값은 이전 코드가 빈 필드를 해석하던 값과 같다.
빈 연결은 기본 연결의 키였고, 빈 버킷은 기본 연결의 버킷이었다.
그래서 이 마이그레이션 뒤에는 이전 코드와 새 코드가 같은 계정과 버킷을 쓴다.

기본 연결이 없는 DB: S·A가 없으면(서버 환경 변수로 자격증명을 받던 DB) 채울 값이 없어 아무것도 바꾸지 않고 키만 지운다.
그런 DB는 사람이 고른 연결 id를 `pin_all`에 넘겨 같은 방식으로 채운다.

되돌리기: 감사 기록에 남긴 두 값을 시스템 설정에 다시 쓴다. 채운 값은 되돌리지 않는다.
이전 코드도 명시한 연결과 버킷을 그대로 따르므로 되돌릴 필요가 없다.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None

#: 지우는 시스템 설정 키. 저장소 기본 연결, 분석 기본 연결 순이다.
STORAGE_KEY = "storage_connection_id"
ANALYSIS_KEY = "analysis_connection_id"
#: 지운 값을 남기는 감사 기록의 종류.
EVENT_TYPE = "system.platform_defaults_retired"

#: 저장소 대상이 필요 없는 분석 방식의 SQL 목록. 이 밖의 값(비었거나 알 수 없는 키 포함)은 NCP 저장소를 쓴다.
_NO_STORAGE_PRESETS = "('aws-bedrock')"
#: MI 분석 대상이 필요 없는 분석 방식의 SQL 목록. 이 밖의 값은 `ncp-mi`로 읽힌다.
_NO_ANALYSIS_PRESETS = "('ncp-shots', 'aws-bedrock')"


def _connection(bind, conn_id: str | None, provider: str) -> dict | None:
    """연결 id의 설정을 읽는다. 없거나 서비스가 다르면 `None`이다."""
    if not conn_id:
        return None
    row = bind.execute(sa.text(
        "SELECT id::text AS id, config FROM connections "
        "WHERE id::text = :id AND provider = :provider"
    ), {"id": conn_id, "provider": provider}).mappings().first()
    if row is None:
        return None
    return {"id": row["id"], "config": row["config"] or {}}


def _count(result) -> int:
    """UPDATE 결과의 행 수를 돌려준다. 드라이버가 행 수를 주지 않으면 0이다."""
    return max(result.rowcount or 0, 0)


def _pin_storage(bind, store: dict | None) -> dict[str, int]:
    """저장소 필드를 채운다. `store`가 없으면 연결 설정에서 버킷만 채운다."""
    out: dict[str, int] = {}
    # 연결은 있는데 버킷이 빈 프로젝트·영상은 그 연결 설정의 버킷을 썼다.
    out["project_bucket_from_connection"] = _count(bind.execute(sa.text("""
        UPDATE projects p SET settings = jsonb_set(p.settings, '{stack,bucket}',
            to_jsonb(c.config->>'bucket'))
        FROM connections c
        WHERE c.id::text = p.settings->'stack'->>'storage_connection_id'
          AND coalesce(p.settings->'stack'->>'bucket', '') = ''
          AND coalesce(c.config->>'bucket', '') <> ''
    """)))
    out["video_bucket_from_connection"] = _count(bind.execute(sa.text("""
        UPDATE videos v SET metadata = jsonb_set(v.metadata, '{storage,bucket}',
            to_jsonb(c.config->>'bucket'))
        FROM connections c
        WHERE jsonb_typeof(v.metadata->'storage') = 'object'
          AND c.id::text = v.metadata->'storage'->>'connection_id'
          AND coalesce(v.metadata->'storage'->>'bucket', '') = ''
          AND coalesce(c.config->>'bucket', '') <> ''
    """)))
    if store is None:
        return out
    s_id, s_bucket = store["id"], str(store["config"].get("bucket") or "").strip()
    by_id, both = {"s": s_id}, {"s": s_id, "b": s_bucket}
    out["project_connection"] = _count(bind.execute(sa.text("""
        UPDATE projects SET settings = jsonb_set(settings, '{stack,storage_connection_id}',
            to_jsonb(CAST(:s AS text)))
        WHERE coalesce(settings->'stack'->>'bucket', '') <> ''
          AND coalesce(settings->'stack'->>'storage_connection_id', '') = ''
    """), by_id))
    out["video_connection"] = _count(bind.execute(sa.text("""
        UPDATE videos SET metadata = jsonb_set(metadata, '{storage,connection_id}',
            to_jsonb(CAST(:s AS text)))
        WHERE jsonb_typeof(metadata->'storage') = 'object'
          AND coalesce(metadata->'storage'->>'connection_id', '') = ''
          AND coalesce(metadata->'storage'->>'bucket', '') <> ''
    """), by_id))
    if not s_bucket:
        return out
    out["project_recorded"] = _count(bind.execute(sa.text(f"""
        UPDATE projects p SET settings = coalesce(p.settings, '{{}}'::jsonb) || jsonb_build_object(
            'stack', coalesce(p.settings->'stack', '{{}}'::jsonb) || jsonb_build_object(
                'storage_connection_id', CAST(:s AS text), 'bucket', CAST(:b AS text)))
        WHERE EXISTS (SELECT 1 FROM videos v WHERE v.project_id = p.project_id)
          AND coalesce(p.settings->'stack'->>'bucket', '') = ''
          AND coalesce(p.settings->'stack'->>'storage_connection_id', '') = ''
          AND coalesce(p.settings->>'analysis_preset', '') NOT IN {_NO_STORAGE_PRESETS}
    """), both))
    out["video_pinned"] = _count(bind.execute(sa.text("""
        UPDATE videos SET metadata = metadata || jsonb_build_object('storage', jsonb_build_object(
            'connection_id', CAST(:s AS text), 'bucket', CAST(:b AS text)))
        WHERE jsonb_typeof(metadata->'storage') IS DISTINCT FROM 'object'
           OR (coalesce(metadata->'storage'->>'connection_id', '') = ''
               AND coalesce(metadata->'storage'->>'bucket', '') = '')
    """), both))
    out["poster_bucket"] = _count(bind.execute(sa.text("""
        UPDATE videos SET metadata = metadata || jsonb_build_object(
            'poster_thumbnail_bucket', CAST(:b AS text))
        WHERE coalesce(metadata->>'poster_thumbnail_key', '') <> ''
          AND coalesce(metadata->>'poster_thumbnail_bucket', '') = ''
    """), {"b": s_bucket}))
    out["clip_thumbnail_bucket"] = _count(bind.execute(sa.text("""
        UPDATE clips SET metadata = metadata || jsonb_build_object(
            'scene_thumbnail_bucket', CAST(:b AS text))
        WHERE coalesce(metadata->>'scene_thumbnail_key', '') <> ''
          AND coalesce(metadata->>'scene_thumbnail_bucket', '') = ''
    """), {"b": s_bucket}))
    out["export_bucket"] = _count(bind.execute(sa.text("""
        UPDATE exports SET storage_bucket = :b
        WHERE storage_bucket IS NULL AND manifest_s3_key IS NOT NULL
    """), {"b": s_bucket}))
    for table in ("grievances", "grievance_messages"):
        # 문의 표를 지운 스키마(0095 이후)에서 이 함수를 부르면 UPDATE가 UndefinedTable로 전체를 멈춘다.
        # 그래서 없는 표는 건너뛴다. 마이그레이션 체인의 0091 시점에는 두 표가 있으므로 결과가 같다.
        if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None:
            continue
        out[f"{table}_attachment_bucket"] = _count(bind.execute(sa.text(f"""
            UPDATE {table} SET attachments = (
                SELECT jsonb_agg(CASE
                    WHEN jsonb_typeof(a) = 'object' AND coalesce(a->>'bucket', '') = ''
                    THEN a || jsonb_build_object('bucket', CAST(:b AS text)) ELSE a END
                    ORDER BY ord)
                FROM jsonb_array_elements(attachments) WITH ORDINALITY AS t(a, ord))
            WHERE jsonb_typeof(attachments) = 'array'
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(attachments) a
                          WHERE jsonb_typeof(a) = 'object'
                            AND coalesce(a->>'bucket', '') = '')
        """), {"b": s_bucket}))
    return out


def _pin_analysis(bind, analysis: dict | None) -> dict[str, int]:
    """분석 필드를 채운다. `analysis`가 없으면 연결 설정에서 워크스페이스·MI 프로젝트만 채운다."""
    out: dict[str, int] = {}
    if analysis is not None:
        a_cfg = analysis["config"]
        params = {"a": analysis["id"],
                  "ws": str(a_cfg.get("workspace_name") or "").strip(),
                  "pid": str(a_cfg.get("project_id") or "").strip()}
        out["project_connection"] = _count(bind.execute(sa.text("""
            UPDATE projects SET settings = jsonb_set(settings, '{stack,analysis_connection_id}',
                to_jsonb(CAST(:a AS text)))
            WHERE coalesce(settings->'stack'->>'maiu_project_id', '') <> ''
              AND coalesce(settings->'stack'->>'analysis_connection_id', '') = ''
        """), {"a": params["a"]}))
        if params["ws"] and params["pid"]:
            out["project_recorded"] = _count(bind.execute(sa.text(f"""
                UPDATE projects p SET settings = coalesce(p.settings, '{{}}'::jsonb) ||
                    jsonb_build_object('stack', coalesce(p.settings->'stack', '{{}}'::jsonb) ||
                        jsonb_build_object('analysis_connection_id', CAST(:a AS text),
                                           'maiu_workspace', CAST(:ws AS text),
                                           'maiu_project_id', CAST(:pid AS text)))
                WHERE EXISTS (SELECT 1 FROM videos v WHERE v.project_id = p.project_id)
                  AND coalesce(p.settings->'stack'->>'maiu_project_id', '') = ''
                  AND coalesce(p.settings->>'analysis_preset', '') NOT IN {_NO_ANALYSIS_PRESETS}
            """), params))
            out["job_recorded"] = _count(bind.execute(sa.text("""
                UPDATE analysis_job SET payload = coalesce(payload, '{}'::jsonb) ||
                    jsonb_build_object('maiu', jsonb_build_object(
                        'connection_id', CAST(:a AS text), 'workspace', CAST(:ws AS text),
                        'project_id', CAST(:pid AS text)))
                WHERE adapter_key = 'maiu'
                  AND jsonb_typeof(payload->'maiu') IS DISTINCT FROM 'object'
            """), params))
        out["job_connection"] = _count(bind.execute(sa.text("""
            UPDATE analysis_job SET payload = jsonb_set(payload, '{maiu,connection_id}',
                to_jsonb(CAST(:a AS text)))
            WHERE adapter_key = 'maiu' AND jsonb_typeof(payload->'maiu') = 'object'
              AND coalesce(payload->'maiu'->>'connection_id', '') = ''
        """), {"a": params["a"]}))
    # 연결은 있는데 워크스페이스·MI 프로젝트가 빈 필드는 그 연결 설정의 값을 썼다.
    out["project_workspace_from_connection"] = _count(bind.execute(sa.text("""
        UPDATE projects p SET settings = jsonb_set(p.settings, '{stack,maiu_workspace}',
            to_jsonb(c.config->>'workspace_name'))
        FROM connections c
        WHERE c.id::text = p.settings->'stack'->>'analysis_connection_id'
          AND coalesce(p.settings->'stack'->>'maiu_project_id', '') <> ''
          AND coalesce(p.settings->'stack'->>'maiu_workspace', '') = ''
          AND coalesce(c.config->>'workspace_name', '') <> ''
    """)))
    for field, cfg_key in (("workspace", "workspace_name"), ("project_id", "project_id")):
        out[f"job_{field}_from_connection"] = _count(bind.execute(sa.text(f"""
            UPDATE analysis_job j SET payload = jsonb_set(j.payload, '{{maiu,{field}}}',
                to_jsonb(c.config->>'{cfg_key}'))
            FROM connections c
            WHERE j.adapter_key = 'maiu' AND jsonb_typeof(j.payload->'maiu') = 'object'
              AND c.id::text = j.payload->'maiu'->>'connection_id'
              AND coalesce(j.payload->'maiu'->>'{field}', '') = ''
              AND coalesce(c.config->>'{cfg_key}', '') <> ''
        """)))
    return out


def pin_all(bind, storage_id: str | None, analysis_id: str | None) -> dict:
    """비어 있으면 기본 연결로 읽던 필드를 주어진 연결로 채우고 바꾼 행 수를 돌려준다. 멱등이다.

    빈 필드만 채우므로 다시 돌려도 이미 채운 행은 바뀌지 않는다. 주어진 연결이 없거나 서비스가
    다르면 해당 항목은 연결 설정에서 읽을 수 있는 필드만 채운다.

    Args:
        bind: 동기 SQLAlchemy 연결.
        storage_id: 저장소 필드를 채울 NCP 연결 id.
        analysis_id: 분석 필드를 채울 MI 연결 id.

    Returns:
        `{"storage": {...}, "analysis": {...}}` 항목별 바꾼 행 수.
    """
    store = _connection(bind, storage_id, "ncp")
    analysis = _connection(bind, analysis_id, "maiu")
    return {"storage": _pin_storage(bind, store), "analysis": _pin_analysis(bind, analysis)}


def _setting(bind, key: str) -> str | None:
    """시스템 설정 값(연결 id 문자열)을 읽는다. 없거나 비었으면 `None`이다."""
    raw = bind.execute(sa.text(
        "SELECT value #>> '{}' FROM system_settings WHERE key = :key"), {"key": key}).scalar()
    return (raw or "").strip() or None


def upgrade() -> None:
    """기본 연결로 해석하던 필드를 고정하고 두 설정 키를 지운 뒤 감사 기록을 남긴다."""
    bind = op.get_bind()
    storage_id, analysis_id = _setting(bind, STORAGE_KEY), _setting(bind, ANALYSIS_KEY)
    pinned = pin_all(bind, storage_id, analysis_id)
    bind.execute(sa.text("DELETE FROM system_settings WHERE key IN (:s, :a)"),
                 {"s": STORAGE_KEY, "a": ANALYSIS_KEY})
    bind.execute(sa.text(
        "INSERT INTO domain_events (event_id, event_type, target_kind, payload, kind) "
        "VALUES (gen_random_uuid(), :t, 'system', CAST(:p AS jsonb), 'audit')"
    ), {"t": EVENT_TYPE, "p": json.dumps({
        STORAGE_KEY: storage_id, ANALYSIS_KEY: analysis_id, "pinned": pinned})})


def downgrade() -> None:
    """감사 기록에 남긴 두 기본 연결 id를 시스템 설정에 다시 쓴다. 채운 값은 그대로 둔다."""
    bind = op.get_bind()
    payload = bind.execute(sa.text(
        "SELECT payload FROM domain_events WHERE event_type = :t "
        "ORDER BY occurred_at DESC LIMIT 1"), {"t": EVENT_TYPE}).scalar()
    if not payload:
        return
    for key in (STORAGE_KEY, ANALYSIS_KEY):
        value = payload.get(key)
        if not value:
            continue
        bind.execute(sa.text(
            "INSERT INTO system_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ), {"k": key, "v": json.dumps(value)})
