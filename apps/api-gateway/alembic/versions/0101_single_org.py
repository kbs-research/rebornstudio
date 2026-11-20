"""조직을 한 행으로 합친다. RebornStudio는 조직 관리가 없고 서버가 기본 조직 한 행을 찾아 쓴다.

## 무엇이 바뀌나

- 프로젝트와 연결이 가장 많은 조직 하나를 남기고, 다른 조직의 프로젝트와 연결을 그 조직으로 옮긴 뒤
  다른 조직을 지운다. 조직이 없으면 하나 만든다.
- 남긴 조직의 slug와 이름을 `default`·`기본 조직`으로 바꾼다.
- 상수 식 유일 인덱스로 조직이 두 행 이상 생기지 않게 막는다.

`org_id` 컬럼은 남긴다. RebornStudio Pro와 같은 스키마를 유지한다.

## 이행이 멈추는 경우

옮길 연결의 이름이나 프로젝트의 slug가 남길 조직의 것과 같으면 멈춘다. 이름을 바꿔 합치면
프로젝트 설정이 연결을 이름으로 가리키므로 다른 연결을 가리키게 된다. 운영자가 한쪽 이름을 바꾼 뒤
다시 실행한다.

## 되돌리기

유일 인덱스만 지운다. 합친 조직은 돌아오지 않는다.
"""
from alembic import op
from sqlalchemy import text

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None

#: 남길 조직. 프로젝트와 연결이 가장 많은 조직이고 같으면 먼저 만든 조직이다.
_KEEPER = """
SELECT o.org_id FROM organizations o
ORDER BY (SELECT count(*) FROM projects p WHERE p.org_id = o.org_id)
       + (SELECT count(*) FROM connections c WHERE c.org_id = o.org_id) DESC,
         o.created_at, o.slug
LIMIT 1
"""


def upgrade() -> None:
    """조직을 한 행으로 합치고 두 번째 행을 막는다."""
    conn = op.get_bind()
    keeper = conn.execute(text(_KEEPER)).scalar()
    if keeper is None:
        op.execute("INSERT INTO organizations (slug, name) VALUES ('default', '기본 조직')")
    else:
        dup_conn = conn.execute(text(
            "SELECT c.name FROM connections c JOIN connections k "
            "ON k.name = c.name AND k.org_id = :k WHERE c.org_id <> :k LIMIT 1"
        ), {"k": keeper}).scalar()
        if dup_conn is not None:
            raise RuntimeError(
                f"다른 조직에 같은 이름의 연결이 있습니다: {dup_conn}. 한쪽 이름을 바꾼 뒤 다시 실행하세요.")
        dup_slug = conn.execute(text(
            "SELECT p.slug FROM projects p JOIN projects k "
            "ON k.slug = p.slug AND k.org_id = :k WHERE p.org_id <> :k LIMIT 1"
        ), {"k": keeper}).scalar()
        if dup_slug is not None:
            raise RuntimeError(
                f"다른 조직에 같은 slug의 프로젝트가 있습니다: {dup_slug}. 한쪽을 바꾼 뒤 다시 실행하세요.")
        conn.execute(text("UPDATE connections SET org_id = :k WHERE org_id <> :k"), {"k": keeper})
        conn.execute(text("UPDATE projects SET org_id = :k WHERE org_id <> :k"), {"k": keeper})
        conn.execute(text("DELETE FROM organizations WHERE org_id <> :k"), {"k": keeper})
        conn.execute(text(
            "UPDATE organizations SET slug = 'default', name = '기본 조직', status = 'active' "
            "WHERE org_id = :k"
        ), {"k": keeper})
    op.execute("CREATE UNIQUE INDEX uq_organizations_single ON organizations ((true))")


def downgrade() -> None:
    """두 번째 행을 막는 인덱스를 지운다. 합친 조직은 되돌리지 않는다."""
    op.execute("DROP INDEX IF EXISTS uq_organizations_single")
