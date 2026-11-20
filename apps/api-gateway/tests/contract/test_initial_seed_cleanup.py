"""마이그레이션 0107이 손대지 않은 시드만 지우고 쓰인 시드는 시드 값만 비우는지 확인한다.

새 설치의 프로젝트 목록과 사용자 목록에 이전 버전의 시드가 남지 않아야 한다. 반대로 이미 쓰인 시드를
지우면 외래 키가 막거나 이력이 끊긴다. 마이그레이션 함수를 테스트 DB의 트랜잭션 안에서 부르고 되돌린다.
마이그레이션은 시드 값을 md5로만 가지므로 테스트는 가짜 시드 값을 넣고 그 md5로 모듈 상수를 바꿔 부른다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text

_MIGRATION = (Path(__file__).resolve().parents[2] / "alembic" / "versions"
              / "0107_drop_initial_seeds.py")


#: 시드 행을 흉내 내는 가짜 시드 값. 목표 분량은 `double precision`의 글자 표현이 `77`이 되는 값이다.
_FAKE_SEED = {"name": "시험 시드 프로젝트", "desc": "시험 시드 설명", "deadline": "2030-01-31", "hours": 77}


def _md5(value: str) -> str:
    """글자의 md5 16진수 값을 돌려준다. SQL의 `md5()`와 같은 값이다."""
    return hashlib.md5(value.encode()).hexdigest()


def _module():
    """마이그레이션 파일을 모듈로 불러온다. 파일 이름이 숫자로 시작해 import 문으로는 부를 수 없다."""
    spec = importlib.util.spec_from_file_location("migration_0107", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_module():
    """시드 값의 md5 상수를 가짜 시드 값의 md5로 바꾼 마이그레이션 모듈을 돌려준다."""
    m = _module()
    m._SEED_PROJECT_NAME_MD5 = (_md5(_FAKE_SEED["name"]),)
    m._SEED_DESCRIPTION_MD5 = _md5(_FAKE_SEED["desc"])
    m._SEED_DEADLINE_MD5 = _md5(_FAKE_SEED["deadline"])
    m._SEED_TARGET_HOURS_MD5 = _md5(str(_FAKE_SEED["hours"]))
    return m


def _engine() -> sa.Engine:
    """테스트 DB에 붙는 동기 엔진을 만든다. 마이그레이션은 동기 드라이버로 돈다."""
    return sa.create_engine(os.environ["DATABASE_URL"].replace("+asyncpg", "+psycopg"))


def _insert_seeds(conn, m) -> tuple[uuid.UUID, uuid.UUID]:
    """이전 버전의 0001·0002가 만든 것과 같은 모양의 시드 프로젝트와 정지된 시드 계정을 만들고 두 id를 돌려준다."""
    conn.execute(text("DELETE FROM projects WHERE slug = 'default'"))
    pid = conn.execute(text("""
        INSERT INTO projects (org_id, slug, name, description, status, contract_deadline, target_hours)
        SELECT org_id, 'default', :name, :desc, 'active', CAST(:deadline AS date), :hours
          FROM organizations LIMIT 1
        RETURNING project_id
    """), {"name": _FAKE_SEED["name"], "desc": _FAKE_SEED["desc"],
           "deadline": _FAKE_SEED["deadline"], "hours": _FAKE_SEED["hours"]}).scalar()
    uid = conn.execute(text("""
        INSERT INTO users (email, display_name, global_role, approval_status, password_hash)
        VALUES (:email, 'Super Admin', 'super_admin', 'suspended', :hash)
        RETURNING user_id
    """), {"email": f"seed-{uuid.uuid4().hex[:8]}@example.com", "hash": m._SEED_HASH}).scalar()
    return pid, uid


def test_fresh_schema_has_no_seed_project_or_admin():
    """새로 만든 테스트 DB에는 시드 프로젝트도 시드 해시의 계정도 없다."""
    m = _module()
    engine = _engine()
    try:
        with engine.connect() as conn:
            projects = conn.execute(text(
                "SELECT count(*) FROM projects WHERE slug = 'default' AND md5(name) = ANY(:n)"),
                {"n": list(m._SEED_PROJECT_NAME_MD5)}).scalar()
            admins = conn.execute(text("SELECT count(*) FROM users WHERE password_hash = :h"),
                                  {"h": m._SEED_HASH}).scalar()
    finally:
        engine.dispose()
    assert (projects, admins) == (0, 0)


def test_untouched_seeds_are_deleted():
    """아무 행도 가리키지 않는 시드 프로젝트와 시드 계정은 지운다."""
    m = _fake_module()
    engine = _engine()
    try:
        with engine.connect() as conn:
            pid, uid = _insert_seeds(conn, m)
            m._drop_seed_project(conn)
            m._drop_seed_admin(conn)
            left_p = conn.execute(text("SELECT count(*) FROM projects WHERE project_id = :p"),
                                  {"p": pid}).scalar()
            left_u = conn.execute(text("SELECT count(*) FROM users WHERE user_id = :u"),
                                  {"u": uid}).scalar()
            conn.rollback()
    finally:
        engine.dispose()
    assert (left_p, left_u) == (0, 0)


def test_referenced_seeds_are_neutralized_not_deleted():
    """이벤트가 가리키는 시드는 지우지 않고 시드 값만 비운다. 운영자가 바꾼 필드는 그대로 둔다."""
    m = _fake_module()
    engine = _engine()
    try:
        with engine.connect() as conn:
            pid, uid = _insert_seeds(conn, m)
            conn.execute(text("UPDATE projects SET target_hours = 300 WHERE project_id = :p"),
                         {"p": pid})
            conn.execute(text("""
                INSERT INTO domain_events (actor_user_id, event_type, target_kind, target_id, project_id)
                VALUES (:u, 'project.updated', 'project', :p, :p)
            """), {"u": uid, "p": pid})
            m._drop_seed_project(conn)
            m._drop_seed_admin(conn)
            project = conn.execute(text("""
                SELECT name, description, contract_deadline, target_hours
                  FROM projects WHERE project_id = :p
            """), {"p": pid}).one()
            email = conn.execute(text("SELECT email FROM users WHERE user_id = :u"),
                                 {"u": uid}).scalar()
            conn.rollback()
    finally:
        engine.dispose()
    assert tuple(project) == (m._NEUTRAL_PROJECT_NAME, None, None, 300)
    assert email == m._RETIRED_EMAIL
