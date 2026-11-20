"""dev 시드가 로컬 개발 DB에만 쓰도록 막는 확인.

시드는 계정 비밀번호를 바꾸고 작업을 제출·승인한다. 운영 DB에서 실행되면 되돌릴 수 없다.
`os.environ.setdefault`는 셸에 이미 있는 `DATABASE_URL`을 덮지 않으므로, 운영 주소가
export된 셸에서는 기본값 대신 그 주소에 붙는다. 그래서 시드는 서버 모듈을 import하기 전에
이 확인을 부른다.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

#: 시드를 허용하는 `ENVIRONMENT` 값. `dev_token.py`의 허용 목록과 같다.
ALLOWED_ENVS = ("local", "dev", "test")

#: 로컬 DB로 인정하는 호스트 이름.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def refuse_unless_local() -> None:
    """`DATABASE_URL`의 호스트가 로컬이 아니거나 `ENVIRONMENT`가 개발 값이 아니면 종료한다.

    호스트는 URL을 나눠 비교한다. 문자열 포함으로 비교하면 DB 이름이나 쿼리에 `localhost`가
    들어간 원격 주소도 통과한다. 거부 문구에는 호스트만 싣는다. URL 전체를 출력하면 비밀번호가
    터미널에 남는다.

    Raises:
        SystemExit: 호스트가 로컬이 아니거나 `ENVIRONMENT`가 허용 목록 밖인 경우.
    """
    host = urlsplit(os.environ.get("DATABASE_URL", "")).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"로컬 DB가 아니라서 dev 시드를 멈춥니다: host={host or '(없음)'}")
    env = os.environ.get("ENVIRONMENT", "")
    if env not in ALLOWED_ENVS:
        raise SystemExit(
            f"ENVIRONMENT={env or '(없음)'}이라서 dev 시드를 멈춥니다. "
            f"허용 값: {'/'.join(ALLOWED_ENVS)}"
        )
