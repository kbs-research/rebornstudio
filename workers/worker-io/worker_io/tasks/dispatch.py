"""무DB 잡을 큐에 넣고, 리스가 만료된 잡을 되돌리는 주기 태스크.

잡 페이로드는 DB 쪽이 모두 준비한다. 잡 본체는 별도 배포 단위(`workers/worker-jobs`)에 있고
페이로드만 받는다. 이 모듈은 잡 함수를 import하지 않고 이름으로 보낸다.

잡 메시지의 id는 그 시도의 펜싱 토큰이다. 회수가 되돌린 시도의 메시지를 그 id로 취소하면,
아직 큐에 있던 옛 메시지를 워커가 시작 전에 버린다. 워커는 DB를 읽지 않으므로
시작 전에 토큰이 낡았는지 확인할 수단은 이 취소 목록뿐이다.
"""
import logging

from reborn_contracts.queues import (
    DEIDENT_ONE,
    PUBLISH_ONE,
    QUEUE_DEIDENT,
    QUEUE_TRANSCODE,
    SCENE_SPLIT_ONE,
    TRANSCODE_ONE,
)

from ..celery_app import app
from ._session import _run_with_fresh_session

log = logging.getLogger(__name__)


def _send_job(name: str, job: dict, queue: str) -> None:
    """무DB 잡 하나를 태스크 이름으로 큐에 넣는다.

    이름으로 보내는 `send_task`에는 잡 태스크의 옵션 정보가 없으므로 `ignore_result`와
    `serializer`를 같이 넘긴다. `ignore_result`가 빠지면 기본값 False가 실려 잡 쪽 선언과
    메시지 헤더가 어긋난다. 그러면 결과 백엔드가 잡마다 결과 채널을 구독한다.

    메시지 id로 펜싱 토큰을 쓴다. 회수(`_revoke_messages`)가 그 id로 옛 메시지를 취소한다.
    """
    app.send_task(name, args=[job], queue=queue, ignore_result=True,
                  serializer=app.conf.task_serializer, task_id=job["token"])


def _revoke_messages(tokens: list[str]) -> None:
    """회수한 시도의 잡 메시지를 취소한다. 아직 큐에 있으면 워커가 시작 전에 버린다.

    취소는 실행 중인 워커에 보내는 방송이라 최선 노력이다. 놓친 옛 메시지가 실행돼도
    그 완료 보고는 펜싱 검사가 버린다. 이미 돌고 있는 시도는 멈추지 않는다(`terminate` 없음).
    비식별화는 그 시도의 결과를 이어받을 수 있어 멈추지 않는다.
    """
    app.control.revoke(tokens)
    log.info("회수한 시도의 잡 메시지 취소 %d건", len(tokens))


@app.task(name="worker_io.tasks.dispatch_transcodes")
def dispatch_transcodes() -> int:
    """30초마다 호출된다. pending 영상을 잡 페이로드로 만들어 q.transcode에 넣는다.

    필요한 값은 DB 쪽이 모두 준비한다.
    """
    from api_gateway.services.media.transcode import build_jobs
    jobs = _run_with_fresh_session(build_jobs)
    for job in jobs:
        _send_job(TRANSCODE_ONE, job, QUEUE_TRANSCODE)
    return len(jobs)


@app.task(name="worker_io.tasks.dispatch_scene_splits")
def dispatch_scene_splits() -> int:
    """1분마다 호출된다. 분할할 영상을 잡 페이로드로 만들어 q.transcode에 넣는다.

    MI 스택 프로젝트에는 대상이 없다. MI가 분석과 함께 분할하므로 이 태스크는
    아무 일도 하지 않는다. 처리 흐름은 스택과 관계없이 하나로 유지한다.
    """
    from api_gateway.services.media.scene_split import build_jobs
    jobs = _run_with_fresh_session(build_jobs)
    for job in jobs:
        _send_job(SCENE_SPLIT_ONE, job, QUEUE_TRANSCODE)
    return len(jobs)


@app.task(name="worker_io.tasks.reclaim_expired_scene_splits")
def reclaim_expired_scene_splits() -> int:
    """5분마다 호출된다. 리스가 만료된 씬 분할을 pending으로 되돌린다.

    되돌리기만 하면 다음 `dispatch_scene_splits`가 가져가 다시 보낸다.
    회수와 재투입을 같은 곳에서 하면 시도 상한을 두 곳에서 세게 된다.
    """
    from api_gateway.services.media.scene_split import reclaim_expired
    return _run_with_fresh_session(lambda s: reclaim_expired(s, revoke=_revoke_messages))


@app.task(name="worker_io.tasks.dispatch_publishes")
def dispatch_publishes() -> int:
    """30초마다 호출된다. publish 대기 영상을 잡으로 만들어 q.transcode에 넣는다.

    큐를 새로 만들지 않는다. publish도 인코딩 잡이고 같은 워커와 같은 CPU 예산을 쓴다.
    큐를 나누면 자원 상한이 둘로 나뉘어 어느 쪽도 정확히 지킬 수 없다.
    """
    from api_gateway.services.media.publish import build_jobs
    jobs = _run_with_fresh_session(build_jobs)
    for job in jobs:
        _send_job(PUBLISH_ONE, job, QUEUE_TRANSCODE)
    return len(jobs)


@app.task(name="worker_io.tasks.reclaim_expired_publishes")
def reclaim_expired_publishes() -> int:
    """5분마다 호출된다. 리스가 만료된 publish를 pending으로 되돌린다."""
    from api_gateway.services.media.publish import reclaim_expired
    return _run_with_fresh_session(lambda s: reclaim_expired(s, revoke=_revoke_messages))


@app.task(name="worker_io.tasks.dispatch_deidents")
def dispatch_deidents() -> int:
    """30초마다 호출된다. 비식별화 대기 영상을 잡으로 만들어 q.deident에 넣는다.

    큐를 따로 만든다. 비식별화는 GPU를 쓰는 워크로드다.
    같은 큐에 두면 GPU 워커가 인코딩 잡을 가져가 GPU가 유휴 상태가 된다.
    """
    from api_gateway.services.media.deident import build_jobs
    jobs = _run_with_fresh_session(build_jobs)
    for job in jobs:
        _send_job(DEIDENT_ONE, job, QUEUE_DEIDENT)
    return len(jobs)


@app.task(name="worker_io.tasks.reclaim_expired_deidents")
def reclaim_expired_deidents() -> int:
    """5분마다 호출된다. 리스가 만료된 비식별화를 pending으로 되돌린다."""
    from api_gateway.services.media.deident import reclaim_expired
    return _run_with_fresh_session(lambda s: reclaim_expired(s, revoke=_revoke_messages))


@app.task(name="worker_io.tasks.reclaim_expired_transcodes")
def reclaim_expired_transcodes() -> int:
    """5분마다 호출된다. 리스 만료(워커 비정상 종료)와 queued 유실을 pending으로 회수한다.

    default 큐에서 돈다. q.transcode에 두면 긴 인코딩에 밀려 회수가 늦어진다.
    """
    from api_gateway.services.media.transcode import reclaim_expired
    return _run_with_fresh_session(lambda s: reclaim_expired(s, revoke=_revoke_messages))
