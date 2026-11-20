-- MI에 등록됐지만 어느 분석 잡도 가리키지 않는 에셋을 센다. 읽기 전용이다.
--
-- 분석 요청은 요청 시점에 에셋을 등록하고 그 번호를 maiu.requested 이벤트와 잡에 적는다.
-- 같은 잡을 다시 등록해 잡의 에셋 번호(external_ref)가 새 에셋으로 바뀌면, 요청 때 만든 에셋은
-- 어느 잡도 가리키지 않은 채 MI에 남는다.
-- 이 쿼리는 이벤트에 남은 에셋 번호 가운데 지금 그 영상의 잡이 가리키지 않는 것을 고른다.
-- 무엇을 지울지는 사람이 정한다. 여기서는 아무것도 바꾸지 않는다.
--
-- 실행: psql "$DATABASE_URL" -f scripts/ops/maiu_orphan_assets.sql
--   DATABASE_URL은 psql 형식(postgresql://…)으로 준다. asyncpg 접두사는 떼야 한다.
--
-- 열:
--   project_slug   프로젝트 슬러그
--   video_id       영상
--   asset_id       이벤트에 남은 에셋 번호
--   job_asset_id   지금 잡이 가리키는 에셋 번호. 잡이 없으면 비어 있다
--   source_event   에셋 번호를 남긴 이벤트(maiu.requested 또는 maiu.stage.register_triggered)
--   seen_at        그 이벤트가 처음 남은 시각
--   job_missing    잡이 없는지. 영상 삭제·처음부터 다시 요청으로 잡이 지워진 경우다.
--                  이 경우 에셋이 고아인지는 MI 쪽 목록과 맞춰 봐야 안다

BEGIN TRANSACTION READ ONLY;

WITH seen AS (
    -- 사람의 분석 요청은 영상을 대상으로 남는다.
    SELECT e.project_id,
           e.target_id AS video_id,
           e.payload ->> 'asset_id' AS asset_id,
           e.event_type,
           e.occurred_at
      FROM domain_events e
     WHERE e.event_type = 'maiu.requested'
       AND e.payload ->> 'asset_id' IS NOT NULL
    UNION ALL
    -- 리컨실러의 등록은 잡을 대상으로 남고 영상 번호를 payload에 싣는다.
    SELECT e.project_id,
           (e.payload ->> 'video_id')::uuid,
           e.payload ->> 'asset_id',
           e.event_type,
           e.occurred_at
      FROM domain_events e
     WHERE e.event_type = 'maiu.stage.register_triggered'
       AND e.payload ->> 'asset_id' IS NOT NULL
       AND e.payload ->> 'video_id' IS NOT NULL
),
first_seen AS (
    SELECT project_id, video_id, asset_id,
           min(event_type) AS source_event,
           min(occurred_at) AS seen_at
      FROM seen
     GROUP BY project_id, video_id, asset_id
),
orphans AS (
    SELECT f.*, j.external_ref AS job_asset_id, (j.id IS NULL) AS job_missing
      FROM first_seen f
      LEFT JOIN analysis_job j
        ON j.video_id = f.video_id AND j.clip_id IS NULL AND j.adapter_key = 'maiu'
     WHERE j.external_ref IS DISTINCT FROM f.asset_id
)
SELECT p.slug AS project_slug, o.video_id, o.asset_id, o.job_asset_id,
       o.source_event, o.seen_at, o.job_missing
  FROM orphans o
  LEFT JOIN projects p ON p.project_id = o.project_id
 ORDER BY p.slug, o.seen_at;

-- 프로젝트별 수. 잡이 있는데 다른 에셋을 가리키는 것(확실한 고아)과 잡이 없는 것을 나눈다.
WITH seen AS (
    SELECT e.project_id, e.target_id AS video_id, e.payload ->> 'asset_id' AS asset_id
      FROM domain_events e
     WHERE e.event_type = 'maiu.requested' AND e.payload ->> 'asset_id' IS NOT NULL
    UNION
    SELECT e.project_id, (e.payload ->> 'video_id')::uuid, e.payload ->> 'asset_id'
      FROM domain_events e
     WHERE e.event_type = 'maiu.stage.register_triggered'
       AND e.payload ->> 'asset_id' IS NOT NULL AND e.payload ->> 'video_id' IS NOT NULL
)
SELECT p.slug AS project_slug,
       count(*) FILTER (WHERE j.id IS NOT NULL) AS orphan_assets,
       count(*) FILTER (WHERE j.id IS NULL) AS job_missing_assets
  FROM seen s
  LEFT JOIN analysis_job j
    ON j.video_id = s.video_id AND j.clip_id IS NULL AND j.adapter_key = 'maiu'
  LEFT JOIN projects p ON p.project_id = s.project_id
 WHERE j.external_ref IS DISTINCT FROM s.asset_id
 GROUP BY p.slug
 ORDER BY orphan_assets DESC;

ROLLBACK;
