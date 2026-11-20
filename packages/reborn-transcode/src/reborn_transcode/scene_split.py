"""씬 분할. DB 없이 도는 워커의 본체.

`job_runner`와 계약이 같다. 잡 페이로드(dict) 하나로 일하고, 외부 접점은
입력 presigned GET 하나뿐이다. DB·스토리지 자격증명·설정에 의존하지 않는다.
그래서 이 러너를 쓰는 워커는 이미지·언어를 바꿔도 된다.

검출은 원본 바이트를 읽으므로 워커에서 돈다. 그래서 api_gateway가 아니라 이 패키지에 둔다.
어댑터(`analysis/scene_detect.py`)에 두면 워커가 api_gateway에 의존하게 되어
트랜스코더를 분리할 수 없다. 어댑터는 이 모듈을 호출해 결과를 계약 타입으로 옮기기만 한다.
구현은 이 모듈 하나에만 있다.

결과는 샷을 길이 상한까지 묶은 고정 길이 구간이다. 끊는 위치는 발화 사이의 무음 구간에서 고른다.
보도영상은 컷이 2~5초마다 바뀌어 39.5분 영상이 샷 수백 개로 나뉜다.
샷 하나는 라벨링 단위로 쓰기에 짧다.

    39.5분 영상의 샷 404개를 78개 구간으로 묶는다(샷 경계 · 최대 40초 · 무음 구간 우선).

무음 구간은 끊을지가 아니라 어디서 끊을지를 정한다. 이 모듈의 핵심 규칙이다.

무음 구간은 두 규칙으로 다룬다.

1. 음량 기준은 -20dB이다. 보도영상은 내레이션과 배경음이 계속 깔려 디지털 무음이 거의 없다.
   `-30dB에서 2초 이상`은 39.5분에 11번뿐이고, `-20dB에서 0.5초`로 보면 297번 나온다.
2. 무음 구간이 있다고 바로 끊지 않는다. 검출된 무음 구간의 80%가 기사 안쪽에 있으므로
   무음 구간마다 끊으면 클립이 2~3배로 늘어난다.

측정: MI가 자른 보도영상 두 편(영상 A 39.4분·39클립, 영상 B 59.7분·51클립)에 돌렸다.
무음 비율은 끊은 위치가 무음 구간 안에 있었는지(발화를 자르지 않았는지)다.
MI 일치율은 MI의 기사 경계와 ±2초 안에서 겹쳤는지다.
지금 규칙으로 영상 A는 78개·무음 81%·MI 34%, 영상 B는 114개·무음 85%·MI 40%다.

MI 일치율 34~40%는 이 방식의 한계다. 기사 경계를 찾으려면 내용을 해석하는 모델이 필요하다.
기사 경계 검출은 MI가 맡는다. 그래서 결과를 의미 단위라고 부르지 않는다.
화면과 문서도 고정 길이 구간으로 부른다.

원본은 한 번만 읽는다. 컷과 무음을 따로 재면 원본을 두 번 내려받는다(40분 720p면 1GB 왕복).
한 번의 `ffmpeg` 호출에 두 필터를 함께 걸어 디코딩 한 번으로 끝낸다.
오디오가 없는 입력에 `[0:a]`를 걸면 필터 그래프 구성부터 실패한다. 그래서 `ffprobe`로
오디오 스트림을 먼저 확인해 걸 필터를 정한다(헤더만 읽는 Range 요청이라 비용이 작다).

상한은 샷을 쪼개지 않는다. 한 샷이 상한보다 길면 그대로 내보낸다.
샷 중간에서 끊을 근거가 없기 때문이다(측정한 최대 샷 길이 73초).
상한은 여러 샷을 얼마나 모을지 정하는 기준이다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable

from reborn_transcode.input_guard import guard_args

log = logging.getLogger(__name__)

_FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
_FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

#: 장면 전환 임계값. 낮을수록 잘게 나뉜다. 0.3은 보도영상에서 컷은 잡고
#: 카메라 흔들림은 넘기는 값이다. 프로젝트별로 달라야 하면 잡 페이로드로 받는다.
DEFAULT_THRESHOLD = 0.3

#: 샷의 최소 길이(초). 이보다 짧은 조각은 앞 구간에 붙인다.
#: 자막 전환이나 플래시는 한 프레임짜리 경계를 만든다.
#: 이 경계를 샷으로 두면 라벨러가 열어 볼 수 없을 만큼 짧은 클립이 된다.
MIN_SHOT_SEC = 0.6

#: 묶은 구간의 길이 상한(초). 넘으면 무음이 없어도 끊는다.
#: 끊지 않으면 한 클립이 몇 분짜리가 되어 라벨러가 한 번에 볼 수 없다.
MERGE_MAX_SEC = 40.0

#: 묶은 구간의 최소 길이(초). 이보다 짧게 끊지 않는다.
#: 무음이 자주 나오는 구간이 잘게 나뉘는 것을 막는다.
MERGE_MIN_SEC = 5.0

#: 무음 구간으로 판정할 음량 상한(dBFS).
#: 보도영상은 배경음·앰비언스가 계속 깔려 -30dB 아래 구간이 거의 없다.
#: 39.5분 영상에서 -30dB·2초 기준은 11회, -20dB·0.5초 기준은 297회 검출된다.
SILENCE_NOISE_DB = -20

#: 무음 구간으로 판정할 최소 길이(초). 발화 사이의 짧은 무음을 잡으려고 짧게 둔다.
#: 기사 경계는 무음 길이로 가를 수 없다.
SILENCE_MIN_SEC = 0.5

#: 검출 제한 시간(초). 멈춘 ffmpeg에 워커가 묶이지 않게 한다.
#: 리스 만료를 기다리면 그동안 워커가 다른 잡을 처리하지 못한다.
#: 페이로드의 `timeout_s`가 있으면 그 값을 쓴다. 생산자가 리스와 다른 값으로 정한다.
DEFAULT_TIMEOUT_S = 3600

#: 하트비트 간격의 기본값(초). 페이로드의 `heartbeat_every_s`가 있으면 그 값을 쓴다.
DEFAULT_HEARTBEAT_EVERY_S = 60

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")
# ffmpeg는 실패하면 입력 URL을 stderr에 그대로 출력한다. presigned 서명은 만료 전까지
# 객체 접근 권한과 같으므로 메시지가 생기는 곳에서 지운다(probe.py와 같은 이유).
_QUERY_RE = re.compile(r"\?[^\s'\"]*")


class SceneSplitError(RuntimeError):
    """씬 분할 실패. `transient`면 재큐잉 대상이고 시도 횟수를 소모하지 않는다."""

    def __init__(self, message: str, *, transient: bool) -> None:
        """메시지와 일시 실패 여부를 저장한다."""
        super().__init__(message)
        self.transient = transient


def _redact(text: str) -> str:
    """URL 쿼리스트링을 `?…`로 바꾼다. presigned 서명이 유출되지 않게 한다."""
    return _QUERY_RE.sub("?…", text)


def has_audio(uri: str, *, timeout: int = 60) -> bool:
    """오디오 스트림이 있는지 확인한다. 걸 필터를 정하려고 검출 전에 부른다.

    오디오가 없는데 `[0:a]`를 걸면 필터 그래프 구성부터 실패한다.
    헤더만 읽는 Range 요청이라 비용이 작다(`probe.py`와 같은 근거).

    ffprobe가 실패(rc≠0)하거나 시간 초과·JSON 오류가 나면 `False`를 돌려준다.
    이때는 무음 정보 없이 상한만으로 샷을 묶는다.
    여기서 예외를 내면 오디오와 무관한 이유로 분할 전체가 실패한다.
    단, ffprobe 실행 파일이 없으면(`FileNotFoundError`) 예외가 그대로 나간다.
    """
    cmd = [_FFPROBE, "-v", "error", "-select_streams", "a",
           "-show_entries", "stream=index", "-print_format", "json", *guard_args(uri), uri]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            return False
        return bool(json.loads(proc.stdout or b"{}").get("streams"))
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return False


def build_command(
    uri: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    silence_min_sec: float = SILENCE_MIN_SEC,
    with_audio: bool = True,
    threads: int = 0,
    ffmpeg: str = _FFMPEG,
) -> list[str]:
    """검출 명령을 만든다. 한 번의 디코딩으로 컷과 무음을 함께 받는다.

    컷과 무음을 따로 재면 원본을 두 번 내려받는다.
    테스트가 인자를 검증할 수 있게 별도 함수로 둔다(`probe.build_command`와 같은 이유).
    `threads`가 0보다 크면 디코더(`-i` 앞)와 필터 그래프(`-filter_complex_threads`)의 스레드를
    그 수로 묶는다. 같은 워커에서 인코딩과 함께 돌 때 호스트 코어 수만큼 스레드를 만들지 않게 한다.
    """
    scene = f"[0:v]select='gt(scene,{threshold})',showinfo[v]"
    dec = ["-threads", str(threads)] if threads > 0 else []
    graph = ["-filter_complex_threads", str(threads)] if threads > 0 else []
    head = [ffmpeg, "-nostdin", "-hide_banner", *dec, *guard_args(uri), "-i", uri, *graph]
    if not with_audio:
        return [*head, "-filter_complex", scene, "-map", "[v]", "-f", "null", "-"]
    silence = f"[0:a]silencedetect=noise={SILENCE_NOISE_DB}dB:d={silence_min_sec}[a]"
    return [*head, "-filter_complex", f"{scene};{silence}",
            "-map", "[v]", "-map", "[a]", "-f", "null", "-"]


def parse(stderr: str) -> tuple[list[float], list[tuple[float, float]]]:
    """ffmpeg stderr에서 `(컷 시각, 무음 구간)`을 읽는다.

    마지막 무음은 닫히지 않은 채 끝날 수 있다(영상이 조용히 끝나면 `silence_end`가 없다).
    시작과 끝이 짝을 이루는 것만 쓴다. 없는 끝을 추정하면 실제로 없는 경계에서 끊게 된다.
    """
    cuts = [float(x) for x in _PTS_RE.findall(stderr)]
    starts = [float(x) for x in _SILENCE_START_RE.findall(stderr)]
    ends = [float(x) for x in _SILENCE_END_RE.findall(stderr)]
    return cuts, list(zip(starts, ends, strict=False))


def _wait(
    proc: subprocess.Popen, timeout: float, on_heartbeat: Callable[[], None] | None,
    heartbeat_every_s: float,
) -> bytes:
    """ffmpeg가 끝날 때까지 기다리며 주기마다 하트비트를 부르고 stderr를 돌려준다.

    `communicate`는 시간 초과 뒤 다시 불러도 그동안의 출력을 잃지 않는다.
    그래서 하트비트 간격으로 끊어 기다려도 결과는 한 번에 기다린 것과 같다.

    Raises:
        SceneSplitError: 실행 상한을 넘긴 경우. 프로세스를 끝낸 뒤 일시 오류로 낸다.
            시간 초과는 환경 문제일 수 있으므로 시도 횟수를 소모하지 않는다.
    """
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        step = min(heartbeat_every_s, left) if on_heartbeat else left
        try:
            _, err = proc.communicate(timeout=max(step, 0.0))
            return err or b""
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                proc.kill()
                proc.communicate()
                raise SceneSplitError(f"장면 검출이 {timeout:g}초 안에 끝나지 않았습니다",
                                      transient=True) from None
            if on_heartbeat is not None:
                on_heartbeat()


def detect(
    uri: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    silence_min_sec: float = SILENCE_MIN_SEC,
    timeout: float = DEFAULT_TIMEOUT_S,
    threads: int = 0,
    on_heartbeat: Callable[[], None] | None = None,
    heartbeat_every_s: float = DEFAULT_HEARTBEAT_EVERY_S,
) -> tuple[list[float], list[tuple[float, float]]]:
    """원본을 읽어 컷과 무음을 찾는다. `threads`의 뜻은 `build_command`와 같다.

    `on_heartbeat`를 주면 검출이 도는 동안 `heartbeat_every_s`마다 부른다.
    리스가 처리 시간을 기준으로 이어지게 하는 값이다.

    Raises:
        SceneSplitError: ffmpeg가 실패하거나 시간을 넘긴 경우. 읽지 못한 영상을
            컷이 없는 영상(빈 결과)으로 처리하면 영상 전체가 한 클립이 되어 잘못 잘린 채 넘어간다.
    """
    cmd = build_command(uri, threshold=threshold, silence_min_sec=silence_min_sec,
                        with_audio=has_audio(uri), threads=threads)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    stderr = _wait(proc, timeout, on_heartbeat, heartbeat_every_s)
    if proc.returncode != 0:
        tail = _redact(stderr.decode("utf-8", errors="replace"))[-300:]
        # 원문(ffmpeg stderr 끝부분)은 워커 로그에만 남긴다. 한글 문장 뒤에 붙이면
        # 화면용 변환기(`error_text.operator_text`)가 앱 문장으로 판정해 그대로 통과시킨다.
        # 그러면 타임라인에 원문이 표시된다.
        log.warning("장면 검출 실패: %s", tail)
        raise SceneSplitError("장면 검출 도구가 영상을 읽지 못했습니다", transient=False)
    return parse(stderr.decode("utf-8", errors="replace"))


# ── 순수 계산. 여기서부터는 미디어 바이트를 읽지 않는다 ───────────────────────────


def to_shots(cuts: list[float], duration: float) -> list[dict]:
    """컷 시각을 샷 구간 `[{"start", "end"}, …]`으로 바꾼다. 구간은 틈도 겹침도 없이 영상을 덮는다.

    틈이 있으면 그 구간은 라벨링에서 통째로 빠진다. 범위 밖·중복·너무 짧은 경계는 버린다.
    그래서 검출기가 어떤 값을 돌려줘도 구간이 영상 전체를 빈틈없이 덮는다.
    """
    if duration <= 0:
        return []
    bounds = sorted({round(c, 3) for c in cuts if 0 < c < duration})
    out: list[dict] = []
    start = 0.0
    for b in bounds:
        if b - start < MIN_SHOT_SEC:
            continue
        out.append({"start": round(start, 3), "end": b})
        start = b
    # 마지막 조각이 너무 짧으면 앞 구간을 끝까지 늘린다. 짧은 꼬리 클립을 만들지 않는다.
    if out and duration - start < MIN_SHOT_SEC:
        out[-1]["end"] = round(duration, 3)
    else:
        out.append({"start": round(start, 3), "end": round(duration, 3)})
    return out


def merge(shots: list[dict], silences: list[tuple[float, float]], duration: float,
          max_sec: float = MERGE_MAX_SEC) -> list[dict]:
    """샷을 길이 상한까지 묶는다. 끊는 위치는 무음 구간 안의 샷 경계에서 고른다.

    다음 샷까지 붙이면 상한을 넘을 때 끊는다.
    무음 구간은 끊을 위치만 고른다. 끊어야 할 때 창(`MERGE_MIN_SEC`~`max_sec`) 안에서
    무음 구간 안에 있는 경계 중 가장 늦은 것을 고른다.
    길이를 상한에 가깝게 채워야 조각 수가 늘지 않는다.
    창 안에 그런 경계가 없으면 창의 마지막 샷 경계에서 끊는다.

    검출된 무음 구간의 80%가 기사 안쪽에 있으므로, 무음 구간마다 끊으면
    클립이 2~3배로 늘어난다(246개 대 114개).

    창 안에 경계가 없으면(샷 하나가 상한보다 길다) 그 샷을 그대로 내보낸다.
    샷 중간에서 끊을 근거가 없기 때문이다.
    """
    if not shots:
        return []

    def at_rest(t: float) -> bool:
        """이 시각이 무음 구간 안에 있으면 True.

        무음 구간 안에서 끊으면 발화가 잘리지 않으므로 구간 포함 여부만 본다.
        구간 중앙과 허용 오차로 비교하는 방식(`|t - 중앙| <= 허용치`)은
        같은 조건을 덜 정확하게 판정한다.
        """
        return any(a <= t <= b for a, b in silences)

    merged: list[dict] = []
    start = shots[0]["start"]
    i = 0
    # 끊어야 할 때만 끊는다. 남은 길이가 상한 안이면 나머지를 한 구간으로 둔다.
    # 이 조건이 없으면 창 안에 무음 구간이 있을 때마다 끝까지 잘게 나눈다.
    # 예를 들어 상한이 40초여도 24초 영상이 셋으로 잘린다.
    while i < len(shots) - 1 and duration - start > max_sec:
        window = [j for j in range(i, len(shots) - 1)
                  if MERGE_MIN_SEC <= shots[j]["end"] - start <= max_sec]
        if not window:
            # 창이 비면 다음 경계가 이미 상한을 넘은 상태다(샷 하나가 상한보다 길다).
            over = next((k for k in range(i, len(shots) - 1)
                         if shots[k]["end"] - start > max_sec), None)
            if over is None:
                break
            window = [over]
        pick = next((j for j in reversed(window) if at_rest(shots[j]["end"])), window[-1])
        merged.append({"start": round(start, 3), "end": round(shots[pick]["end"], 3)})
        start = shots[pick]["end"]
        i = pick + 1

    merged.append({"start": round(start, 3), "end": round(duration, 3)})
    return merged


def rule_text(max_sec: float = MERGE_MAX_SEC) -> str:
    """분할 기준을 설명하는 한 줄을 만든다. 화면이 그대로 표시하므로 실제로 쓴 값을 적는다.

    상수를 적으면 프로젝트가 상한을 바꿨을 때 화면에 이전 값이 표시된다.

    결과는 MI 경계와 34~40%만 겹친다. "의미 단위"라고 쓰면 라벨러가
    클립 하나에 기사 하나가 담겼다고 오해하므로 그 말을 쓰지 않는다.
    """
    # 화면 문구라서 분야 표준 용어(무음 구간)를 쓴다.
    return f"샷 경계 · 최대 {max_sec:g}초 · 무음 구간 우선"


def run_job(job: dict, on_heartbeat: Callable[[], None] | None = None) -> dict:
    """잡 1건을 DB 없이 실행한다. 페이로드 하나로 일하고 결과 dict를 돌려준다.

    페이로드는 `queues.md`의 `scene_split_one` 스키마다::

        {"video_id", "token", "input_url", "duration_seconds", "threshold"?, "max_seconds"?,
         "timeout_s"?, "heartbeat_every_s"?}

    `on_heartbeat`는 검출이 도는 동안 `heartbeat_every_s`마다 불린다(`detect`).

    Returns:
        dict: `{"segments": [{"start","end"}, …], "raw_count": int, "rule": str}`.
            묶기 전 개수도 함께 돌려준다. 묶은 개수만 받으면 받는 쪽은 그 수가 많은지
            적은지 판단할 수 없다.

    Raises:
        SceneSplitError: 분류된 실패. 최종 판정은 DB 쪽(`complete`)이 내린다.
    """
    duration = float(job.get("duration_seconds") or 0)
    if duration <= 0:
        # 영상 길이가 없으면 구간을 만들지 않는다. 길이를 추정하지 않는다.
        # 생산자가 채워야 할 값이라 재시도해도 바뀌지 않는다(영구 실패).
        raise SceneSplitError("영상 길이를 알 수 없어 구간을 만들 수 없습니다", transient=False)
    threshold = float(job.get("threshold") or DEFAULT_THRESHOLD)
    # 상한은 생산자가 정한다. 프로젝트 설정에서 온다.
    # 값이 없으면 기본값을 쓴다. 그래서 이 옵션이 없는 이전 형식의 잡도 같은 결과를 낸다.
    max_sec = float(job.get("max_seconds") or MERGE_MAX_SEC)
    # 스레드 수는 인코딩 잡과 같은 값(`TRANSCODE_THREADS`)을 쓴다. 같은 워커와 CPU 상한을 나눠 쓴다.
    threads = int(os.getenv("TRANSCODE_THREADS") or job.get("threads") or 0)
    cuts, silences = detect(
        job["input_url"], threshold=threshold, threads=threads,
        timeout=float(job.get("timeout_s") or DEFAULT_TIMEOUT_S),
        on_heartbeat=on_heartbeat,
        heartbeat_every_s=float(job.get("heartbeat_every_s") or DEFAULT_HEARTBEAT_EVERY_S))
    shots = to_shots(cuts, duration)
    return {
        "segments": merge(shots, silences, duration, max_sec),
        "raw_count": len(shots),
        "rule": rule_text(max_sec),
    }
