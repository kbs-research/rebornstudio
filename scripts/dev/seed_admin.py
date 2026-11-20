"""개발·테스트용 super_admin을 시드한다.

마이그레이션이 아니라 스크립트로 두는 이유: 마이그레이션은 모든 환경에서 똑같이
실행되므로 개발 편의가 그대로 운영에 실린다. 편의 기능은 부르는 곳이 있을 때만 생겨야
한다. 그래서 dev·test·CI는 이 스크립트를 부르고, 운영은 부르지 않는다. 마이그레이션
0057이 기존 시드 계정을 정지시킨다. 이 스크립트는 필요한 곳에서만 그 계정을 되살린다.
마이그레이션으로 만들면 저장소에 적힌 비밀번호를 가진 super_admin이 운영 서버에 남는다.

사용하는 곳:

    make seed-dev                      # 로컬
    python scripts/dev/seed_admin.py   # CI (pytest 앞)
    tests/conftest.py                  # 테스트가 직접 (이 모듈을 import 한다)

멱등이다. 계정이 이미 있으면 활성 상태와 역할을 맞추고, 비밀번호는 명시했을 때만 바꾼다.

비밀번호는 인자나 `ADMIN_SEED_PASSWORD`로 받는다. 둘 다 없으면 새 계정에만 무작위 값을 주고,
이미 있는 계정의 비밀번호는 그대로 둔다. 다시 시드할 때마다 무작위 값으로 덮으면 그 계정에
비밀번호로 로그인하던 사람이 들어가지 못한다. 만든 값은 출력하지 않는다.
저장소에 평문 기본값을 두면 그 값이 공개 소스와 함께 퍼진다. 로그인은 `make dev-token`으로 한다.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import UTC, datetime

DEFAULT_EMAIL = "admin@example.com"


def _password(given: str | None) -> str | None:
    """명시한 비밀번호를 고른다. 인자, `ADMIN_SEED_PASSWORD` 순서이고 둘 다 없으면 None이다."""
    return given or os.getenv("ADMIN_SEED_PASSWORD") or None


async def seed_admin(email: str = DEFAULT_EMAIL, password: str | None = None) -> str:
    """super_admin을 만들거나 되살린다. 무엇을 했는지 로그용 문자열로 돌려준다.

    돌려주는 문자열에는 비밀번호를 싣지 않는다.

    시드 계정은 사람이 아니라 개발용 계정이라 개인정보 수집·이용 동의 시각도 시드가 채운다.
    채우지 않으면 업무 API가 403 `privacy_consent_required`로 응답해 테스트와 로컬 화면이 막힌다.

    운영에서 부르지 않는 함수다. `ENVIRONMENT`가 local·test가 아니면
    (`ENVIRONMENT=prod` 포함) 거부한다. 배포 스크립트에 실수로 끼어 들어가는 것을
    여기서 막는다.
    """
    from api_gateway.config import settings
    from api_gateway.database import SessionLocal
    from api_gateway.models import User
    from api_gateway.security import hash_password
    from sqlalchemy import select

    # 이 목록은 config가 실제로 느슨하게 검사하는 환경과 같아야 한다.
    # 여기서만 허용해도 config가 그 환경을 모르면 `Settings()` 생성에서 실패한다.
    if settings.environment not in ("local", "test"):
        raise SystemExit(
            f"거부: ENVIRONMENT={settings.environment}. 이 시드는 개발·테스트 전용이다. "
            "운영의 첫 관리자는 셋업 화면이 만든다."
        )

    password = _password(password)
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(User(
                email=email, display_name="Super Admin", global_role="super_admin",
                approval_status="active",
                password_hash=hash_password(password or secrets.token_urlsafe(18)),
                email_verified=True, privacy_consented_at=datetime.now(UTC),
            ))
            action = "생성"
        else:
            # 0057이 정지시켰을 수 있다. 되살리고, 비밀번호는 명시했을 때만 맞춘다(멱등).
            user.approval_status = "active"
            user.global_role = "super_admin"
            if password:
                user.password_hash = hash_password(password)
            if user.privacy_consented_at is None:
                user.privacy_consented_at = datetime.now(UTC)
            action = "갱신"
        await db.commit()
    return f"{action}: {email}"


if __name__ == "__main__":
    os.environ.setdefault("ENVIRONMENT", "local")
    print(asyncio.run(seed_admin(os.getenv("ADMIN_SEED_EMAIL", DEFAULT_EMAIL))), file=sys.stderr)
    print("로그인은 make dev-token으로 한다.", file=sys.stderr)
