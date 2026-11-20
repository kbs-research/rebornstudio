"""기능 명세의 enum과 스키마가 실제 DB와 맞는지 보는 회귀 테스트.

명세의 enum·스키마가 실제 DB, 모델, 계약과 일치하는지 확인한다.
"""
from api_gateway.database import SessionLocal
from sqlalchemy import text

# copyright_policy 값 1종(alembic 0099)

async def test_copyright_policy_enum_matches_spec():
    """영상 권리 정책 컬럼이 pre_cleared 하나만 허용하는지 확인한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='ck_video_copyright_policy'"
        ))
        defn = r.scalar()
    assert defn and "pre_cleared" in defn
    # 이전 정책값이 제거됐는지 확인한다. 값이 남아 있으면 화면이 두 가지 정책 체계로 나뉜다.
    assert "review_required" not in defn
    assert "'mixed'" not in defn
    assert "'external_pending'" not in defn
    assert "'public'" not in defn
    assert "'restricted'" not in defn


# clips 테이블과 work_items.clip_id

async def test_clips_table_exists_with_spec_fields():
    """clips 테이블에 명세의 필드(rights_status, auto_caption, speakers 등)가 있는지 확인한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'clips' ORDER BY ordinal_position"
        ))
        cols = {row[0] for row in r}
    for required in {"clip_id", "video_id", "project_id", "start_seconds",
                     "end_seconds", "auto_caption", "speakers", "rights_status"}:
        assert required in cols, f"clips.{required} missing"


async def test_work_items_has_clip_id_nullable():
    """work_items.clip_id가 nullable인지 확인한다. 이 컬럼은 FK로 clips를 참조한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='work_items' AND column_name='clip_id'"
        ))
        assert r.scalar() == "YES"


async def test_clip_rights_status_check_constraint():
    """clips.rights_status의 CHECK 제약이 명세의 값 6종(auto_cleared 등)을 모두 허용하는지 확인한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='ck_clip_rights_status'"
        ))
        defn = r.scalar()
    for s_val in ("pending", "auto_cleared", "cleared", "cleared_with_edit",
                  "rejected", "legal_review"):
        assert s_val in defn, f"clip rights_status missing {s_val}"


# 분석 상태 테이블 analysis_job의 중립 상태와 reconciler 컬럼

async def test_analysis_job_table_replaces_maiu_tables():
    """analysis_job 테이블이 있고 이전 MI 상태 테이블(maiu_jobs·maiu_job_state)이 없는지 확인한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' "
            "AND table_name IN ('maiu_jobs','maiu_job_state','analysis_job')"
        ))
        names = {row[0] for row in r}
    assert "analysis_job" in names
    assert "maiu_jobs" not in names
    assert "maiu_job_state" not in names


async def test_analysis_job_status_check_constraint_has_neutral_states():
    """analysis_job의 상태 CHECK 제약이 중립 상태 5종을 모두 허용하는지 확인한다."""
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='ck_analysis_job_status'"
        ))
        defn = r.scalar()
    for s_val in ("pending", "running", "done", "partial", "failed"):
        assert s_val in defn, f"analysis_job status missing {s_val}"


async def test_analysis_job_has_reconciler_fields():
    """analysis_job에 reconciler가 쓰는 컬럼이 있는지 확인한다.

    MI 단계(stage), 에셋 번호(external_ref), deferred, attempt, in_flight_count, trace_id 등이다.
    """
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='analysis_job'"
        ))
        cols = {row[0] for row in r}
    for col in {"stage", "external_ref", "deferred", "attempt", "in_flight_count",
                "trace_id", "error_msg", "payload", "adapter_key"}:
        assert col in cols, f"analysis_job.{col} missing"


# 시드

async def test_organizations_seed():
    """조직이 기본 조직 한 행뿐인지 확인한다(alembic 0101)."""
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT slug, name FROM organizations"))).all()
    assert [tuple(r) for r in rows] == [("default", "기본 조직")]


async def test_project_policy_column_defaults():
    """새 프로젝트가 받는 정책 컬럼 기본값이 스키마에 있는지 확인한다.

    `conversations_enabled`는 이 검사에서 뺀다.
    읽는 코드가 없어 DTO·PATCH 허용 키·계약에 없는 값이다.
    컬럼은 남아 있지만 기본값을 보장할 이유가 없다.
    """
    async with SessionLocal() as s:
        r = await s.execute(text("""
            SELECT column_name, column_default FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'projects'
               AND column_name IN ('default_assignment_ttl_hours',
                                   'default_max_concurrent_assignments', 'caption_min_tokens')
        """))
        defaults = dict(r.all())
    # 기본 배정 만료는 마이그레이션 0068이 정한 24시간이다. 기준은 마이그레이션이다.
    assert defaults == {
        "default_assignment_ttl_hours": "24",
        "default_max_concurrent_assignments": "5",
        "caption_min_tokens": "50",
    }
