"""Celery 태스크가 inline(eager) 모드에서 정상 호출되는지 확인한다."""
from worker_io.celery_app import app
from worker_io.tasks import run_maiu_reconciler


def test_celery_app_loaded():
    """celery_app 인스턴스와 reconciler·리스 회수 태스크가 등록됐는지 확인한다."""
    assert app.main == "reborn-worker-io"
    task_names = set(app.tasks.keys())
    assert "worker_io.tasks.run_maiu_reconciler" in task_names
    assert "worker_io.tasks.reclaim_expired_transcodes" in task_names


def test_beat_schedule_registered():
    """5초 reconciler와 5분 리스 회수 스케줄이 등록됐는지 확인한다."""
    s = app.conf.beat_schedule
    assert "maiu-reconciler-5s" in s
    assert "reclaim-expired-transcodes-5min" in s
    # schedule의 run_every가 timedelta 형식인지 확인한다
    assert s["maiu-reconciler-5s"]["schedule"].run_every.total_seconds() == 5.0
    assert s["reclaim-expired-transcodes-5min"]["schedule"].run_every.total_seconds() == 300.0


def test_work_holds_have_no_reclaim_task():
    """라벨링·검수 보유는 자동으로 풀지 않는다. 주기 회수 태스크와 beat 항목이 없다.

    보유는 제출·판정·신고, 멤버 제거·나가기로만 풀린다. 파이프라인 리스 회수는 따로 남는다.
    """
    names = set(app.tasks.keys())
    assert "worker_io.tasks.reclaim_expired_work_items" not in names
    assert "worker_io.tasks.release_expired_review_claims" not in names
    beats = {e["task"] for e in app.conf.beat_schedule.values()}
    assert not {"worker_io.tasks.reclaim_expired_work_items",
                "worker_io.tasks.release_expired_review_claims"} & beats


def test_run_maiu_reconciler_eager():
    """eager 모드에서 inline으로 실행하면 정상 형태 세 가지 중 하나를 반환한다.

    ① stage dict(정상 사이클)
    ② {"skipped":1}(advisory lock 경합, 같은 DB에 다른 워커가 붙어 있을 때)
    ③ {"slot":"deferred", ...}(11회 중 1번 오는 deferred 슬롯).
    슬롯 카운터는 Redis에 있다. 같은 Redis에 붙은 다른 워커가 카운터를 올려 두면 테스트가
    약 1/11 확률로 deferred 슬롯을 받는다. 그래서 ③을 빼면 테스트가 간헐적으로 실패한다.
    """
    result = run_maiu_reconciler()
    assert isinstance(result, dict)
    if "skipped" in result or result.get("slot") == "deferred":
        return
    for key in ("registered", "analyzed", "summarized", "deferred"):
        assert key in result


def test_scene_split_tasks_are_registered_and_routed():
    """씬 분할 태스크 4종이 등록되고 정해진 큐로 가는지 확인한다.

    큐를 빠뜨린 워커는 그 큐의 태스크를 소비하지 않는다. 잡은 쌓이는데 화면은
    "자르는 중"으로 표시한다. 그래서 라우팅은 계약이다(queues.md).
    """
    routes = app.conf.task_routes
    # 잡 본체(`scene_split_one`)는 worker-jobs 앱이 등록한다. 이 앱은 투입·회수·회신 셋을 등록한다.
    for name in ("dispatch_scene_splits",
                 "reclaim_expired_scene_splits", "scene_split_complete"):
        assert f"worker_io.tasks.{name}" in app.tasks

    # 검출은 원본 전체를 디코딩한다. 자원 사용 양상이 인코딩과 같아서 같은 큐를 쓴다.
    assert routes["worker_io.tasks.scene_split_one"]["queue"] == "q.transcode"
    # DB를 쓰는 태스크는 모두 default다. q.transcode에 두면 긴 인코딩에 밀려 늦게 처리된다.
    for name in ("dispatch_scene_splits", "reclaim_expired_scene_splits",
                 "scene_split_complete"):
        assert routes[f"worker_io.tasks.{name}"]["queue"] == "default"


def test_scene_split_beat_is_slower_than_transcode_dispatch():
    """씬 분할 dispatch 주기는 1분이다. 같은 워커를 쓰므로 인코딩 잡이 밀리지 않게 한다."""
    s = app.conf.beat_schedule
    assert s["dispatch-scene-splits-1min"]["schedule"].run_every.total_seconds() == 60.0
    assert s["reclaim-expired-scene-splits-5min"]["schedule"].run_every.total_seconds() == 300.0


def test_transcode_dispatch_beat_is_30_seconds():
    """변환 투입 주기는 30초다.

    이 주기가 업로드 완료부터 변환 시작까지의 최대 지연이다. 늘리면 준비가 그만큼 늦어진다.
    줄이면 DB 조회가 늘어난다. 이 테스트는 주기가 의도치 않게 바뀌는 것을 막는다.
    주기를 바꾸려면 이 테스트도 함께 바꾼다.
    """
    s = app.conf.beat_schedule
    entry = s["dispatch-transcodes-30s"]
    assert entry["task"] == "worker_io.tasks.dispatch_transcodes"
    assert entry["schedule"].run_every.total_seconds() == 30.0
    # 회수 주기는 리스(5분)와 맞춘다. 투입 주기보다 길어야 막 투입한 잡을 회수하지 않는다.
    assert s["reclaim-expired-transcodes-5min"]["schedule"].run_every.total_seconds() == 300.0


def test_completion_tasks_take_reports_without_facts():
    """구 워커의 회신(측정값 없음)도 받는다. 새 인자는 선택 인자다.

    필수로 두면 배포 중 큐에 남은 이전 메시지가 인자 오류로 실패한다. 그 영상은 리스 만료까지
    멈춰 있다가 처음부터 다시 돈다(`transcode_heartbeat`의 `done_seconds`와 같은 이유).
    """
    import inspect

    from worker_io.tasks import publish_complete, transcode_complete

    for task in (transcode_complete, publish_complete):
        assert inspect.signature(task.run).parameters["outputs"].default is None


def test_job_tasks_are_sent_by_name_not_registered_here():
    """무DB 잡은 이 앱에 등록하지 않고 회신 태스크는 모두 등록한다. 라우팅 표는 계약 모듈의 표다.

    잡이 여기에도 등록되면 같은 이름이 두 워커에 등록된다. 그러면 어느 이미지의 코드가 도는지가
    큐 설정에 따라 달라진다. 회신이 빠지면 잡이 끝나도 DB에 반영되지 않는다.

    celery는 한 프로세스의 앱끼리 태스크를 공유하므로 이름 대신 정의한 모듈로 구분한다.
    """
    from reborn_contracts.queues import CALLBACK_TASKS, JOB_TASKS, TASK_ROUTES

    own = {n for n, t in app.tasks.items() if t.__module__.startswith("worker_io.")}
    assert not JOB_TASKS & own
    assert own >= CALLBACK_TASKS
    assert app.conf.task_routes is TASK_ROUTES


def test_every_routed_task_has_exactly_one_home():
    """라우팅 표의 이름마다 그 태스크를 등록한 앱이 정확히 하나 있다.

    queues.md의 규칙을 테스트로 보장한다.

    worker-jobs는 별도 패키지라 설치되지 않은 환경에서는 건너뛴다. 워크스페이스 전량 설치
    (`uv sync --all-packages`, CI와 같다)에서는 돈다.
    """
    import pytest
    from reborn_contracts.queues import TASK_ROUTES

    jobs_app = pytest.importorskip("worker_jobs.celery_app").app
    jobs_app.loader.import_default_modules()
    # celery는 한 프로세스의 앱끼리 태스크를 공유하므로 정의한 모듈로 구분한다.
    io_names = {n for n, t in app.tasks.items() if t.__module__.startswith("worker_io.")}
    job_names = {n for n, t in jobs_app.tasks.items()
                 if t.__module__.startswith("worker_jobs.")}
    assert not io_names & job_names
    assert set(TASK_ROUTES) <= io_names | job_names, set(TASK_ROUTES) - io_names - job_names


def test_dispatch_sends_jobs_by_name_with_the_same_options(monkeypatch):
    """dispatch는 잡을 이름으로 보내되 잡 태스크가 선언한 것과 같은 큐·옵션을 싣는다.

    `ignore_result=True`가 빠지면 메시지 헤더가 잡 선언과 어긋난다. 그러면 결과 백엔드가 잡마다
    결과 채널을 구독한다. 메시지 id는 펜싱 토큰이다. 회수가 그 id로 옛 메시지를 취소한다.
    """
    from reborn_contracts.queues import (
        DEIDENT_ONE,
        PUBLISH_ONE,
        SCENE_SPLIT_ONE,
        TRANSCODE_ONE,
    )
    from worker_io.tasks import dispatch

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(dispatch.app, "send_task",
                        lambda name, **kw: sent.append((name, kw)))
    monkeypatch.setattr(dispatch, "_run_with_fresh_session",
                        lambda fn: [{"video_id": "v-1", "token": "t"}])
    for f in (dispatch.dispatch_transcodes, dispatch.dispatch_scene_splits,
              dispatch.dispatch_publishes, dispatch.dispatch_deidents):
        assert f() == 1
    job = {"video_id": "v-1", "token": "t"}
    assert sent == [
        (TRANSCODE_ONE, {"args": [job], "queue": "q.transcode", "ignore_result": True,
                          "serializer": "json", "task_id": "t"}),
        (SCENE_SPLIT_ONE, {"args": [job], "queue": "q.transcode", "ignore_result": True,
                          "serializer": "json", "task_id": "t"}),
        (PUBLISH_ONE, {"args": [job], "queue": "q.transcode", "ignore_result": True,
                          "serializer": "json", "task_id": "t"}),
        (DEIDENT_ONE, {"args": [job], "queue": "q.deident", "ignore_result": True,
                          "serializer": "json", "task_id": "t"}),
    ]


def test_caption_qc_tasks_are_registered_routed_and_scheduled():
    """품질검수 투입은 default, 검사는 q.qc로 간다. 투입은 5초 주기다.

    검사는 모델 응답을 수십 초 기다리므로 default에 섞으면 내보내기와 무DB 잡의 회신이 밀린다.
    """
    from reborn_contracts.queues import CAPTION_QC_RUN, DISPATCH_CAPTION_QC, QUEUE_QC, TASK_ROUTES

    assert {CAPTION_QC_RUN, DISPATCH_CAPTION_QC} <= set(app.tasks)
    assert TASK_ROUTES[CAPTION_QC_RUN] == {"queue": QUEUE_QC}
    assert TASK_ROUTES[DISPATCH_CAPTION_QC] == {"queue": "default"}
    entry = app.conf.beat_schedule["dispatch-caption-qc-5s"]
    assert entry["task"] == DISPATCH_CAPTION_QC
    assert entry["schedule"].run_every.total_seconds() == 5.0
    run = app.tasks[CAPTION_QC_RUN]
    assert run.acks_late and run.reject_on_worker_lost and run.ignore_result


def test_dispatch_caption_qc_sends_only_leased_items_to_q_qc(monkeypatch):
    """투입은 표시(lease)를 얻은 건만 q.qc로 보낸다. 이미 보낸 건을 주기마다 다시 보내지 않는다."""
    from uuid import uuid4

    from api_gateway.services.caption_qc import backlog
    from worker_io.tasks import qc

    a, b = uuid4(), uuid4()

    async def _owed(_db):
        """투입 대상 두 건을 돌려준다."""
        return [(a, "approve"), (b, "submit")]

    sent = []
    monkeypatch.setattr(backlog, "find_owed", _owed)
    monkeypatch.setattr(backlog, "lease", lambda pairs: pairs[:1])
    monkeypatch.setattr(
        qc.caption_qc_run, "apply_async",
        lambda kwargs, queue: sent.append((kwargs, queue)),
    )
    assert qc.dispatch_caption_qc() == 1
    assert sent == [({"work_item_id": str(a), "trigger": "approve"}, "q.qc")]


def test_dispatch_caption_qc_eager():
    """eager 모드에서 실제 DB로 돌면 보낸 건수(정수)를 돌려준다."""
    from worker_io.tasks import dispatch_caption_qc

    result = dispatch_caption_qc()
    assert isinstance(result, int) and result >= 0


def test_thumbnail_budgets_fit_the_beat():
    """썸네일 두 종류는 1분 주기로 q.media에 가고, 시간 예산의 합이 주기보다 짧다.

    q.media 워커의 동시 실행 수는 2이고 규격 검사도 같은 큐를 쓴다. 두 썸네일은 각각 한 번에
    하나만 돈다(advisory lock). 한 주기에 쓰는 시간의 합이 주기보다 짧아야 규격 검사가
    한 주기 넘게 밀리지 않는다.
    주기나 예산을 바꾸려면 이 테스트와 `queues.md`의 q.media 행을 같이 바꾼다.
    """
    from api_gateway.services.media import clip_thumbnail, video_thumbnail

    s = app.conf.beat_schedule
    routes = app.conf.task_routes
    period = {}
    for key, task in (("run-pending-clip-thumbnails-1min", "run_pending_clip_thumbnails"),
                      ("run-pending-video-thumbnails-1min", "run_pending_video_thumbnails")):
        assert s[key]["task"] == f"worker_io.tasks.{task}"
        period[task] = s[key]["schedule"].run_every.total_seconds()
        assert routes[f"worker_io.tasks.{task}"]["queue"] == "q.media"
    assert period == {"run_pending_clip_thumbnails": 60.0, "run_pending_video_thumbnails": 60.0}
    clip_period = period["run_pending_clip_thumbnails"]
    video_period = period["run_pending_video_thumbnails"]
    assert clip_period > clip_thumbnail.TIME_BUDGET_S
    assert video_period > video_thumbnail.TIME_BUDGET_S
    assert clip_thumbnail.TIME_BUDGET_S + video_thumbnail.TIME_BUDGET_S < 60.0


def test_reclaim_tasks_revoke_the_old_messages(monkeypatch):
    """회수 태스크는 서비스가 넘긴 옛 토큰을 큐 메시지 취소로 보낸다. 네 단계가 같다.

    취소하지 않으면 큐에서 기다리던 옛 메시지가 나중에 실행된다. 그러면 새 토큰으로 다시 투입된
    시도와 함께 같은 영상을 두 번 처리한다. 취소가 실패해도 회수 결과는 그대로 돌려준다.
    """
    import asyncio
    import importlib

    from worker_io.tasks import dispatch

    revoked: list = []
    monkeypatch.setattr(dispatch, "_run_with_fresh_session", lambda fn: asyncio.run(fn(None)))
    monkeypatch.setattr(dispatch.app.control, "revoke", lambda ids, **kw: revoked.append(ids))

    async def _fake(db, *, revoke=None):
        """옛 토큰 하나를 회수한 것처럼 취소 함수를 부른다."""
        revoke(["old-tok"])
        return 1

    for task, module in (
        (dispatch.reclaim_expired_transcodes, "transcode"),
        (dispatch.reclaim_expired_publishes, "publish"),
        (dispatch.reclaim_expired_deidents, "deident"),
        (dispatch.reclaim_expired_scene_splits, "scene_split"),
    ):
        revoked.clear()
        mod = importlib.import_module(f"api_gateway.services.media.{module}")
        monkeypatch.setattr(mod, "reclaim_expired", _fake)
        assert task() == 1
        assert revoked == [["old-tok"]], module


def test_forked_child_drops_the_inherited_pool_without_closing(monkeypatch):
    """fork된 자식이 시작할 때 부모의 커넥션 풀을 닫지 않고 새 풀로 바꾸는지 확인한다.

    자식이 물려받은 커넥션을 닫으면 부모가 쓰는 연결에 종료 패킷이 가서 부모 쪽 연결까지 망가진다.
    그래서 옛 풀의 `dispose`(커넥션마다 close)를 부르지 않고 풀 객체만 바꿔야 한다.
    """
    from api_gateway.database import engine
    from worker_io.celery_app import _dispose_inherited_pool

    inherited = engine.sync_engine.pool
    closed: list[bool] = []
    monkeypatch.setattr(inherited, "dispose", lambda: closed.append(True))
    _dispose_inherited_pool()
    assert engine.sync_engine.pool is not inherited
    assert closed == []


def test_short_interval_beats_expire_within_their_interval():
    """30초 이하 주기 태스크의 메시지는 주기 안에 만료된다. 워커가 멈춘 동안 쌓이지 않는다."""
    short = {name: e for name, e in app.conf.beat_schedule.items()
             if getattr(e["schedule"], "run_every", None) is not None
             and e["schedule"].run_every.total_seconds() <= 30}
    assert {"maiu-reconciler-5s", "dispatch-caption-qc-5s",
            "run-pending-storage-imports-10s", "dispatch-transcodes-30s"} <= set(short)
    for name, e in short.items():
        every = e["schedule"].run_every.total_seconds()
        assert e.get("options", {}).get("expires") == every, name
    assert "options" not in app.conf.beat_schedule["reclaim-expired-transcodes-5min"], (
        "긴 주기는 그대로 둔다"
    )


def test_beat_success_reuses_one_redis_client(monkeypatch):
    """주기 태스크 성공 기록은 Redis 클라이언트 하나를 다시 쓴다. 성공마다 새로 열지 않는다."""
    import redis
    from worker_io import celery_app as mod

    made: list[object] = []

    class _Client:
        """`set` 호출만 받는 Redis 대역."""

        def set(self, key, value):
            """아무것도 하지 않는다."""

    def _from_url(url, **kw):
        """만든 횟수를 세고 대역을 돌려준다."""
        made.append(url)
        return _Client()

    monkeypatch.setattr(mod, "_beat_redis", None)
    monkeypatch.setattr(redis, "from_url", _from_url)
    task = app.tasks["worker_io.tasks.run_maiu_reconciler"]
    for _ in range(3):
        mod._mark_beat_alive(sender=task)
    assert len(made) == 1


def test_beat_success_records_the_period(monkeypatch):
    """주기 태스크 성공 기록에 그 태스크 주기를 같이 쓴다. 진단이 태스크마다 다른 기준으로 잰다."""
    from worker_io import celery_app as mod

    written: dict[str, str] = {}

    class _Client:
        """`set`만 받는 Redis 대역."""

        def set(self, key, value):
            """기록한다."""
            written[key] = value

    monkeypatch.setattr(mod, "_beat_redis", _Client())
    mod._mark_beat_alive(sender=app.tasks["worker_io.tasks.dispatch_transcodes"])
    mod._mark_beat_alive(sender=app.tasks["worker_io.tasks.purge_audit_events"])
    assert written["reborn:beat:worker_io.tasks.dispatch_transcodes"].split()[1] == "30"
    assert written["reborn:beat:worker_io.tasks.purge_audit_events"].split()[1] == "86400"


def test_routine_task_logs_are_quiet_but_errors_stay():
    """태스크 수신·성공 줄과 beat 발송 줄은 WARNING 아래로 찍지 않는다. 태스크 오류는 그대로 남는다.

    5~10초 주기 태스크가 여럿이라 이 줄만으로 분당 백 줄이 넘는다.
    그러면 로그 회전 상한 안에서 며칠치만 남는다.
    """
    import logging

    from worker_io import celery_app as mod

    names = ("celery.app.trace", "celery.worker.strategy", "celery.beat")
    root = logging.getLogger()
    saved = (root.level, {n: logging.getLogger(n).level for n in names})
    try:
        # `celery -l info`처럼 루트 로거를 INFO로 둔 상태에서 확인한다.
        root.setLevel(logging.INFO)
        for n in names:
            logging.getLogger(n).setLevel(logging.NOTSET)
        mod._quiet_polling_http_logs()
        for n in names:
            logger = logging.getLogger(n)
            assert not logger.isEnabledFor(logging.INFO), n
            assert logger.isEnabledFor(logging.ERROR), n
    finally:
        root.setLevel(saved[0])
        for n, level in saved[1].items():
            logging.getLogger(n).setLevel(level)
