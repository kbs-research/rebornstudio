"""Celery 앱 인스턴스, 큐 라우팅, beat 스케줄을 정의한다.

실행 예:
  celery -A worker_io.celery_app worker --loglevel=info
  celery -A worker_io.celery_app beat   --loglevel=info

테스트(pytest):
  CELERY_TASK_ALWAYS_EAGER=1이면 태스크를 inline으로 실행한다. 별도 worker 프로세스가 필요 없다.
"""
import contextlib
import logging
import os

from celery import Celery
from celery.schedules import crontab, schedule
from celery.signals import (
    after_setup_logger,
    heartbeat_sent,
    task_success,
    worker_process_init,
    worker_ready,
)
from reborn_contracts.queues import (
    BROKER_VISIBILITY_TIMEOUT_S,
    QUEUE_DEFAULT,
    TASK_ROUTES,
    WORKER_NODE_KEY_PREFIX,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

app = Celery(
    "reborn-worker-io",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["worker_io.tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    # 태스크 결과를 읽는 곳이 없다. 모두 beat 배치이거나 fire-and-forget이다.
    # 결과를 Redis에 쓰면 태스크마다 SET과 TTL이 낭비되고 큐 깊이 관찰이 어려워진다.
    # 결과가 필요한 태스크가 생기면 그 태스크에만 ignore_result=False를 명시한다.
    task_ignore_result=True,
    timezone="Asia/Seoul",
    enable_utc=True,
    # 테스트에서는 환경변수로 태스크를 바로 inline 실행한다.
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "").lower() == "1",
    task_eager_propagates=True,
    # 큐 라우팅 표와 태스크 이름은 worker-jobs와 같이 읽는다(`reborn_contracts.queues`).
    # 한쪽에만 적으면 다른 워커에 등록되지 않은 이름이나 큐로 보내게 된다.
    task_routes=TASK_ROUTES,
    task_default_queue=QUEUE_DEFAULT,
    # 긴 잡이 실행 중에 다시 전달되지 않게 한다. 두 앱이 같은 값을 쓴다.
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT_S},
)

# beat 스케줄.
app.conf.beat_schedule = {
    "maiu-reconciler-5s": {
        "task": "worker_io.tasks.run_maiu_reconciler",
        "schedule": schedule(run_every=5.0),
    },
    # 연동 점검: 아무도 화면을 보지 않을 때도 자격증명이 유효한지 확인한다.
    # 15분마다 돈다. 외부를 실제로 호출하므로(스토리지에 쓰기·읽기·삭제) 더 자주 돌리지 않는다.
    # 자격증명이 무효가 되면 늦어도 15분 안에 드러난다.
    "check-integrations-15min": {
        "task": "worker_io.tasks.check_integrations",
        "schedule": schedule(run_every=900.0),
    },
    # 자격증명 연결 점검. 위의 플랫폼 진단은 `.env`의 자격증명을 점검한다.
    # 이 점검은 조직이 등록한 연결 행을 점검한다. 알림은 그 조직 담당자에게 간다.
    # 주기는 같은 15분이지만 점검 대상이 달라 서로 겹치지 않는다.
    "check-connections-15min": {
        "task": "worker_io.tasks.check_connections",
        "schedule": schedule(run_every=900.0),
    },
    # 원본 정리는 급하지 않다. 분석이 끝난 뒤 최대 10분 안에 정리되면 충분하다.
    # 주기를 짧게 하면 대상이 없는 조회만 늘어난다.
    # 지우는 원본은 아래 체크섬 작업이 값을 기록한 뒤의 회차에 정리된다(`retention`).
    "dispose-finished-sources-10min": {
        "task": "worker_io.tasks.dispose_finished_sources",
        "schedule": schedule(run_every=600.0),
    },
    # 원본 체크섬. 파일 전체를 읽어야 하므로 한 번에 몇 건씩만 처리한다. 급하지 않다.
    # 값이 채워지기 전에 내보내도 `null`로 나가고, 리포트가 빈 건수를 센다(내보내기를 막지 않는다).
    "fill-source-checksums-10min": {
        "task": "worker_io.tasks.fill_source_checksums",
        "schedule": schedule(run_every=600.0),
    },
    # 고아 스테이징 정리. 보고 없이 끝난 시도의 파일만 지우고 하루 한 번이면 충분하다.
    # 버킷 목록을 읽으므로 감사 정리와 겹치지 않는 새벽 시각에 돈다.
    "sweep-orphan-staging-0430kst": {
        "task": "worker_io.tasks.sweep_orphan_staging",
        "schedule": crontab(hour=4, minute=30),
    },
    # 감사 로그 보존 정리. 매일 KST 03:00에 돈다. 보존 기간은 3년(1,095일)이다.
    # 삭제가 인덱스를 갱신하므로 사용량이 적은 새벽에 돈다.
    "purge-audit-events-0300kst": {
        "task": "worker_io.tasks.purge_audit_events",
        "schedule": crontab(hour=3, minute=0),
    },
    # 30초마다 대기 중인 내보내기를 처리한다(비동기 manifest 생성과 S3 업로드).
    "run-pending-exports-30s": {
        "task": "worker_io.tasks.run_pending_exports",
        "schedule": schedule(run_every=30.0),
    },
    # 10초마다 폴더째 등록을 처리한다. 사용자가 요청 직후 묶음 행을 보고 있으므로
    # 짧게 돈다. 대상이 없으면 부분 인덱스 SELECT 한 번이라 비용이 거의 없다.
    "run-pending-storage-imports-10s": {
        "task": "worker_io.tasks.run_pending_storage_imports",
        "schedule": schedule(run_every=10.0),
    },
    # 트랜스코딩 dispatcher(30초). pending을 queued로 바꾸고 transcode_one에 넣는다.
    # 스키마 기본값은 skipped라서 pending 행이 없으면 아무 일도 하지 않는다.
    # probe를 통과한 영상은 `media/pipeline.open_next`가 pending으로 연다.
    "dispatch-transcodes-30s": {
        "task": "worker_io.tasks.dispatch_transcodes",
        "schedule": schedule(run_every=30.0),
    },
    # 분석은 사람이 명시적으로 요청하므로 자동 분석 요청 주기 태스크는 두지 않는다.
    # 등록 즉시 분석을 요청하면 변환 전 원본이 나간다.
    # 리스 만료·queued 유실을 회수한다.
    # 유실된 잡이 멈춘 채 남지 않고 다음 회수 주기에 다시 투입된다.
    "reclaim-expired-transcodes-5min": {
        "task": "worker_io.tasks.reclaim_expired_transcodes",
        "schedule": schedule(run_every=300.0),
    },
    # publish(재생본·최종화) dispatcher와 회수. transcode와 같은 주기이고 이유도 같다.
    "dispatch-publishes-30s": {
        "task": "worker_io.tasks.dispatch_publishes",
        "schedule": schedule(run_every=30.0),
    },
    "reclaim-expired-publishes-5min": {
        "task": "worker_io.tasks.reclaim_expired_publishes",
        "schedule": schedule(run_every=300.0),
    },
    # 씬 분할. 자체 분할을 쓰는 스택의 잡이다.
    # MI 스택에서는 대상이 없어 아무 일도 하지 않는다.
    # 주기는 1분이다. 트랜스코딩과 같은 워커를 쓰므로 30초보다 길게 두어
    # 인코딩 잡이 밀리지 않게 한다.
    "dispatch-scene-splits-1min": {
        "task": "worker_io.tasks.dispatch_scene_splits",
        "schedule": schedule(run_every=60.0),
    },
    "reclaim-expired-scene-splits-5min": {
        "task": "worker_io.tasks.reclaim_expired_scene_splits",
        "schedule": schedule(run_every=300.0),
    },
    # 비식별화 dispatcher와 회수. 프로젝트 스위치가 꺼져 있으면 아무 일도 하지 않는다.
    "dispatch-deidents-30s": {
        "task": "worker_io.tasks.dispatch_deidents",
        "schedule": schedule(run_every=30.0),
    },
    "reclaim-expired-deidents-5min": {
        "task": "worker_io.tasks.reclaim_expired_deidents",
        "schedule": schedule(run_every=300.0),
    },
    # 1분마다 클립 씬 썸네일을 만든다. MAIU가 준 만료되는 URL 대신 영상에서 직접 프레임을 추출한다.
    # 새 MAIU 클립이 에셋으로 들어오면 다음 주기에 썸네일이 붙는다(수동 백필이 필요 없다).
    # 한 주기 처리 수와 시간 예산은 `clip_thumbnail.BATCH_LIMIT`·`TIME_BUDGET_S`가 정한다.
    # 예산이 주기보다 짧아야 다음 주기와 겹치지 않는다(`test_thumbnail_budgets_fit_the_beat`).
    "run-pending-clip-thumbnails-1min": {
        "task": "worker_io.tasks.run_pending_clip_thumbnails",
        "schedule": schedule(run_every=60.0),
    },
    # 1분마다 영상 포스터 썸네일을 만든다. 등록한 영상의 앞 프레임을 추출해
    # MAIU 클립 없이도 바로 대표 이미지를 둔다.
    "run-pending-video-thumbnails-1min": {
        "task": "worker_io.tasks.run_pending_video_thumbnails",
        "schedule": schedule(run_every=60.0),
    },
    # 캡션 품질검수 투입. 제출·승인은 검사를 기다리지 않고 커밋된다.
    # 그래서 이 주기가 검사 시작의 지연이다.
    # 검수자가 제출 직후 여는 화면에 결과가 늦게 뜨지 않도록 짧게 둔다.
    # 대상이 없으면 작업 표 조회 두 번으로 끝난다.
    # 실행 기록이 없는 건을 다시 보내는 회수도 이 태스크가 한다.
    "dispatch-caption-qc-5s": {
        "task": "worker_io.tasks.dispatch_caption_qc",
        "schedule": schedule(run_every=5.0),
    },
    # 1분마다 ffprobe 규격 게이트를 돌린다.
    # MI 요청 전에 규격 위반을 걸러 과금 낭비를 막는다.
    # probe는 Range 요청이라 가볍다(수백 ms). 그래서 주기를 짧게 해 등록 직후 빠르게 판정한다.
    "probe-pending-videos-1min": {
        "task": "worker_io.tasks.probe_pending_videos",
        "schedule": schedule(run_every=60.0),
    },
}

#: 이 주기(초) 이하로 도는 beat 태스크에 메시지 만료(`expires`)를 붙인다.
#: 워커가 멈춘 동안에도 beat는 계속 보낸다. 짧은 주기 태스크는 한 시간이면 수백 건이 쌓이고,
#: 워커가 돌아오면 같은 대상을 고르는 같은 일을 그만큼 되풀이한다.
#: 다음 주기의 메시지가 같은 일을 하므로 주기보다 오래된 메시지는 버려도 된다.
_EXPIRE_AT_MOST_S = 30.0
for _entry in app.conf.beat_schedule.values():
    _every = getattr(_entry["schedule"], "run_every", None)
    if _every is not None and _every.total_seconds() <= _EXPIRE_AT_MOST_S:
        _entry.setdefault("options", {})["expires"] = _every.total_seconds()


#: 태스크마다 INFO 한 줄을 찍는 celery 로거. 수신(`strategy`), 성공(`trace`), beat 발송(`beat`)이다.
#: 5~10초 주기 태스크가 여럿이라 이 줄만으로 분당 백 줄을 넘는다. 로그 회전 상한(50MB×5) 안에서
#: 며칠치만 남아 장애를 되짚을 기록이 먼저 밀려난다. 태스크 실패는 `celery.app.trace`가 ERROR로
#: 찍으므로 WARNING으로 올려도 남는다.
_ROUTINE_TASK_LOGGERS = ("celery.app.trace", "celery.worker.strategy", "celery.beat")


@after_setup_logger.connect
def _quiet_polling_http_logs(**_: object) -> None:
    """httpx와 태스크 일상 로그를 WARNING 이상만 남긴다. 요청·태스크마다 찍히는 INFO 줄을 끈다.

    이 프로세스의 httpx 요청은 MAIU 폴링과 연동 점검이 보낸다. httpx는 요청마다 INFO 한 줄을
    찍는다. 그대로 두면 같은 URL이 반복되는 줄에 단계 전환·완료·실패 줄이 묻힌다.
    WARNING 이상은 그대로 나온다. 비식별화 폴링 쪽 설정은 `worker_jobs.celery_app`에 있다.

    태스크 수신·성공 줄과 beat 발송 줄도 끈다(`_ROUTINE_TASK_LOGGERS`). 태스크 오류는 그대로 남는다.

    celery가 로깅을 설정한 뒤에 적용해야 한다. 모듈 최상단에서 설정하면
    celery 설정이 나중에 덮어쓴다.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for name in _ROUTINE_TASK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@worker_process_init.connect
def _dispose_inherited_pool(**_: object) -> None:
    """fork된 자식 프로세스가 부모의 DB 커넥션을 물려받지 않도록 풀을 비운다.

    celery prefork는 부모 프로세스를 fork해 자식을 만든다. 부모가 import할 때 만든
    `api_gateway.database.engine`의 커넥션 풀은 열린 TCP 소켓째 자식에게 복제된다.
    그러면 여러 자식이 같은 소켓에 동시에 쓰고, asyncpg가 다음 오류를 낸다.

        InterfaceError: cannot perform operation: another operation is in progress
        [SQL: SELECT pg_try_advisory_lock($1, hashtext(current_database()))]

    이 예외가 나면 reconciler가 매 주기 실패한다. 그러면 MI 분석이 끝나도 결과를 회수하지 못해
    파이프라인이 멈춘다. 로컬에서 프로세스 하나로 돌리면 재현되지 않고 로그에만 드러난다.

    주의: 이 소켓은 부모가 쓰는 소켓이다. 자식이 정상 종료 절차(close)를 밟으면 부모가 쓰는
    연결에 종료 패킷을 보내 부모 쪽 연결까지 망가뜨린다. 그래서 닫지 않고 버린다
    (`Engine.dispose(close=False)`). `Pool.dispose()`는 유휴 커넥션마다 `close()`를 부른다.

    태스크가 쓰는 세션은 `_run_with_fresh_session`이 태스크마다 새로 만든다(NullPool).
    advisory lock도 `services/joblock.single_runner`가 호출마다 NullPool 전용 엔진을 만든다.
    워커는 모듈 전역 engine을 쓰지 않지만, 나중에 쓰게 되어도 fork 뒤 공유 소켓이 생기지 않게
    자식이 시작할 때 풀을 버린다.
    """
    from api_gateway.database import engine

    engine.sync_engine.dispose(close=False)


# 주기 태스크의 마지막 성공 시각을 Redis에 남긴다.
# 성공 시각이 없으면 주기 태스크가 매번 예외로 실패해도 화면·지표에 그 사실이 드러나지 않는다.
# 성공만 기록한다. 실패로도 갱신하면 계속 실패하는 태스크가 정상으로 보인다.
_BEAT_KEY_PREFIX = "reborn:beat:"
_BEAT_TASKS = frozenset(e["task"] for e in app.conf.beat_schedule.values())
#: crontab 항목의 주기(초). 이 스케줄의 crontab 항목은 모두 하루 한 번이다.
_CRONTAB_PERIOD_S = 86400


def _period_s(entry: dict) -> int:
    """beat 항목 하나의 주기(초)를 돌려준다. 진단이 마지막 성공이 늦었는지 재는 기준이다."""
    every = getattr(entry["schedule"], "run_every", None)
    if isinstance(entry["schedule"], crontab) or every is None:
        return _CRONTAB_PERIOD_S
    return int(every.total_seconds())


#: 주기 태스크별 주기(초). 성공 기록에 같이 써서 api가 태스크마다 다른 기준으로 늦음을 잰다.
_BEAT_PERIODS = {e["task"]: _period_s(e) for e in app.conf.beat_schedule.values()}
#: 성공 시각 기록에 쓰는 Redis 클라이언트. 처음 쓸 때 만들고 프로세스 안에서 다시 쓴다.
#: 성공마다 새로 만들면 5초 주기 태스크마다 연결을 새로 열고 닫는다.
#: redis-py 연결 풀은 fork된 자식에서 자기 연결을 새로 열므로 prefork 자식끼리 소켓을 나누지 않는다.
_beat_redis = None


def _beat_client():
    """성공 시각 기록용 Redis 클라이언트를 돌려준다. 없으면 만든다."""
    global _beat_redis
    if _beat_redis is None:
        import redis
        _beat_redis = redis.from_url(REDIS_URL, socket_timeout=2)
    return _beat_redis


@task_success.connect
def _mark_beat_alive(sender=None, **_: object) -> None:
    """주기 태스크가 성공하면 `reborn:beat:<task>`에 `<epoch> <주기 초>`를 쓴다.

    api가 경과 시간을 그 태스크 주기와 견준다. 주기를 같이 쓰지 않으면 api는 5초 태스크와
    하루 1회 태스크를 같은 기준으로 재게 되어, 짧은 태스크 하나가 멈춘 것을 놓친다.

    beat schedule에 있는 태스크만 기록한다. 이벤트성 태스크(transcode_complete 등)는
    오래 오지 않아도 정상이다. 같은 기준으로 재면 알림이 잘못 뜬다.

    Redis가 없거나 불안정해도 태스크를 실패시키지 않는다. 관측 기록의 실패로 작업을 막지 않는다.
    """
    name = getattr(sender, "name", None)
    if name not in _BEAT_TASKS:
        return
    try:
        import time

        _beat_client().set(f"{_BEAT_KEY_PREFIX}{name}",
                           f"{int(time.time())} {_BEAT_PERIODS[name]}")
    except Exception:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
        pass


#: 노드 동작 기록 사이의 최소 간격(초). 이벤트 하트비트는 2초마다 오므로 그때마다 쓰지 않는다.
_NODE_MARK_EVERY_S = 30
#: 이 프로세스의 노드 역할(노드 이름의 `@` 앞부분)과 마지막으로 쓴 시각.
_node = {"role": None, "at": 0.0}


def _mark_node(force: bool = False) -> None:
    """이 워커 노드가 동작 중이라는 기록(`reborn:node:<역할>`)에 epoch을 쓴다.

    Redis가 없거나 불안정해도 예외를 내지 않는다. 관측 기록의 실패로 작업을 막지 않는다.
    """
    import time

    now = time.time()
    if _node["role"] is None or (not force and now - _node["at"] < _NODE_MARK_EVERY_S):
        return
    _node["at"] = now
    # 관측 실패가 작업을 막지 않는다.
    with contextlib.suppress(Exception):
        _beat_client().set(f"{WORKER_NODE_KEY_PREFIX}{_node['role']}", str(int(now)))


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
