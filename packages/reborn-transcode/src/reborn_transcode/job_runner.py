"""DB 없이 도는 트랜스코딩 잡 러너. 큐 프로토콜만 쓰는 워커의 본체.

이 모듈은 api_gateway에 의존하지 않는다. 입력은 잡 페이로드(dict) 하나이고,
외부와의 접점은 세 가지뿐이다. 입력 presigned GET(ffmpeg 스트리밍),
출력 presigned PUT(httpx), 호출자가 넘긴 `on_heartbeat` 콜백이다.
DB·스토리지 자격증명·설정이 없으므로 이 러너를 쓰는 워커는 언어·이미지·클러스터를 바꿔도 된다.

잡 페이로드 계약(생산자는 api 쪽 `build_jobs`, 스키마는 reborn-contracts/queues.md):

    {
      "video_id": "uuid",
      "token": "펜싱 토큰(완료 보고에 그대로 되돌린다)",
      "input_url": "presigned GET — ffmpeg가 스트리밍으로 읽는다",
      "targets": [{"label", "height"(null=스케일 없음), "crf", "preset"?, "threads"?,
                   "put_url": "presigned PUT", "content_type"}],
      "heartbeat_every_s": 60
    }

실패 분류는 워커가 1차로 한다(transient 플래그). 최종 판정은 DB 쪽(complete)이 내린다.
네트워크 오류·5xx·429·만료된 presign(403)은 일시 실패다(재큐잉되면 새 URL을 받는다).
ffmpeg 실패는 영구 실패다.

성공 회신에는 산출물 측정값(`outputs`)을 싣는다. 올리기 전에 로컬 파일을 한 번
probe한 길이·프레임레이트·코덱·해상도·크기다. 원본과 비교하는 판정은 받는 쪽 한 곳이
`output_check`로 한다. 잡 페이로드에 원본 길이와 기준값이 없으므로 워커는 판정하지 않는다.
"""
from __future__ import annotations

import contextlib
import logging
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx

from reborn_transcode.probe import ProbeError, ProbeTimeoutError, probe
from reborn_transcode.transcode import TargetSpec, TranscodeError, transcode_streaming

log = logging.getLogger(__name__)

# 인코딩 진행이 멈춘 채 이 시간(초)이 지나면 인코딩을 중단한다.
# 멈춘 ffmpeg가 하트비트만 계속 보내면 리스가 유지되어 회수가 일어나지 않는다.
_STALL_S = 300
_HTTP_TRANSIENT = {403, 408, 425, 429, 500, 502, 503, 504}  # 403은 presign 만료일 수 있다


class JobError(RuntimeError):
    """잡 실패. transient면 재큐잉 대상이고 시도 횟수를 소모하지 않는다.

    transient가 아니면 시도 상한까지 세는 실패다.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        """메시지와 일시 실패 여부를 저장한다."""
        super().__init__(message)
        self.transient = transient


def _put(url: str, path: Path, content_type: str) -> None:
    """산출물을 presigned PUT으로 올린다. 자격증명이 필요 없다.

    Raises:
        JobError: 전송 오류이거나 4xx·5xx 응답인 경우. 상태 코드로 일시·영구를 분류한다.
    """
    try:
        with path.open("rb") as f:
            r = httpx.put(
                url, content=f, headers={"Content-Type": content_type},
                timeout=httpx.Timeout(30.0, write=600.0),
            )
    except httpx.HTTPError as e:
        # 예외 타입명은 화면에 보여 줄 사유가 아니므로 로그에만 남긴다.
        log.warning("업로드 전송 오류: %r", e)
        raise JobError("결과 파일을 올리는 중 연결이 끊겼습니다", transient=True) from e
    if r.status_code >= 400:
        # 화면에 뜨는 사유라 결과를 문장으로 쓴다. 코드는 문의용으로 괄호에 남긴다.
        raise JobError(
            f"결과 파일을 올리지 못했습니다 (HTTP {r.status_code})",
            transient=r.status_code in _HTTP_TRANSIENT,
        )


@contextlib.contextmanager
def heartbeating(
    on_heartbeat: Callable | None, every: float, progress: Callable[[], float | None],
) -> Iterator[None]:
    """블록이 도는 동안 별도 스레드에서 `every`초마다 `on_heartbeat(progress())`를 부른다.

    리스는 하트비트로만 연장된다. 인코딩 뒤의 산출 검사와 업로드는 길면 리스(5분)를 넘긴다.
    수 GB 업로드나 제한 시간을 두 배로 늘린 재검사가 그렇다. 그동안 하트비트가 없으면
    회수가 시도를 되돌려 같은 영상이 새 토큰으로 다시 돈다.
    `on_heartbeat`가 None이면 아무것도 하지 않는다.
    """
    if on_heartbeat is None:
        yield
        return
    stop = threading.Event()

    def _loop() -> None:
        """멈춤 신호가 올 때까지 주기마다 하트비트를 보낸다."""
        while not stop.wait(every):
            on_heartbeat(progress())

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()
        th.join(timeout=every)


#: 산출물 검사(`_inspect`) 제한 시간(초). 로컬 파일이라 컨테이너 헤더만 읽고 곧 끝난다.
#: 멈춘 ffprobe가 완료 보고를 막지 않도록 상한만 둔다.
_INSPECT_TIMEOUT_S = 60
#: 제한 시간을 넘겼을 때 다시 읽는 횟수. 부하가 높은 서버에서는 헤더 읽기도 늦어진다.
#: 다시 읽어 끝나면 수십 분 걸린 인코딩을 버리지 않는다. 두 번째는 제한 시간을 두 배로 준다.
_INSPECT_RETRIES = 1

#: 산출물에서 측정해 싣는 필드. 받는 쪽이 저장하고 원본과 비교하는 형식이다(queues.md).
#: 파일 크기도 싣는다. 저장해 두면 조회할 때마다 HEAD 요청으로 잴 필요가 없다.
_FACTS = ("duration_seconds", "fps", "video_codec", "width", "height", "size_bytes")


def _inspect(label: str, path: Path) -> dict:
    """산출물 하나를 한 번 읽어 측정값만 기록한다. 판정은 받는 쪽이 한다.

    여기서 읽는 이유: ffmpeg는 입력이 도중에 끊겨도 rc=0으로 짧은 사본을 만든다
    (예: 앞 절반만 남은 60.0초 원본은 경고만 찍고 rc=0으로 30.0초 사본이 된다).
    종료 코드만 보면 그 사본이 성공으로 올라간다. 올리기 전의 로컬 파일이라 읽는 비용이 작다.

    읽지 못해도 예외를 내지 않고 `error`를 싣는다. 읽지 못한 산출을 실패로 볼지는
    판정하는 쪽(`output_check.check_output`)이 정한다. 여기서 `JobError`로 바꾸면
    원본 길이와 기준값이 없는 워커가 판정하게 된다.

    제한 시간을 넘기면 제한 시간을 늘려 `_INSPECT_RETRIES`번 더 읽는다. 그래도 넘기면
    `timed_out`을 함께 싣는다. 그러면 받는 쪽이 파일이 깨진 경우와 서버가 바빠 늦은 경우를
    구분할 수 있다.

    Returns:
        dict: `{"label", duration_seconds, fps, video_codec, width, height, size_bytes}`.
            읽지 못했으면 `{"label", "error"}`, 시간을 넘겼으면 `"timed_out": True`를 더한다.
    """
    timeout, retries = _INSPECT_TIMEOUT_S, _INSPECT_RETRIES
    while True:
        try:
            meta = probe(str(path), timeout=timeout)
        except ProbeTimeoutError as e:
            log.warning("산출물 검사 시간 초과 label=%s: %s", label, e)
            if retries:
                retries, timeout = retries - 1, timeout * 2
                continue
            return {"label": label, "error": str(e)[:300], "timed_out": True}
        except ProbeError as e:
            log.warning("산출물 검사 실패 label=%s: %s", label, e)
            return {"label": label, "error": str(e)[:300]}
        return {"label": label, **{k: meta[k] for k in _FACTS}}


def run_job(job: dict, *, on_heartbeat=None) -> dict:
    """잡 1건을 실행한다. 타깃별로 인코딩한 뒤 PUT으로 올리고 결과 dict를 돌려준다.

    인코딩은 별도 스레드에서 돌린다. 본 스레드는 주기(heartbeat_every_s)마다 두 가지를 한다.
    ① `on_heartbeat(진행초)`를 호출한다. 호출자(워커 셸)가 이 호출을 리스 연장 메시지로 바꾼다.
    ② 진행이 멈췄는지 확인한다. `_STALL_S` 동안 out_time이 늘지 않으면 프로세스를 중단한다.

    진행 초는 타깃을 넘어 누적된다. 분모는 받는 쪽이 타깃 수만큼 곱한다
    (둘을 만들면 인코딩할 미디어 시간도 두 배다).
    타깃마다 0부터 다시 세면 산출이 둘인 잡의 진행 막대가 100%까지 찼다가 0으로 돌아간다.

    산출 검사와 업로드 동안에도 같은 주기로 하트비트를 보낸다(`heartbeating`).

    타깃마다 인코딩이 끝나면 올리기 전에 산출물을 한 번 읽는다.
    로컬 파일을 probe해 길이·프레임레이트·코덱·해상도·크기를 `outputs`에 싣는다(`_inspect`).
    원본과 비교하는 판정은 받는 쪽이 한다(`output_check`). 러너는 측정값만 모은다.

    Returns:
        dict: `{"elapsed_s": float, "outputs": [산출 측정값…]}`. 타깃 순서대로 하나씩 담는다.
            산출 키는 생산자가 이미 알고 있으므로 돌려주지 않는다.

    Raises:
        JobError: 인코딩이 실패한 경우(영구), 업로드가 실패한 경우(일시·영구로 분류),
            진행이 멈춘 경우(일시, 환경 문제일 수 있다),
            인코딩 스레드에서 그 밖의 예외가 난 경우(일시).
    """
    started = time.monotonic()
    beat_every = float(job.get("heartbeat_every_s") or 60)
    done_before = 0.0   # 앞선 타깃들이 인코딩한 미디어 시간의 합
    with tempfile.TemporaryDirectory(prefix="rb-job-") as tmp:
        outs: list[tuple[dict, Path]] = []
        facts: list[dict] = []   # 산출별 측정값. 받는 쪽이 원본과 비교한다
        for t in job["targets"]:
            # 프리셋·스레드는 생산자가 정한다. 워커는 페이로드에 적힌 대로 인코딩한다.
            # 프리셋·스레드 키가 없는 이전 형식의 잡은 TargetSpec 기본값으로 인코딩한다.
            spec = TargetSpec(t["label"], t.get("height"), t["crf"],
                              preset=t.get("preset", "fast"), threads=int(t.get("threads") or 0))
            out = Path(tmp) / f"{spec.label}.mp4"

            progress = {"sec": -1.0}
            cancel = threading.Event()
            error: list[BaseException] = []

            def _encode(out=out, spec=spec, progress=progress, cancel=cancel, sink=error):
                """타깃 하나를 인코딩한다. 예외는 `sink`에 담아 본 스레드로 넘긴다."""
                try:
                    transcode_streaming(
                        job["input_url"], out, spec,
                        on_progress=lambda sec: progress.__setitem__("sec", sec),
                        cancel=cancel,
                    )
                except BaseException as exc:  # noqa: BLE001 — 스레드 경계, 본 스레드로 전달
                    sink.append(exc)

            th = threading.Thread(target=_encode, daemon=True)
            th.start()
            last, stalled_since = -2.0, time.monotonic()
            while th.is_alive():
                th.join(timeout=beat_every)
                if not th.is_alive():
                    break
                if on_heartbeat is not None:
                    on_heartbeat(done_before + max(progress["sec"], 0.0))
                if progress["sec"] != last:
                    last, stalled_since = progress["sec"], time.monotonic()
                elif time.monotonic() - stalled_since > _STALL_S:
                    cancel.set()  # 진행 정체. 환경 문제일 수 있어 일시 실패로 분류한다
            th.join()
            if error:
                e = error[0]
                if isinstance(e, TranscodeError) and "취소됨" in str(e):
                    raise JobError(
                        f"{spec.label} 변환이 진행되지 않아 중단했습니다", transient=True) from e
                if isinstance(e, TranscodeError):
                    raise JobError(str(e), transient=False) from e
                # 예외 타입명은 로그에만 남긴다. 화면 사유에는 고정 문장을 쓴다.
                log.warning("변환 중 예외: %r", e)
                raise JobError("변환 중 예상하지 못한 오류가 발생했습니다", transient=True) from e
            outs.append((t, out))
            # 이 타깃이 끝난 지점을 누적 기준으로 삼는다. 다음 타깃의 0초는 여기서 이어진다.
            done_before += max(progress["sec"], 0.0)
            # 올리기 전에 한 번 읽는다. rc=0이어도 짧은 사본일 수 있다(`_inspect`).
            # 검사는 재시도까지 3분 넘게 걸릴 수 있어 그동안에도 하트비트를 보낸다.
            with heartbeating(on_heartbeat, beat_every, lambda done=done_before: done):
                facts.append(_inspect(spec.label, out))

        # 모든 타깃의 인코딩이 성공한 뒤에만 업로드한다. 일부 산출만 남기지 않는다.
        # 읽지 못한 산출도 올린다. 버릴지는 판정하는 쪽이 정한다.
        # 버린 산출의 정리도 판정하는 쪽이 한다.
        # 업로드는 파일 크기와 대역폭에 따라 수십 분 걸리므로 그동안에도 하트비트를 보낸다.
        with heartbeating(on_heartbeat, beat_every, lambda: done_before):
            for t, out in outs:
                _put(t["put_url"], out, t.get("content_type", "video/mp4"))

    return {"elapsed_s": round(time.monotonic() - started, 1), "outputs": facts}
