"""Alembic 실행 환경. 동기 드라이버(psycopg)로 돌고 asyncpg URL은 psycopg로 바꿔 쓴다."""
import os
from logging.config import fileConfig

from alembic import context
from api_gateway.models import Base
from sqlalchemy import create_engine, pool

config = context.config

# 접속 주소는 DATABASE_URL 환경변수가 정한다. `alembic.ini`에 기본 주소가 없어서
# 둘 다 비면 연결하기 전에 멈춘다. 기본 주소로 대신하면 호스트의 다른 postgres(5432)에
# 같은 계정이 있을 때 의도하지 않은 DB에 적용된다. `+asyncpg` 드라이버는 `+psycopg`로 바꾼다.
db_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if not db_url:
    raise SystemExit(
        "DATABASE_URL을 지정해야 합니다. 예: DATABASE_URL=postgresql+asyncpg://"
        "<계정>:<비밀번호>@<호스트>:<포트>/<DB> alembic upgrade head"
    )
if "+asyncpg" in db_url:
    db_url = db_url.replace("+asyncpg", "+psycopg")
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB에 붙지 않고 SQL을 출력한다."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# 락을 기다리는 상한. 배포는 이전 앱이 도는 중에 마이그레이션을 실행한다.
# ALTER TABLE이 긴 트랜잭션 뒤에서 락을 기다리는 동안 대상 테이블을 읽는 요청이 모두 줄을 서서
# 화면 전체가 멈춘다. 상한을 넘기면 마이그레이션이 실패하고 배포가 멈춘다.
# 실패한 마이그레이션은 바뀐 것 없이 끝나므로 다시 배포하거나 워커를 잠시 내리고 다시 실행한다.
LOCK_TIMEOUT = "5s"


def run_migrations_online() -> None:
    """DB에 붙어 마이그레이션을 실행한다. 모든 문장에 락 대기 상한을 건다."""
    connectable = create_engine(db_url, poolclass=pool.NullPool)
    with connectable.connect() as conn:
        # 세션 설정이라 커밋한 뒤에도 남는다. 커밋하지 않으면 alembic이 열린 트랜잭션을 바깥
        # 트랜잭션으로 보고 마이그레이션을 커밋하지 않는다.
        conn.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        conn.commit()
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
