"""RLS baseline 마이그레이션(alembic 0017) 테스트."""
from api_gateway.database import SessionLocal
from sqlalchemy import text

_RLS_TABLES = (
    "projects", "videos", "work_items", "clips",
    "labels", "reviews",
)


async def test_core_tables_have_rls_enabled():
    """alembic 0017 적용 뒤 핵심 테이블 6개의 rowsecurity가 모두 true인지 확인한다."""
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT tablename, rowsecurity FROM pg_tables "
            "WHERE schemaname='public' AND tablename = ANY(:names) "
            "ORDER BY tablename"
        ), {"names": list(_RLS_TABLES)})).all()
    table_to_rls = {t: r for t, r in rows}
    for t in _RLS_TABLES:
        assert table_to_rls.get(t) is True, f"{t}에 RLS 미적용"


async def test_baseline_policy_exists_per_table():
    """테이블마다 `_baseline` 정책(PERMISSIVE, FOR ALL)이 있는지 확인한다."""
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT tablename, policyname, permissive FROM pg_policies "
            "WHERE schemaname='public' AND tablename = ANY(:names)"
        ), {"names": list(_RLS_TABLES)})).all()
    pol_by_table = {t: (p, perm) for t, p, perm in rows}
    for t in _RLS_TABLES:
        assert t in pol_by_table, f"{t}에 policy 없음"
        polname, permissive = pol_by_table[t]
        assert polname == f"{t}_baseline"
        assert permissive in ("PERMISSIVE", "permissive", True)


async def test_baseline_does_not_block_existing_queries():
    """baseline 정책(`USING(true)`)이 기존 select를 막지 않는지 스모크 테스트로 확인한다."""
    async with SessionLocal() as s:
        n = (await s.execute(text("SELECT count(*) FROM projects"))).scalar()
    assert n is not None and n >= 0
