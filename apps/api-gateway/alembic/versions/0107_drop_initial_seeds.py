"""이전 버전의 마이그레이션이 만든 시드 프로젝트와 시드 관리자 계정을 지운다.

## 무엇이 바뀌나

이전 버전의 0001과 0038은 slug `default` 프로젝트를 설치와 무관한 값(목표 분량, 마감일, 설명)으로 만들고,
이전 버전의 0002는 개발용 최고관리자 계정을 만든다(0057이 운영에서 정지한다). 두 행은 화면의 프로젝트 목록과
사용자 목록에 남아 설치 기관과 무관한 값을 보인다.
지금의 0001·0002·0038은 두 행을 만들지 않으므로 이 마이그레이션은 이전 버전으로 만든 DB에서만 행을 바꾼다.

- 프로젝트: 이름이 시드 이름 그대로이고 만든 사람이 없으며 어떤 행도 가리키지 않으면 지운다.
- 계정: 비밀번호가 시드 해시 그대로이고 정지 상태이며 어떤 행도 가리키지 않으면 지운다.

가리키는 행은 그 테이블을 참조하는 외래 키 전부와 외래 키 없이 id를 담는 열
(`domain_events.target_id`, `clips.rights_decided_by`)에서 찾는다.

시드 프로젝트의 이름, 설명, 마감일, 목표 분량은 원문 대신 md5 값으로 두고 SQL의 `md5()`로 비교한다.
원문을 소스에 남기지 않기 위해서다. 시드 행은 slug `default`, 만든 사람 없음, 이름의 md5 일치를
함께 만족할 때만 고른다.

## 이행

행이 가리키는 시드는 지우지 않는다. 지우면 외래 키가 막거나 이력이 끊긴다.

- 프로젝트: 시드 값과 같은 필드만 고친다. 이름은 `기본 프로젝트`로 바꾸고 설명, 마감일, 목표 분량은
  비운다. 운영자가 바꾼 필드는 그대로 둔다.
- 계정: 시드 해시 그대로 정지된 계정은 로그인할 수 없으므로 이메일만 예약 도메인 주소로 바꾼다.
  비밀번호를 바꾼 계정은 운영자가 실제로 쓰는 계정이라 건드리지 않는다.

## 되돌리기

아무것도 하지 않는다. 지운 시드는 새 설치에 필요 없고, 고친 필드는 원래 값을 알 수 없다.
0093(RebornStudio Pro와 같은 스키마)까지 되돌려도 스키마는 같고 시드 행만 없다.
"""
import logging

from alembic import op
from sqlalchemy import text

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

#: 이전 버전의 0001과 0038이 시드 프로젝트에 붙인 이름 두 개의 md5.
_SEED_PROJECT_NAME_MD5 = ("0ad14a5470616b693e564721b21d593d", "1fe4b1e52744f8865823c1453e8fcf7f")
#: 이전 버전의 0001이 넣은 설명의 md5.
_SEED_DESCRIPTION_MD5 = "b63ecaef2728420e70132dc29b97b295"
#: 이전 버전의 0001이 넣은 마감일을 `YYYY-MM-DD`로 쓴 글자의 md5.
_SEED_DEADLINE_MD5 = "f4c20443c57178866623ce24f099a272"
#: 이전 버전의 0001이 넣은 목표 분량을 `double precision`의 글자 표현으로 쓴 값의 md5.
_SEED_TARGET_HOURS_MD5 = "81e5f81db77c596492e6f1a5a792ed53"
_NEUTRAL_PROJECT_NAME = "기본 프로젝트"

#: 이전 버전의 0002가 만든 계정의 비밀번호 해시. bcrypt 해시는 솔트가 달라 이 값과 같은 행은 시드 계정뿐이다.
_SEED_HASH = "$2b$12$WGucEJr9tsp3esMvQ8z.HOOGoxnLz2lzjxNC4BjEgG3SFLx/ubgRm"
_RETIRED_EMAIL = "retired-seed-admin@example.invalid"

#: 외래 키 없이 id를 담는 열. 외래 키 목록만 보면 이 열의 참조를 놓친다.
_EXTRA_REFS = {
    "projects": [("domain_events", "target_id")],
    "users": [("domain_events", "target_id"), ("clips", "rights_decided_by")],
}


def _references(conn, table: str, row_id) -> list[str]:
    """행을 가리키는 열 목록을 돌려준다. 비어 있으면 아무 행도 가리키지 않는다.

    Args:
        conn: 마이그레이션 연결.
        table: 대상 테이블 이름(`projects` 또는 `users`).
        row_id: 대상 행의 id.

    Returns:
        가리키는 행이 있는 `테이블.열` 목록.
    """
    fks = conn.execute(text("""
        SELECT c.conrelid::regclass::text, a.attname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'f'
           AND c.confrelid = CAST(:t AS regclass)
           AND array_length(c.conkey, 1) = 1
    """), {"t": table}).all()
    hits = []
    for rel, col in [*fks, *_EXTRA_REFS[table]]:
        found = conn.execute(
            text(f'SELECT EXISTS (SELECT 1 FROM {rel} WHERE "{col}" = :v)'), {"v": row_id}
        ).scalar()
        if found:
            hits.append(f"{rel}.{col}")
    return hits


def _drop_seed_project(conn) -> None:
    """시드 프로젝트를 지우거나, 가리키는 행이 있으면 시드 값만 비운다."""
    rows = conn.execute(text("""
        SELECT project_id FROM projects
         WHERE slug = 'default' AND md5(name) = ANY(:names) AND created_by IS NULL
    """), {"names": list(_SEED_PROJECT_NAME_MD5)}).scalars().all()
    for pid in rows:
        refs = _references(conn, "projects", pid)
        if not refs:
            conn.execute(text("DELETE FROM projects WHERE project_id = :p"), {"p": pid})
            log.info("시드 프로젝트를 지웠습니다: %s", pid)
            continue
        conn.execute(text("""
            UPDATE projects
               SET name = CASE WHEN md5(name) = ANY(:names) THEN :neutral ELSE name END,
                   description = CASE WHEN md5(description) = :desc THEN NULL ELSE description END,
                   contract_deadline = CASE WHEN md5(to_char(contract_deadline, 'YYYY-MM-DD')) = :deadline
                                            THEN NULL ELSE contract_deadline END,
                   target_hours = CASE WHEN md5(CAST(target_hours AS text)) = :hours
                                       THEN NULL ELSE target_hours END
             WHERE project_id = :p
        """), {"names": list(_SEED_PROJECT_NAME_MD5), "neutral": _NEUTRAL_PROJECT_NAME,
               "desc": _SEED_DESCRIPTION_MD5, "deadline": _SEED_DEADLINE_MD5,
               "hours": _SEED_TARGET_HOURS_MD5, "p": pid})
        log.info("시드 프로젝트를 쓰는 행이 있어 시드 값만 비웠습니다: %s (%s)", pid, ", ".join(refs))


def _drop_seed_admin(conn) -> None:
    """정지된 시드 계정을 지우거나, 가리키는 행이 있으면 이메일만 바꾼다."""
    uid = conn.execute(text("""
        SELECT user_id FROM users
         WHERE password_hash = :hash AND approval_status = 'suspended'
    """), {"hash": _SEED_HASH}).scalar()
    if uid is None:
        return
    refs = _references(conn, "users", uid)
    if not refs:
        conn.execute(text("DELETE FROM users WHERE user_id = :u"), {"u": uid})
        log.info("시드 관리자 계정을 지웠습니다")
        return
    taken = conn.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": _RETIRED_EMAIL}).scalar()
    if taken is None:
        conn.execute(text("UPDATE users SET email = :e WHERE user_id = :u"),
                     {"e": _RETIRED_EMAIL, "u": uid})
    log.info("시드 관리자 계정을 쓰는 행이 있어 이메일만 바꿨습니다 (%s)", ", ".join(refs))


def upgrade() -> None:
    """손대지 않은 시드 프로젝트와 시드 관리자 계정을 지운다."""
    conn = op.get_bind()
    _drop_seed_project(conn)
    _drop_seed_admin(conn)


def downgrade() -> None:
    """되돌리지 않는다. 지운 시드는 새 설치에 필요 없고 고친 필드의 원래 값은 남아 있지 않다."""
