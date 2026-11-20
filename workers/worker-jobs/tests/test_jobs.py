"""무DB 잡 태스크의 등록·회신 계약을 확인한다. 브로커와 DB 없이 돈다."""
from reborn_contracts.queues import JOB_TASKS, TASK_ROUTES
from worker_jobs.celery_app import app


def test_registers_exactly_the_job_tasks():
    """이 앱은 무DB 잡 네 개만 등록한다. 이름은 `reborn_contracts.queues`와 같다.

    이름이 어긋나면 worker-io가 보낸 메시지가 이 워커에 등록되지 않은 태스크가 되어 버려진다.
    DB를 쓰는 태스크가 여기 등록되면 잡 이미지에 DB 설정이 필요해진다.
    celery는 한 프로세스의 앱끼리 태스크를 공유하므로 이 패키지가 정의한 태스크만 센다.
    """
    app.loader.import_default_modules()
    names = {n for n, t in app.tasks.items() if t.__module__.startswith("worker_jobs.")}
    assert names == set(JOB_TASKS)


def test_job_tasks_keep_the_redelivery_contract():
    """잡 태스크는 늦은 확인 응답으로 받고, 실행 자식만 죽은 메시지는 다시 넣지 않는다.

    늦은 확인 응답이 빠지면 워커 컨테이너가 강제 종료될 때 메시지가 확인 응답된 채 사라진다.
    자식이 죽은 메시지를 다시 넣으면 자식을 죽이는 입력이 같은 토큰으로 곧바로 다시 돌아
    회수의 시도 상한이 동작하지 않는다. 그 복구는 리스 회수가 새 토큰으로 맡는다.
    """
    app.loader.import_default_modules()
    for name in JOB_TASKS:
        t = app.tasks[name]
        assert (t.acks_late, t.reject_on_worker_lost, t.ignore_result) == (True, False, True), name


def test_routes_come_from_the_contract_table():
    """계약 모듈의 라우팅 표를 그대로 쓴다. 잡은 정해진 큐로, 회신은 default로 간다."""
    assert app.conf.task_routes is TASK_ROUTES
    assert app.conf.task_default_queue == "default"
    for name in JOB_TASKS:
        assert app.amqp.router.route({}, name)["queue"].name == TASK_ROUTES[name]["queue"]


def test_unexpected_errors_still_send_a_completion_report(monkeypatch):
    """`JobError`가 아닌 예외도 완료 보고를 보낸다. 보고 없이 끝나지 않는다.

    원인이 불분명해도 실패로 끝났다는 사실은 보고한다.
    보고가 없으면 영상이 리스 만료(변환 5분, 씬 분할 1시간)까지 `running`에 머물다가
    회수되어 처음부터 다시 돈다. 원인은 로그에도 DB에도 남지 않는다.
    네 태스크(transcode·publish·deident·scene_split)를 모두 확인한다.

    화면 사유에 예외 타입명을 싣지 않는다. 타입명은 로그에 남긴다.
    """
    from worker_jobs import jobs as tasks

    sent: list[dict] = []
    monkeypatch.setattr(tasks.app, "send_task",
                        lambda name, **kw: sent.append({"name": name, **kw}))

    def _boom(job, on_heartbeat=None):
        """`JobError`가 아닌 예외를 던지는 러너를 흉내 낸다."""
        raise KeyError("예상 못 한 것")

    job = {"video_id": "v-1", "token": "tok"}
    for task, runner_attr, complete in (
        (tasks.deident_one, "reborn_transcode.run_deident_job",
         "worker_io.tasks.deident_complete"),
        (tasks.scene_split_one, "reborn_transcode.scene_split.run_job",
         "worker_io.tasks.scene_split_complete"),
    ):
        sent.clear()
        monkeypatch.setattr(runner_attr, _boom)
        task(job)                       # 예외가 밖으로 나오면 여기서 실패한다
        reports = [x for x in sent if x["name"] == complete]
        assert reports, f"{complete} 보고가 안 나갔다"
        kw = reports[0]["kwargs"]
        assert kw["ok"] is False
        assert kw["transient"] is False, "모르는 실패를 무한 재시도로 두면 버그가 영원히 돈다"
        assert kw["error"] == tasks.UNEXPECTED_ERROR
        assert "KeyError" not in kw["error"], "예외 타입명이 화면 사유로 나간다"


def test_success_reports_carry_the_output_facts(monkeypatch):
    """성공 보고에 산출 측정값(`outputs`)이 실린다. 판정은 받는 쪽이 한다.

    변환(분석본)과 재생본 변환이 같은 러너를 쓰므로 둘 다 확인한다. 워커는 러너가 모은
    측정값을 그대로 전달한다. 회신에 판정(`cause`)이 없는 것도 함께 확인한다.
    """
    from worker_jobs import jobs as tasks

    sent: list[dict] = []
    monkeypatch.setattr(tasks.app, "send_task",
                        lambda name, **kw: sent.append({"name": name, **kw}))
    facts = [{"label": "analysis-720p", "duration_seconds": 600.0, "fps": 29.97,
              "video_codec": "h264", "width": 1280, "height": 720, "size_bytes": 4096}]
    monkeypatch.setattr("reborn_transcode.run_job",
                        lambda job, on_heartbeat=None: {"elapsed_s": 1.5, "outputs": facts})

    job = {"video_id": "v-1", "token": "tok", "targets": []}
    for task, complete in ((tasks.transcode_one, "worker_io.tasks.transcode_complete"),
                           (tasks.publish_one, "worker_io.tasks.publish_complete")):
        sent.clear()
        task(job)
        kw = next(x for x in sent if x["name"] == complete)["kwargs"]
        assert kw["ok"] is True and kw["elapsed_s"] == 1.5
        assert kw["outputs"] == facts
        assert "cause" not in kw["outputs"][0], "워커는 판정하지 않는다"


def test_jobs_announce_their_start_before_working(monkeypatch):
    """잡은 일을 시작하기 전에 하트비트를 한 번 보낸다.

    투입 때 건 리스는 큐 대기를 덮을 만큼 길다. 시작 하트비트가 리스를 평소 길이로 줄여야
    시작한 뒤 워커가 비정상 종료됐을 때 몇 분 안에 회수된다.
    진행 초를 계산할 수 없는 비식별화는 None을 보낸다.
    """
    from worker_jobs import jobs as tasks

    sent: list[dict] = []
    monkeypatch.setattr(tasks.app, "send_task",
                        lambda name, **kw: sent.append({"name": name, **kw}))

    def _runner(job, on_heartbeat=None):
        """러너가 불린 시점까지 나간 메시지를 표시한다."""
        sent.append({"name": "runner"})
        return {"elapsed_s": 0.1, "outputs": []}

    monkeypatch.setattr("reborn_transcode.run_job", _runner)
    monkeypatch.setattr("reborn_transcode.run_deident_job", _runner)
    job = {"video_id": "v-1", "token": "tok", "targets": []}
    for task, beat, progress in (
        (tasks.transcode_one, "worker_io.tasks.transcode_heartbeat", 0.0),
        (tasks.publish_one, "worker_io.tasks.publish_heartbeat", 0.0),
        (tasks.deident_one, "worker_io.tasks.deident_heartbeat", None),
    ):
        sent.clear()
        task(job)
        names = [x["name"] for x in sent]
        assert names.index(beat) < names.index("runner"), names
        assert sent[names.index(beat)]["args"] == ["v-1", "tok", progress]


def test_scene_split_announces_its_start_and_beats_while_detecting(monkeypatch):
    """씬 분할도 시작 전에 하트비트를 보내고, 검출 중 러너가 부르는 하트비트를 같은 이름으로 보낸다.

    투입 때 건 리스는 큐 대기를 덮을 만큼 길다. 시작 하트비트가 없으면 리스가 처리 시간이 아니라
    큐 대기까지 재고, 처리 중 하트비트가 없으면 긴 검출이 리스 만료로 회수된다.
    """
    from worker_jobs import jobs as tasks

    sent: list[dict] = []
    monkeypatch.setattr(tasks.app, "send_task",
                        lambda name, **kw: sent.append({"name": name, **kw}))

    def _runner(job, on_heartbeat=None):
        """러너가 불린 시점을 표시하고 검출 중 하트비트를 한 번 부른다."""
        sent.append({"name": "runner"})
        on_heartbeat()
        return {"segments": [], "raw_count": 0, "rule": "r"}

    monkeypatch.setattr("reborn_transcode.scene_split.run_job", _runner)
    tasks.scene_split_one({"video_id": "v-1", "token": "tok"})
    names = [x["name"] for x in sent]
    beat = "worker_io.tasks.scene_split_heartbeat"
    assert names.count(beat) == 2 and names.index(beat) < names.index("runner"), names
    assert sent[names.index(beat)]["args"] == ["v-1", "tok"]


def test_ready_worker_records_its_role(monkeypatch):
    """워커가 기동을 마치면 노드 역할(`gpu@…`의 gpu) 이름으로 동작 기록을 남긴다.

    이 워커는 주기 태스크를 돌리지 않아 진단이 멈춘 것을 알 근거가 이 기록뿐이다.
    """
    from types import SimpleNamespace

    import redis
    from worker_jobs import celery_app as mod

    written: dict[str, str] = {}

    class _Client:
        """`set`만 받는 Redis 대역."""

        def set(self, key, value):
            """기록한다."""
            written[key] = value

    monkeypatch.setattr(redis, "from_url", lambda url, **kw: _Client())
    monkeypatch.setattr(mod, "_node", {"role": None, "at": 0.0, "client": None})
    mod._node_ready(sender=SimpleNamespace(hostname="gpu@host-1"))
    assert set(written) == {"reborn:node:gpu"}
    written.clear()
    mod._node_alive()
    assert not written, "30초 안에 다시 쓰지 않는다"


def test_routine_task_logs_are_quiet_but_errors_stay():
    """태스크 수신·성공 줄은 WARNING 아래로 찍지 않는다. 태스크 오류는 그대로 남는다."""
    import logging

    from worker_jobs import celery_app as mod

    names = ("celery.app.trace", "celery.worker.strategy")
    root = logging.getLogger()
    saved = (root.level, {n: logging.getLogger(n).level for n in names})
    try:
        # `celery -l info`처럼 루트 로거를 INFO로 둔 상태에서 확인한다.
        root.setLevel(logging.INFO)
        for n in names:
            logging.getLogger(n).setLevel(logging.NOTSET)
        mod._quiet_http_logs()
        for n in names:
            logger = logging.getLogger(n)
            assert not logger.isEnabledFor(logging.INFO), n
            assert logger.isEnabledFor(logging.ERROR), n
    finally:
        root.setLevel(saved[0])
        for n, level in saved[1].items():
            logging.getLogger(n).setLevel(level)
