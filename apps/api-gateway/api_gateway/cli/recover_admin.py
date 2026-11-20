"""최고관리자의 1회용 비밀번호 재설정 링크를 서버에서 발급한다.

메일 발송 기능이 없어 재설정 링크는 최고관리자가 화면에서 발급한다. 최고관리자 본인이 비밀번호를
잃으면 링크를 발급할 사람이 없으므로, 서버에 접속할 수 있는 운영자가 이 명령으로 발급한다.
서버 셸에 들어올 수 있으면 이미 DB 전체를 읽고 쓸 수 있으므로 이 명령이 넓히는 권한은 없다.

대상은 활성 최고관리자로 한정한다. 일반 사용자의 링크는 최고관리자가 화면에서 발급한다.
발급은 화면과 같은 함수(`services.auth.admin.issue_reset_token`)가 하므로 유효 시간·1회 사용·
이전 링크 무효화 규칙이 같다. 감사 기록에는 발급자 없이 `via: server_command`가 남는다.

쓰는 법(운영 서버):

    infra/deploy/dc.sh exec api uv run --package api-gateway \\
        python -m api_gateway.cli.recover_admin [--email 주소] [--base-url https://서비스주소]

링크는 이 명령의 출력에만 나온다. DB에는 토큰의 해시만 남아 다시 볼 수 없으므로,
잃어버리면 명령을 다시 실행해 새 링크를 받는다. 새로 발급하면 앞서 발급한 링크는 무효가 된다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from urllib.parse import quote

#: 성공.
EXIT_OK = 0
#: 대상을 정하지 못한 경우(최고관리자가 여럿인데 이메일이 없음, 해당 계정 없음 등).
EXIT_NO_TARGET = 2


async def recover(email: str | None, base_url: str | None) -> int:
    """대상 최고관리자를 고르고 재설정 링크를 발급해 출력한다.

    이메일이 없으면 활성 최고관리자가 한 명일 때만 그 계정을 고른다. 여럿이면 목록을 보이고 멈춘다.
    무엇을 골랐는지 사람이 확인하지 않은 채 다른 관리자의 링크를 발급하지 않기 위해서다.

    Args:
        email: 대상 최고관리자의 이메일. 대소문자를 구분하지 않는다.
        base_url: 서비스 주소. 주면 전체 링크를, 없으면 서비스 주소 뒤에 붙일 경로를 출력한다.

    Returns:
        종료 코드(`EXIT_OK`·`EXIT_NO_TARGET`).
    """
    from sqlalchemy import func, select

    from api_gateway.database import SessionLocal
    from api_gateway.models import User
    from api_gateway.services.auth.admin import issue_reset_token

    async with SessionLocal() as db:
        admins = list((await db.scalars(
            select(User).where(User.global_role == "super_admin").order_by(User.email)
        )).all())
        active = [u for u in admins if u.approval_status == "active"]
        if email:
            user = next((u for u in admins if u.email.lower() == email.lower()), None)
            if user is None:
                known = await db.scalar(
                    select(User.user_id).where(func.lower(User.email) == email.lower()))
                if known:
                    _err(f"{email}은 최고관리자 계정이 아닙니다.")
                    _err("일반 사용자의 재설정 링크는 최고관리자가 "
                         "사용자 관리 화면에서 발급합니다.")
                else:
                    _err(f"{email} 계정이 없습니다.")
                _list(active)
                return EXIT_NO_TARGET
            if user.approval_status != "active":
                _err(f"{email}은 정지된 계정입니다. 비밀번호를 바꿔도 로그인할 수 없습니다.")
                _list(active)
                return EXIT_NO_TARGET
        elif len(active) == 1:
            user = active[0]
        else:
            if active:
                _err("최고관리자가 여럿입니다. --email로 대상을 고릅니다.")
                _list(active)
            else:
                _err("활성 최고관리자가 없습니다.")
                _err("계정이 하나도 없으면 브라우저로 접속해 최초 설정 화면에서 관리자를 만듭니다.")
            return EXIT_NO_TARGET

        issued = await issue_reset_token(db, user, issued_by=None)

    # 토큰은 주소 조각에 싣는다. 조각은 브라우저가 서버로 보내지 않아
    # 웹 서버 접근 로그에 남지 않는다.
    path = f"/password-reset#token={quote(issued.token, safe='')}"
    link = f"{base_url.rstrip('/')}{path}" if base_url else path
    print(f"대상: {issued.email}")
    print(f"만료: {issued.expires_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
          f"({issued.valid_minutes}분, 한 번만 쓸 수 있음)")
    print(f"링크: {link}")
    if not base_url:
        print("서비스 주소 뒤에 위 경로를 붙여 브라우저로 엽니다.")
    return EXIT_OK


def _err(msg: str) -> None:
    """안내문을 표준 오류로 출력한다. 표준 출력에는 발급 결과만 싣는다."""
    print(msg, file=sys.stderr)


def _list(active: list) -> None:
    """고를 수 있는 활성 최고관리자의 이메일을 출력한다."""
    if active:
        _err("활성 최고관리자: " + ", ".join(u.email for u in active))


def main(argv: list[str] | None = None) -> int:
    """명령행 인자를 읽고 `recover`를 실행한다."""
    parser = argparse.ArgumentParser(
        prog="python -m api_gateway.cli.recover_admin",
        description="최고관리자의 1회용 비밀번호 재설정 링크를 발급합니다.",
    )
    parser.add_argument(
        "--email", help="대상 최고관리자 이메일. 최고관리자가 한 명이면 생략할 수 있습니다")
    parser.add_argument(
        "--base-url",
        help="서비스 주소(예: https://studio.example.com). 주면 전체 링크를 출력합니다")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.email, args.base_url))


async def _run(email: str | None, base_url: str | None) -> int:
    """`recover`를 실행하고 DB 커넥션 풀을 닫는다. 닫지 않으면 종료할 때 경고가 남는다."""
    from api_gateway.database import engine

    try:
        return await recover(email, base_url)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
