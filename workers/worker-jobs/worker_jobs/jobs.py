"""DB 없이 도는 잡 태스크. 페이로드 하나로 일하고 결과를 큐 메시지로 보낸다.

이 모듈은 api_gateway와 worker_io를 import하지 않는다(루트 `.importlinter`가 막는다).
DB 접점은 worker-io의 `tasks.dispatch`(투입·회수)와 `tasks.callbacks`(하트비트·완료)가 맡는다.
태스크 이름과 회신 이름은 `reborn_contracts.queues`에 있다.
"""
import logging
import os

from reborn_contracts.queues import (
    DEIDENT_COMPLETE,
    DEIDENT_HEARTBEAT,
    DEIDENT_ONE,
    PUBLISH_COMPLETE,
    PUBLISH_HEARTBEAT,
    PUBLISH_ONE,
    QUEUE_DEFAULT,
    SCENE_SPLIT_COMPLETE,
    SCENE_SPLIT_HEARTBEAT,
    SCENE_SPLIT_ONE,
    TRANSCODE_COMPLETE,
    TRANSCODE_HEARTBEAT,
    TRANSCODE_ONE,
)

from .celery_app import app

#: 무DB 워커의 로그. 완료 보고가 나가지 못한 경우처럼 DB에 남지 않는 사실을 기록한다.
log = logging.getLogger(__name__)

#: 예상하지 못한 예외일 때 화면에 보여 줄 사유.
#: 파이썬 예외 타입명은 운영자에게 의미가 없다. 원인 추적용 traceback은
#: 바로 뒤의 `log.exception`이 남긴다.
#: 타입명을 한글 문장에 붙이면 사유 변환(`error_text.operator_text`)이 앱 문장으로 판정해
#: 그대로 통과시킨다. 그러면 준비 타임라인에 타입명이 뜬다.
UNEXPECTED_ERROR = "예상하지 못한 오류로 중단됐습니다"


@app.task(
    name=TRANSCODE_ONE,
    # 늦은 확인 응답: 워커 컨테이너가 강제 종료되면 메시지가 브로커에 남아 다시 전달된다.
    # 실행 프로세스(자식)만 죽은 경우에는 다시 넣지 않는다(reject_on_worker_lost=False).
    # 다시 넣으면 메모리 초과처럼 자식을 죽이는 입력이 같은 토큰으로 곧바로 다시 돌고,
    # 하트비트가 리스를 이어 가 회수와 시도 상한이 끝내 동작하지 않는다.
    # 확인 응답한 뒤에는 리스 만료를 회수가 받아 새 토큰으로 다시 투입하고 시도를 센다.
    # 다시 실행해도 리스와 펜싱이 중복 반영을 막는다.
    acks_late=True,
    reject_on_worker_lost=False,
    ignore_result=True,
)
def transcode_one(job: dict) -> None:
    """영상 1건을 DB 없이 트랜스코딩한다.

    이 태스크는 api_gateway를 import하지 않는다. 페이로드 하나로 일하고 하트비트와 완료를
    큐 메시지로 보낸다. 그래서 트랜스코더를 별도 이미지·언어로 분리할 수 있고,
    다른 GPU 잡도 이 구조를 그대로 쓸 수 있다.
    """
    from reborn_transcode import JobError, run_job

    vid, token = job["video_id"], job["token"]
    log.info("변환 시작 video=%s", vid)

    # 스레드 수만은 워커가 덮어쓸 수 있다. 나머지 사양(프리셋·crf·해상도)은
    # 생산자가 정한다. 스레드 수는 이 워커의 코어 수에 맞춰야 한다.
    # 생산자에게는 소비자의 CPU 할당 정보가 없다. 지금은 두 쪽이 같은 .env를 읽어 값이 맞는다.
    # 코어 수가 다른 워커를 붙이면 생산자가 정한 값이 맞지 않는다.
    override = os.getenv("TRANSCODE_THREADS")
    if override:
        for t in job.get("targets", []):
            t["threads"] = int(override)

    def _beat(progress_sec: float) -> None:
        """트랜스코딩 하트비트를 보낸다. 진행 초도 함께 보낸다."""
        # 하트비트는 best-effort다. 전송 예외가 밖으로 나가면 잡 전체가 실패한다
        # (아래 `except Exception`이 예상하지 못한 오류로 보고해 시도 횟수를 쓴다).
        # 그래서 브로커 오류 한 번으로 잡이 실패하지 않게 예외를 삼키고 경고만 남긴다.
        # 보내지 못한 하트비트는 다음 주기가 보낸다(리스는 주기보다 길다).
        try:
            app.send_task(
                TRANSCODE_HEARTBEAT,
                args=[vid, token, progress_sec], queue=QUEUE_DEFAULT,
            )
        except Exception as exc:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
            log.warning("하트비트 전송 실패(무시) video=%s: %s", vid, exc)

    # 시작을 먼저 알린다. 투입 때 건 리스는 큐 대기를 덮을 만큼 길다.
    # 이 하트비트가 리스를 평소 길이로 줄인다.
    # 그래야 시작한 뒤 워커가 비정상 종료되면 몇 분 안에 회수된다.
    _beat(0.0)
    try:
        result = run_job(job, on_heartbeat=_beat)
    except JobError as e:
        app.send_task(
            TRANSCODE_COMPLETE,
            kwargs={"video_id": vid, "token": token, "ok": False,
                    "error": str(e), "transient": e.transient},
            queue=QUEUE_DEFAULT,
        )
        return
    except Exception:  # noqa: BLE001 — **보고 없이 끝나면 안 된다**
        # JobError가 아닌 예외(예상하지 못한 버그·환경 오류)도 실패로 완료 보고를 보낸다.
        # 보고가 없으면 영상은 리스(5분)가 만료될 때까지 `running`에 머물다가
        # 회수되어 처음부터 다시 돈다.
        # 일시 실패로 분류하지 않는다. 원인이 불분명한 실패를 계속 재시도하면 같은 버그가 반복된다.
        # 받는 쪽이 시도 횟수를 세어 상한에서 멈춘다.
        app.send_task(TRANSCODE_COMPLETE,
                      kwargs={"video_id": vid, "token": token, "ok": False,
                              "error": UNEXPECTED_ERROR,
                              "transient": False}, queue=QUEUE_DEFAULT)
        log.exception("transcode_one 예상 못 한 오류 video=%s", vid)
        return
    app.send_task(
        TRANSCODE_COMPLETE,
        kwargs={"video_id": vid, "token": token, "ok": True,
                "elapsed_s": result["elapsed_s"],
                # 산출물 측정값. 워커는 측정값만 싣는다.
                # 원본과 비교하는 판정은 받는 쪽이 한다.
                "outputs": result.get("outputs")},
        queue=QUEUE_DEFAULT,
    )


@app.task(
    name=SCENE_SPLIT_ONE,
    # 다시 전달해도 리스와 펜싱이 중복 반영을 막는다. 이전 토큰의 보고는 complete가 버린다.
    # 자식만 죽은 경우 다시 넣지 않는 이유는 `transcode_one`과 같다.
    acks_late=True, reject_on_worker_lost=False, ignore_result=True,
)
def scene_split_one(job: dict) -> None:
    """영상 1건을 DB 없이 씬 분할한다.

    이 태스크는 api_gateway를 import하지 않는다. 페이로드 하나로 검출·병합하고 결과를
    큐 메시지로 보낸다. 판정(클립 생성·재시도·실패 확정)은 받는 쪽 한 곳이 한다.

    시작할 때와 검출이 도는 동안 하트비트를 보낸다. 투입 때 건 리스는 큐 대기를 덮을 만큼 길고,
    시작 하트비트가 그 리스를 처리 시간 기준으로 줄인다(`transcode_one`과 같은 이유).
    """
    from reborn_transcode.scene_split import SceneSplitError, run_job

    vid, token = job["video_id"], job["token"]

    def _beat() -> None:
        """씬 분할 하트비트를 보낸다. 전송 실패는 경고만 남긴다(`transcode_one`과 같은 이유)."""
        try:
            app.send_task(SCENE_SPLIT_HEARTBEAT, args=[vid, token], queue=QUEUE_DEFAULT)
        except Exception as exc:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
            log.warning("하트비트 전송 실패(무시) video=%s: %s", vid, exc)

    _beat()
    try:
        result = run_job(job, on_heartbeat=_beat)
    except SceneSplitError as e:
        app.send_task(
            SCENE_SPLIT_COMPLETE,
            kwargs={"video_id": vid, "token": token, "ok": False,
                    "error": str(e), "transient": e.transient},
            queue=QUEUE_DEFAULT,
        )
        return
    except Exception:  # noqa: BLE001 — 보고 없이 끝나면 안 된다
        # SceneSplitError가 아닌 예외도 실패로 완료 보고를 보낸다. `run_job`이
        # `SceneSplitError`로 감싸는 것은 길이 없음·시간 초과·ffmpeg 실패뿐이다.
        # 페이로드 키 누락·OSError·디코더 예외 같은 나머지 예외는 그대로 빠져나온다.
        # 보고가 없으면 리스가 끝날 때까지 멈췄다가 회수되어 처음부터 다시 돈다.
        # 일시 실패로 분류하면 항상 실패하는 입력이 끝없이 재시도된다.
        # 받는 쪽(`scene_split.complete`)이 시도 횟수를 세어 상한에서 멈춘다.
        app.send_task(
            SCENE_SPLIT_COMPLETE,
            kwargs={"video_id": vid, "token": token, "ok": False,
                    "error": UNEXPECTED_ERROR, "transient": False},
            queue=QUEUE_DEFAULT,
        )
        log.exception("scene_split_one 예상 못 한 오류 video=%s", vid)
        return
    app.send_task(
        SCENE_SPLIT_COMPLETE,
        kwargs={"video_id": vid, "token": token, "ok": True,
                "segments": result["segments"],
                # 묶기 전 개수도 함께 보낸다. 묶은 개수만 보내면 받는 쪽은 그 수가
                # 많은지 적은지 판단할 수 없다.
                "basis": {"raw_count": result["raw_count"],
                          "merged_count": len(result["segments"]),
                          "rule": result["rule"]}},
        queue=QUEUE_DEFAULT,
    )


@app.task(
    name=PUBLISH_ONE,
    acks_late=True, reject_on_worker_lost=False, ignore_result=True,
)
def publish_one(job: dict) -> None:
    """재생본을 DB 없이 인코딩한다. transcode_one과 같은 러너를 쓴다.

    잡 형식이 같으므로 러너도 같다. 다른 점은 회신을 보내는 태스크뿐이다.
    """
    from reborn_transcode import JobError, run_job

    vid, token = job["video_id"], job["token"]
    log.info("게시 시작 video=%s", vid)
    override = os.getenv("TRANSCODE_THREADS")
    if override:
        for t in job.get("targets", []):
            t["threads"] = int(override)

    def _beat(progress_sec: float) -> None:
        """publish 하트비트를 보낸다. 진행 초도 함께 보낸다."""
        # 하트비트는 best-effort다. 전송 예외가 밖으로 나가면 잡 전체가 실패한다
        # (아래 `except Exception`이 예상하지 못한 오류로 보고해 시도 횟수를 쓴다).
        # 그래서 브로커 오류 한 번으로 잡이 실패하지 않게 예외를 삼키고 경고만 남긴다.
        # 보내지 못한 하트비트는 다음 주기가 보낸다(리스는 주기보다 길다).
        try:
            app.send_task(PUBLISH_HEARTBEAT,
                          args=[vid, token, progress_sec], queue=QUEUE_DEFAULT)
        except Exception as exc:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
            log.warning("하트비트 전송 실패(무시) video=%s: %s", vid, exc)

    # 시작을 먼저 알린다. 이유는 `transcode_one`과 같다.
    _beat(0.0)
    try:
        result = run_job(job, on_heartbeat=_beat)
    except JobError as e:
        app.send_task(PUBLISH_COMPLETE,
                      kwargs={"video_id": vid, "token": token, "ok": False,
                              "error": str(e), "transient": e.transient}, queue=QUEUE_DEFAULT)
        return
    except Exception:  # noqa: BLE001 — **보고 없이 끝나면 안 된다**
        # JobError가 아닌 예외(예상하지 못한 버그·환경 오류)도 실패로 완료 보고를 보낸다.
        # 보고가 없으면 영상은 리스(5분)가 만료될 때까지 `running`에 머물다가
        # 회수되어 처음부터 다시 돈다.
        # 일시 실패로 분류하지 않는다. 원인이 불분명한 실패를 계속 재시도하면 같은 버그가 반복된다.
        # 받는 쪽이 시도 횟수를 세어 상한에서 멈춘다.
        app.send_task(PUBLISH_COMPLETE,
                      kwargs={"video_id": vid, "token": token, "ok": False,
                              "error": UNEXPECTED_ERROR,
                              "transient": False}, queue=QUEUE_DEFAULT)
        log.exception("publish_one 예상 못 한 오류 video=%s", vid)
        return
    app.send_task(PUBLISH_COMPLETE,
                  kwargs={"video_id": vid, "token": token, "ok": True,
                          "elapsed_s": result["elapsed_s"],
                          # 재생본 측정값. transcode_one과 같은 형식이다.
                          "outputs": result.get("outputs")}, queue=QUEUE_DEFAULT)


@app.task(
    name=DEIDENT_ONE,
    acks_late=True, reject_on_worker_lost=False, ignore_result=True,
)
def deident_one(job: dict) -> None:
    """비식별화를 DB 없이 실행한다.

    모델은 이 프로세스 안에서 돈다(`reborn_transcode.deident`). `DEIDENT_MODEL`이 비어 있으면
    내장 비식별화 모듈(`essential-yunet`)을 쓴다.
    """
    from reborn_transcode import JobError, run_deident_job

    vid, token = job["video_id"], job["token"]
    log.info("비식별화 시작 video=%s", vid)

    def _beat(progress_sec: float | None) -> None:
        """비식별화 하트비트를 보낸다. 진행 초가 None이면 리스만 연장한다."""
        # 계산할 수 없는 진행 초를 0으로 보내면 화면이 0%에서 멈춘 것으로 잘못 표시한다.
        # 하트비트는 best-effort다. 전송 예외가 밖으로 나가면 잡 전체가 실패한다
        # (아래 `except Exception`이 예상하지 못한 오류로 보고해 시도 횟수를 쓴다).
        # 그래서 브로커 오류 한 번으로 잡이 실패하지 않게 예외를 삼키고 경고만 남긴다.
        # 보내지 못한 하트비트는 다음 주기가 보낸다(리스는 주기보다 길다).
        try:
            app.send_task(DEIDENT_HEARTBEAT,
                          args=[vid, token, progress_sec], queue=QUEUE_DEFAULT)
        except Exception as exc:  # noqa: BLE001 — 관측 실패가 작업을 막지 않는다
            log.warning("하트비트 전송 실패(무시) video=%s: %s", vid, exc)

    # 시작을 먼저 알린다. 이유는 `transcode_one`과 같다. 진행 초는 아직 없다.
    _beat(None)
    try:
        result = run_deident_job(job, on_heartbeat=_beat)
    except JobError as e:
        app.send_task(DEIDENT_COMPLETE,
                      kwargs={"video_id": vid, "token": token, "ok": False,
                              "error": str(e), "transient": e.transient}, queue=QUEUE_DEFAULT)
        return
    except Exception:  # noqa: BLE001 — **보고 없이 끝나면 안 된다**
        # JobError가 아닌 예외(예상하지 못한 버그·환경 오류)도 실패로 완료 보고를 보낸다.
        # 보고가 없으면 영상은 리스(5분)가 만료될 때까지 `running`에 머물다가
        # 회수되어 처음부터 다시 돈다.
        # 일시 실패로 분류하지 않는다. 원인이 불분명한 실패를 계속 재시도하면 같은 버그가 반복된다.
        # 받는 쪽이 시도 횟수를 세어 상한에서 멈춘다.
        app.send_task(DEIDENT_COMPLETE,
                      kwargs={"video_id": vid, "token": token, "ok": False,
                              "error": UNEXPECTED_ERROR,
                              "transient": False}, queue=QUEUE_DEFAULT)
        log.exception("deident_one 예상 못 한 오류 video=%s", vid)
        return
    app.send_task(DEIDENT_COMPLETE,
                  kwargs={"video_id": vid, "token": token, "ok": True,
                          "elapsed_s": result["elapsed_s"],
                          "report": result.get("report")}, queue=QUEUE_DEFAULT)
    log.info("비식별화 완료 video=%s elapsed=%.1fs", vid, result["elapsed_s"])
