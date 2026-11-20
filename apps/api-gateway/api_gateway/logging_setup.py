"""앱 로깅 설정과 모든 로그 줄에 붙는 요청 컨텍스트.

이 모듈은 코드에 이미 있는 로그를 출력한다.
로깅 설정이 없으면 파이썬 기본 동작대로 `WARNING` 미만의 로그가 모두 버려진다.
`RequestContextMiddleware`의 요청 로그(`rid`·경로·상태·소요)도 `INFO`라서 함께 버려진다.
컨테이너 로그의 `INFO: … GET /healthz 200`은 uvicorn 자체 access log다.
그래서 이 줄로는 앱 로그가 나오는지 판단할 수 없다.

요청 컨텍스트가 없으면 한 요청이 남긴 로그 줄을 한데 모을 수 없다.
그래서 미들웨어가 발급하는 `rid`를 `contextvars`에 담아 모든 로그 줄에 자동으로 붙인다.
`grep rid=abc123` 한 번으로 한 요청의 로그를 모두 찾는다.

`contextvars`는 asyncio에서 요청마다 값을 따로 유지한다.
모듈 전역 변수에 담으면 동시 요청이 서로의 값을 덮어쓴다.
"""
import logging
import os
from contextvars import ContextVar

# 요청 단위 값. 미들웨어와 인증 의존성이 채우고 필터가 읽는다. 요청 밖(워커·기동)에서는 비어 있다.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
actor_var: ContextVar[str] = ContextVar("actor", default="")


class ContextFilter(logging.Filter):
    """모든 로그 레코드에 요청 컨텍스트를 붙인다. 값이 없으면 `-`를 쓴다.

    포맷터는 레코드에 없는 필드를 만나면 실패하므로 필터에서 채운다.
    여기서 항상 채워 두면 어느 로거(서비스·라이브러리)에서 온 줄도 같은 형식이 된다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """레코드에 `rid`와 `actor`를 채운다. 레코드는 항상 통과시킨다."""
        record.rid = request_id_var.get() or "-"
        record.actor = actor_var.get() or "-"
        return True


# 로그 한 줄의 형식. `rid`와 `actor`는 `ContextFilter`가 채운다.
_FMT = "%(asctime)s %(levelname)-7s %(name)s rid=%(rid)s actor=%(actor)s | %(message)s"


def configure(level: str | None = None) -> None:
    """앱 로깅을 켠다. 기동할 때 한 번만 부른다.

    uvicorn의 access 로그는 uvicorn 자체 설정을 그대로 둔다.
    그래서 앱 요청 로그(`reborn.access`)와 겹쳐 요청마다 두 줄이 나온다.
    로그를 잃지 않으려고 둘 다 둔다.

    Args:
        level: 로그 레벨. 없으면 `LOG_LEVEL` 환경변수를 쓰고, 환경변수도 없으면 `INFO`를 쓴다.
            운영에서 로그가 많으면 `WARNING`으로 올리고, 자세히 볼 때는 `DEBUG`로 내린다.
    """
    # `settings`를 import하지 않고 환경변수를 직접 읽는다.
    # config 자체는 다른 모듈을 가져오지 않지만 기동 순서와 얽히지 않게 한다.
    # `config.log_level`도 같은 키를 쓴다.
    lv = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    # 두 번 부르면 핸들러가 쌓여 같은 줄이 여러 번 나오므로 이 모듈이 단 핸들러만 교체한다.
    for h in list(root.handlers):
        if getattr(h, "_reborn", False):
            root.removeHandler(h)
    handler._reborn = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(lv)

    # 라이브러리 로그는 따로 WARNING으로 올린다. 앱 레벨을 DEBUG로 내렸을 때
    # SQL·HTTP 로그가 앱 로그를 가리지 않게 한다.
    for noisy, noisy_level in (
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("botocore", logging.WARNING),
        ("boto3", logging.WARNING),
        ("urllib3", logging.WARNING),
        ("sqlalchemy.engine", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)
