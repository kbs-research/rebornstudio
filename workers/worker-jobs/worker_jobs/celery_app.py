"""무DB 잡 워커의 Celery 앱을 정의한다.

실행 예:
  celery -A worker_jobs.celery_app worker -l info -Q q.transcode --prefetch-multiplier 1

이 앱에는 beat가 없다. 잡을 투입·회수하는 주기 태스크와 회신을 DB에 반영하는 태스크는
`worker_io.celery_app`에 있다. 두 앱은 브로커와 `reborn_contracts.queues`의 이름·라우팅 표만
공유한다.
브로커·직렬화·결과 설정은 worker-io 앱과 같게 둔다. 한쪽만 바꾸면 서로의 메시지를 읽지 못한다.
"""
import logging
import os

from celery import Celery
from celery.signals import after_setup_logger, heartbeat_sent, worker_ready
from reborn_contracts.queues import (
    BROKER_VISIBILITY_TIMEOUT_S,
    QUEUE_DEFAULT,
    TASK_ROUTES,
    WORKER_NODE_KEY_PREFIX,
)

#: 브로커이자 결과 백엔드. worker-io와 같은 Redis를 가리켜야 회신이 닿는다.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

app = Celery(
    "reborn-worker-jobs",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["worker_jobs.jobs"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    # 잡의 결과는 아무도 읽지 않는다. 회신은 결과 백엔드가 아니라 회신 태스크로 보낸다.
    task_ignore_result=True,
    timezone="Asia/Seoul",
    enable_utc=True,
    # 테스트에서는 환경변수로 태스크를 바로 inline 실행한다.
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "").lower() == "1",
    task_eager_propagates=True,
    task_routes=TASK_ROUTES,
    task_default_queue=QUEUE_DEFAULT,
    # 긴 잡이 실행 중에 다시 전달되지 않게 한다. 두 앱이 같은 값을 쓴다.
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_S},
)


#: 태스크마다 INFO 한 줄을 찍는 celery 로거. 수신(`strategy`)과 성공(`trace`)이다.
#: 잡 시작·완료는 잡 코드가 영상 id와 함께 찍으므로 이 줄은 같은 사실을 한 번 더 남긴다.
#: 태스크 실패는 `celery.app.trace`가 ERROR로 찍으므로 WARNING으로 올려도 남는다.
_ROUTINE_TASK_LOGGERS = ("celery.app.trace", "celery.worker.strategy")


@after_setup_logger.connect
def _quiet_http_logs(**_: object) -> None:
    """httpx와 태스크 일상 로그를 WARNING 이상만 남긴다. 요청·태스크마다 찍히는 INFO 줄을 끈다.

    httpx는 요청마다 주소 전체를 INFO로 찍는다. 잡의 입력·산출 주소는 presigned URL이라
    서명이 로그에 남으면 유효 시간 동안 로그를 읽은 사람이 그 객체를 읽거나 덮어쓸 수 있다.
    실패는 잡 러너가 서명을 뺀 사유로 남긴다. WARNING 이상은 그대로 나온다.

    태스크 수신·성공 줄도 끈다(`_ROUTINE_TASK_LOGGERS`). 태스크 오류는 그대로 남는다.

    celery가 로깅을 설정한 뒤에 적용해야 한다. 모듈 최상단에서 설정하면
    celery 설정이 나중에 덮어쓴다.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for name in _ROUTINE_TASK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


#: 노드 동작 기록 사이의 최소 간격(초). worker-io 앱과 같은 규칙이다.
_NODE_MARK_EVERY_S = 30
#: 이 프로세스의 노드 역할(노드 이름의 `@` 앞부분), 마지막으로 쓴 시각, Redis 클라이언트.
_node: dict = {"role": None, "at": 0.0, "client": None}


def _mark_node(force: bool = False) -> None:
    """이 워커 노드가 동작 중이라는 기록(`reborn:node:<역할>`)에 epoch을 쓴다.

    이 워커는 beat 태스크를 돌리지 않아 주기 태스크 성공 기록이 없다. 그래서 진단이 이 워커가
    멈춘 것을 알려면 따로 남겨야 한다. Redis 실패는 삼킨다. 관측 기록이 작업을 막지 않는다.
    """
    import time

    now = time.time()
    if _node["role"] is None or (not force and now - _node["at"] < _NODE_MARK_EVERY_S):
        return
    _node["at"] = now
    try:
        if _node["client"] is None:
            import redis
            _node["client"] = redis.from_url(REDIS_URL, socket_timeout=2)
        _node["client"].set(f"{WORKER_NODE_KEY_PREFIX}{_node['role']}", str(int(now)))
    except Exception:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
        pass


@worker_ready.connect
def _node_ready(sender=None, **_: object) -> None:
    """워커가 기동을 마치면 노드 역할을 정하고 첫 동작 기록을 남긴다."""
    hostname = getattr(sender, "hostname", None) or ""
    _node["role"] = hostname.split("@", 1)[0] or None
    _mark_node(force=True)


@heartbeat_sent.connect
def _node_alive(**_: object) -> None:
    """이벤트 하트비트마다 불린다. `_NODE_MARK_EVERY_S`마다 동작 기록을 갱신한다."""
    _mark_node()
