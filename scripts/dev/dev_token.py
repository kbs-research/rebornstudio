"""로컬 개발용 세션 토큰을 발급한다. 비밀번호 없이 화면을 검증할 때 쓴다.

화면 검증에는 로그인한 세션이 필요한데, 개발 중 세션은 자주 만료된다(access 60분).
만료될 때마다 비밀번호로 다시 로그인하면 검증이 끊긴다. 비밀번호를 스크립트나 셸 히스토리에
남기면 dev 비밀번호라도 유출 위험이 생긴다.
그래서 비밀번호 경로를 쓰지 않는다. 이미 DB에 있는 계정에 앱 자신의
`create_access_token`으로 토큰을 만든다. 테스트가 실행할 때마다 하는 것과 같은 일이다.

`ENVIRONMENT`가 local·dev·test가 아니면 거부한다. 이 스크립트는 인증을 우회하는
도구다. 운영 자격증명이 로드된 셸에서 실수로 실행하면 운영 super_admin 토큰이
터미널에 찍힌다. 그 유출은 되돌릴 수 없다.
`ENVIRONMENT`가 없으면 기본값 prod라서 거부한다. `DATABASE_URL`의 호스트가 로컬이 아니어도 거부한다.
운영 DB 주소와 시크릿이 export된 셸에서 `ENVIRONMENT`만 local로 잘못 주어도 DB에 붙기 전에 멈춘다.
compose 서비스 이름(`postgres`)은 `ENVIRONMENT`를 명시했을 때만 받는다. 운영 compose도 같은
이름을 쓰므로 이름만으로는 개발 스택인지 가릴 수 없다.

사용법:

    make dev-token                        # 첫 super_admin
    make dev-token EMAIL=a@example.com    # 특정 계정 (역할별 화면 검증)
    make dev-token ACCESS=1               # API 스크립트용 access 토큰 한 줄

기본 출력은 브라우저 콘솔에 붙여 넣을 한 줄이다. 화면은 refresh 토큰을 HttpOnly 쿠키로만 받고
access 토큰은 메모리에만 두므로, 이 줄은 refresh 쿠키를 심고 새로고침한다. 새로 열린 화면이 쿠키로
refresh를 한 번 불러 access 토큰을 받는다.

    fetch("/api/auth/logout",{method:"POST"}).finally(()=>{document.cookie=
      "reborn_refresh=<refresh>; path=/api/auth; samesite=strict";location.reload()})

실제 출력은 줄바꿈 없는 한 줄이다. 화면 주소(`http://localhost:3000` 등)의 콘솔에서 실행한다.
그 주소가 api의 `CORS_ORIGINS`에 없으면 refresh가 403이다.

먼저 로그아웃을 부르는 이유는 이전 로그인이 남긴 HttpOnly 쿠키다. 스크립트는 같은 이름의 HttpOnly
쿠키를 덮어쓸 수 없어서 쿠키 심기가 표시 없이 무시된다. 로그아웃 응답이 그 쿠키를 지운다.
스크립트가 심은 쿠키는 HttpOnly가 아니지만 첫 refresh 응답이 같은 이름의 HttpOnly 쿠키로 바꾼다.
서버에 새 경로를 두지 않으므로 운영에서 인증을 우회하는 경로도 생기지 않는다.

토큰은 로그 파일·커밋·이슈에 남기지 않는다. 만료되면 다시 발급하면 된다.
"""
import argparse
import asyncio
import sys
from datetime import UTC, datetime
from urllib.parse import urlsplit

from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import User
from api_gateway.security import create_access_token, create_refresh_token, session_end
from sqlalchemy import select

# 이 환경에서만 실행된다. 새 환경 이름을 추가하기 전에 그 환경에서 인증 우회를
# 허용해도 되는지 먼저 판단한다. 목록에 넣으면 그 환경에서 인증 우회를 허용하게 된다.
_ALLOWED_ENVS = ("local", "dev", "test")

#: refresh 쿠키 이름과 경로. 앱의 `services/auth/session_cookie.py`와 같다.
_REFRESH_COOKIE = "reborn_refresh"
_REFRESH_COOKIE_PATH = "/api/auth"

#: 늘 받는 DB 호스트. dev 시드의 확인(`_local_only.LOCAL_HOSTS`)과 같다.
_LOCAL_DB_HOSTS = ("localhost", "127.0.0.1", "::1")

#: 개발 compose의 DB 서비스 이름. `ENVIRONMENT`를 명시했을 때만 받는다.
_COMPOSE_DB_HOSTS = ("postgres",)


def db_host_refusal(database_url: str, *, environment_explicit: bool) -> str | None:
    """DB 호스트가 토큰을 발급해도 되는 곳이 아니면 거부 문구를, 맞으면 None을 돌려준다.

    거부 문구에는 호스트만 싣는다. URL 전체를 출력하면 DB 비밀번호가 터미널에 남는다.

    Args:
        database_url: 검사할 `DATABASE_URL`.
        environment_explicit: `ENVIRONMENT`를 env나 `.env`로 명시했으면 참. 기본값이면 거짓.
    """
    host = urlsplit(database_url).hostname or ""
    if host in _LOCAL_DB_HOSTS:
        return None
    if host in _COMPOSE_DB_HOSTS and environment_explicit:
        return None
    return f"거부: 로컬 DB가 아닙니다 (host={host or '(없음)'})"


def console_line(refresh: str) -> str:
    """브라우저 콘솔에 붙여 넣을 한 줄을 만든다. 이전 쿠키를 지우고 refresh 쿠키를 심는다."""
    cookie = f"{_REFRESH_COOKIE}={refresh}; path={_REFRESH_COOKIE_PATH}; samesite=strict"
    return (
        'fetch("/api/auth/logout",{method:"POST"}).finally(()=>{'
        f'document.cookie="{cookie}";location.reload()}})'
    )


async def _main(email: str | None, access_only: bool = False) -> int:
    """계정 하나에 세션 토큰을 발급해 표준출력에 낸다.

    Args:
        email: 대상 계정. None이면 가장 먼저 만들어진 활성 super_admin.
        access_only: 참이면 API 스크립트용 access 토큰만 한 줄로 낸다.

    Returns:
        종료 코드. 0은 성공, 1은 환경·DB 호스트 거부 또는 계정 없음이다.
    """
    if settings.environment not in _ALLOWED_ENVS:
        print(
            f"거부: ENVIRONMENT={settings.environment} — 이 도구는 "
            f"{'/'.join(_ALLOWED_ENVS)}에서만 돕니다.",
            file=sys.stderr,
        )
        return 1
    refusal = db_host_refusal(
        settings.database_url,
        environment_explicit="environment" in settings.model_fields_set,
    )
    if refusal:
        print(refusal, file=sys.stderr)
        return 1

    async with SessionLocal() as db:
        stmt = select(User).where(User.approval_status == "active")
        stmt = (
            stmt.where(User.email == email)
            if email
            else stmt.where(User.global_role == "super_admin")
        )
        user = (await db.scalars(stmt.order_by(User.created_at))).first()

    if user is None:
        who = email or "활성 super_admin"
        print(f"찾지 못함: {who}. `make seed-dev`로 계정을 만드세요.", file=sys.stderr)
        return 1

    if access_only:
        access, _ = create_access_token(user.user_id, user.global_role)
        print(access)
        return 0

    refresh = create_refresh_token(user.user_id)
    # 이 줄로 TTL 설정이 적용되었는지 확인한다. access가 15분이면 `.env`를 읽지 못해 기본값을 썼다.
    # 시각은 실행한 기계의 로컬 시간대로 시간대 이름과 함께 찍는다. UTC로 찍으면 매번 환산해야 한다.
    end = session_end(int(datetime.now(UTC).timestamp())).astimezone()
    print(
        f"# {user.email} ({user.global_role}) · access {settings.jwt_access_ttl_minutes}분"
        f" · 세션 {end:%Y-%m-%d %H:%M %Z}까지"
    )
    print(console_line(refresh))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", default=None, help="대상 계정 (없으면 첫 super_admin)")
    ap.add_argument(
        "--access", action="store_true", help="API 스크립트용 access 토큰만 한 줄로 낸다",
    )
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args.email, access_only=args.access)))
