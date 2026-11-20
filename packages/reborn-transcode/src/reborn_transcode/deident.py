"""비식별화 러너. 잡 프로토콜과 비식별화 모델 사이의 경계.

이 모듈은 입력을 presigned GET으로 받아 어댑터에 넘긴다.
결과 파일과 얼굴 좌표 JSON은 presigned PUT으로 올리고 처리 결과를 보고한다.
어댑터는 이 프로세스 안에서 돈다. 기본 어댑터는 내장 비식별화 모듈(`essential-yunet`)이다.

다른 모델을 붙일 때도 어댑터 하나만 추가하면 된다.
프로토콜·리스·펜싱·진행률은 그대로다. 이 모듈이 자체 이미지(CUDA 베이스)로 옮겨도
계약은 바뀌지 않는다.

`passthrough`는 스트림을 복사만 하는 검증용 어댑터다.
마스킹하지 않아도 정상 파일이 나오므로 보고서에 마스킹하지 않았다는 사실을 적는다
(`model: "passthrough"`, `masked: false`). 화면이 이 값을 표시하므로 운영자가 비식별화된 결과로
오인하지 않는다. `DEIDENT_MODEL`로 지정해야만 쓴다.
등록되지 않은 모델 이름이면 잡이 실패한다. 잡이 실패하면 파이프라인이 멈춘다.
그래서 비식별화를 켠 프로젝트에서 마스킹하지 않은 영상이 다음 단계로 넘어가지 않는다.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from reborn_transcode.input_guard import guard_args
from reborn_transcode.job_runner import JobError, _put, heartbeating
from reborn_transcode.probe import _redact

log = logging.getLogger(__name__)

_DEFAULT_FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")


def _passthrough(src: Path, dst: Path, coords: Path | None = None) -> dict:
    """스트림을 복사만 한다. 마스킹하지 않으며 프로토콜 검증에만 쓴다. 좌표 파일은 쓰지 않는다."""
    cmd = [_DEFAULT_FFMPEG, "-y", *guard_args(str(src)), "-i", str(src), "-c", "copy",
           "-movflags", "+faststart", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        # ffmpeg 원문은 워커 로그에만 남긴다.
        # 화면 사유에 실으면 운영자가 이해할 수 없는 ffmpeg 출력이 뜬다.
        tail = proc.stderr.decode("utf-8", errors="replace")[-300:]
        log.warning("passthrough 실패: %s", tail)
        raise JobError("검증용 복사(passthrough)에 실패했습니다", transient=False)
    return {"model": "passthrough", "masked": False,
            "note": "비식별화하지 않은 검증용 결과입니다"}


def _essential_yunet(src: Path, dst: Path, coords: Path | None = None) -> dict:
    """내장 비식별화 모듈(YuNet)로 얼굴을 가린다. 좌표 경로가 있으면 좌표 파일도 쓴다.

    모듈은 Apache-2.0 허용형 모델만 쓰므로 이 프로세스 안에서 돌린다.
    모듈은 비식별화 잡을 받는 워커 이미지에만 설치하므로 함수 안에서 import한다.
    """
    from essential_deident import DeidentError, anonymize

    try:
        return anonymize(str(src), str(dst), str(coords) if coords else None)
    except DeidentError as e:
        raise JobError(str(e), transient=False) from e


_ADAPTERS = {"essential-yunet": _essential_yunet, "passthrough": _passthrough}

# 기본 비식별화 모델. 마스킹하는 모델이므로 설정이 없을 때 이 값을 쓴다.
DEFAULT_MODEL = "essential-yunet"


def run_deident_job(job: dict, *, on_heartbeat=None) -> dict:
    """비식별화 잡 1건을 처리한다. 입력을 받아 마스킹하고 결과를 올린다.

    모델은 `DEIDENT_MODEL`로 고르고, 비어 있으면 `DEFAULT_MODEL`을 쓴다.
    잡에 좌표 업로드 주소(`coordinates_put_url`)가 있으면 어댑터가 쓴 좌표 파일도 올린다.

    다운로드·어댑터 실행·업로드 내내 하트비트를 보낸다.
    리스는 하트비트로만 연장된다.
    시작할 때 한 번만 보내면 1GB 다운로드와 처리가 리스(5분)를 넘겨 잡이 회수되고 중복 실행된다.

    진행 초는 `None`으로 보낸다. 어댑터가 프레임 진행을 보고하지 않아 진행도를 계산할 수 없다.
    0을 보내면 화면이 0%에서 멈춘 것으로 잘못 표시한다.

    Returns:
        dict: {"elapsed_s", "report"}. report는 모델·버전·검출 수다.

    Raises:
        JobError: 모델이 구성되지 않은 경우(영구), 다운로드·업로드가 실패한 경우(일시·영구로 분류),
            어댑터가 실패한 경우.
    """
    started = time.monotonic()
    beat_every = float(job.get("heartbeat_every_s") or 60)
    name = os.getenv("DEIDENT_MODEL", "").strip() or DEFAULT_MODEL
    adapter = _ADAPTERS.get(name)
    if adapter is None:
        raise JobError(f"알 수 없는 비식별화 모델입니다 ({name})", transient=False)

    def beat() -> None:
        """리스 연장 하트비트를 보낸다. 진행 초는 계산할 수 없어 None을 보낸다."""
        if on_heartbeat is not None:
            on_heartbeat(None)

    with tempfile.TemporaryDirectory(prefix="rb-deid-") as tmp:
        src, dst = Path(tmp) / "in.mp4", Path(tmp) / "out.mp4"
        # 잡에 좌표 업로드 주소가 있을 때만 어댑터에 좌표 파일 경로를 준다.
        coords = Path(tmp) / "coords.json" if job.get("coordinates_put_url") else None
        try:
            timeout = httpx.Timeout(30.0, read=600.0)
            with httpx.stream("GET", job["input_url"], timeout=timeout) as r:
                r.raise_for_status()
                last = time.monotonic()
                with src.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
                        if time.monotonic() - last > beat_every:
                            beat()
                            last = time.monotonic()
        except httpx.HTTPError as e:
            # 예외 타입명은 화면에 보여 줄 사유가 아니므로 로그에만 남긴다.
            # httpx 예외 문장에는 요청 주소 전체가 들어 있다.
            # 입력 주소는 원본을 읽는 presigned URL이라 쿼리를 지우고 남긴다.
            # 서명은 만료 전까지 원본 접근 권한과 같다.
            log.warning("입력 다운로드 실패: %s", _redact(repr(e)))
            raise JobError("입력 영상을 받는 중 연결이 끊겼습니다", transient=True) from e

        # 어댑터는 별도 스레드에서 돌린다. 본 스레드는 하트비트로 리스를 유지한다
        # (job_runner와 같은 구조).
        out: list = []
        err: list[BaseException] = []

        def _work() -> None:
            """어댑터를 실행하고 결과나 예외를 본 스레드가 읽을 목록에 담는다."""
            try:
                out.append(adapter(src, dst, coords))
            except BaseException as exc:  # noqa: BLE001 — 스레드 경계, 본 스레드로 전달
                err.append(exc)

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(timeout=beat_every)
            if th.is_alive():
                beat()
        if err:
            e = err[0]
            raise e if isinstance(e, JobError) else JobError(
                f"{type(e).__name__}: {e}", transient=False) from e

        # 업로드 동안에도 하트비트를 보낸다. 큰 결과 파일은 업로드만으로 리스(5분)를 넘긴다.
        with heartbeating(on_heartbeat, beat_every, lambda: None):
            _put(job["put_url"], dst, job.get("content_type", "video/mp4"))
            # 좌표를 쓴 어댑터만 보고에 `coordinates`를 싣는다.
            # 서버는 이 키가 있을 때만 좌표 키를 기록한다.
            if coords is not None and "coordinates" in out[0] and coords.exists():
                _put(job["coordinates_put_url"], coords, "application/json")

    return {"elapsed_s": round(time.monotonic() - started, 1), "report": out[0]}
