"""연결 시크릿을 새 암호화 키(`CREDENTIALS_KEY`)로 다시 암호화한다. 키 교체 절차의 가운데 단계다.

`CREDENTIALS_KEY`에 새 키, `CREDENTIALS_KEY_PREVIOUS`에 이전 키를 넣고
컨테이너를 다시 만든 뒤에 실행한다.
기본은 드라이런이라 건수만 세고 바꾸지 않는다.
`--apply`를 주면 이전 키로 된 암호문만 새 키로 바꾼다.
바꾸는 행과 감사 기록(`system.credentials_rekeyed`)은 한 트랜잭션으로 커밋한다.
출력은 건수 JSON 한 줄이고 시크릿 값과 암호문은 출력하지 않는다.
다시 실행해도 안전하다(새 키 행은 건너뛴다).

쓰는 법(운영 서버):

    infra/deploy/dc.sh exec -T api uv run --package api-gateway \\
        python -m api_gateway.cli.rotate_credentials_key [--apply]

종료 코드는 어느 키로도 풀지 못한 행(`unreadable`)이 있으면 1이다. 그 행은 바꾸지 않는다.
이전 키를 목록에서 빼기 전에 `previous`가 0인지 드라이런으로 확인한다.
절차는 `infra/deploy/README.md`에 있다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

#: 모든 행이 새 키나 이전 키로 풀린 경우.
EXIT_OK = 0
#: 어느 키로도 풀지 못한 행이 있는 경우.
EXIT_UNREADABLE = 1


async def _run(apply: bool) -> dict[str, int]:
    """세션 하나로 재암호화를 실행하고 건수를 돌려준다."""
    from api_gateway.database import SessionLocal
    from api_gateway.services import crypto

    async with SessionLocal() as db:
        return await crypto.rekey_connections(db, apply=apply)


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 재암호화를 실행하고 건수 JSON 한 줄을 출력한다.

    Returns:
        종료 코드(`EXIT_OK`·`EXIT_UNREADABLE`).
    """
    ap = argparse.ArgumentParser(description="연결 시크릿을 새 암호화 키로 다시 암호화한다")
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다. 없으면 건수만 센다")
    args = ap.parse_args(argv)
    counts = asyncio.run(_run(args.apply))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **counts}, ensure_ascii=False))
    return EXIT_UNREADABLE if counts["unreadable"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
