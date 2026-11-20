"""worker-io 테스트 환경을 설정한다. Celery eager 모드와 실제 DB(rebornstudio)로 통합 검증한다."""
import os

# 로컬 `.env`를 읽지 않는다. config가 저장소 루트의 `.env`를 실행 위치와 무관하게 찾기 때문이다.
os.environ.setdefault("REBORN_ENV_FILE", "")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
