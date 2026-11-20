"""FFmpeg 스트리밍 h264 인코딩을 제공한다.

워커는 `transcode_streaming`으로 산출물 1종을 인코딩한다. S3 업로드는 호출자가 맡는다.

설계 결정:
  - subprocess를 직접 호출한다. ffmpeg-python 의존성을 추가하지 않는다.
  - h264 + AAC + faststart movflags를 쓴다(HTTP 스트리밍 재생 호환).
  - 영상 인코더는 NVENC로 인코딩되는 서버면 h264_nvenc, 아니면 libx264다(`_use_nvenc`).
  - 실패는 TranscodeError 하나로 낸다(returncode와 stderr 끝부분).
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from reborn_transcode.input_guard import guard_args

_DEFAULT_FFMPEG = "ffmpeg"

log = logging.getLogger(__name__)

#: libx264 프리셋에 대응하는 NVENC 프리셋(p1 가장 빠름, p7 가장 느림).
_NVENC_PRESET = {"ultrafast": "p1", "superfast": "p1", "veryfast": "p2", "faster": "p3",
                 "fast": "p4", "medium": "p5", "slow": "p6", "slower": "p7", "veryslow": "p7"}


class TranscodeError(RuntimeError):
    """ffmpeg 실패 예외. returncode와 stderr 끝부분을 메시지에 담는다.

    `stderr`에는 서명을 지운 stderr 뒷부분(최대 4,000자)을 따로 둔다. 메시지의 300자로는
    실패 원인 줄이 잘려 다른 인코더로 다시 할지 판정할 수 없다.
    """

    def __init__(self, message: str, *, stderr: str = "") -> None:
        """메시지와 서명을 지운 stderr를 저장한다."""
        super().__init__(message)
        self.stderr = stderr


# ── 스트리밍 h264 인코딩 ────────────────────────────────────────
#   * 입력에 presigned GET URL을 그대로 넘겨 전체 다운로드를 생략한다.
#   * Popen과 `-progress pipe:1`로 진행을 읽어 수십 분 인코딩 중에도 호출자가 리스를 연장한다.
#   * 리스를 잃었거나 진행이 멈추면 호출자가 cancel 이벤트로 인코딩을 중단해 CPU를 돌려받는다.
#   * 출력 코덱은 h264로 고정하고 업스케일하지 않는다(height=None이면 scale 생략).

# ffmpeg는 실패하면 입력 URL을 stderr에 출력한다. presigned 서명이 유출되지 않게 지운다
# (probe.py와 같은 이유).
_QUERY_RE = re.compile(r"\?[^\s'\"]*")

#: NVENC·CUDA 쪽에서 난 실패의 stderr 표지. 이 표지가 있을 때만 libx264로 다시 인코딩한다.
#: 세션 한도·장치 없음·GPU 메모리 부족이 해당한다.
#: 입력을 읽지 못한 실패(403·손상 파일)를 CPU로 다시 돌리면 같은 이유로 또 실패하면서
#: 원본을 한 번 더 내려받고 CPU 상한을 차지한다.
_NVENC_FAILURE_RE = re.compile(
    r"OpenEncodeSessionEx|No capable devices|out of memory|nvenc|cuda", re.IGNORECASE)


@dataclass(frozen=True)
class TargetSpec:
    """산출물 1종의 인코딩 사양. height=None이면 해상도를 유지하고 재인코딩만 한다.

    crf는 측정으로 정했다. av1 원본을 h264 crf22로 만들면 해상도를
    절반으로 줄여도 용량이 늘어난다(+23%). 그래서 분석본(사람이 보지 않는 사본)은 crf24를 쓴다.
    """
    label: str
    height: int | None
    crf: int
    # 프리셋은 산출마다 다르다. 기본값은 보수적인 fast다.
    # 생산자(build_jobs)가 산출별로 정해 보낸다.
    preset: str = "fast"
    # 인코딩 스레드 수. 0이면 ffmpeg가 자동으로 정한다.
    # 컨테이너에서는 `nproc`이 cgroup 할당 대신 호스트 코어 수를 보고해 자동 값이 너무 크다
    # (예: `--cpus 2` 컨테이너에서 8). 스레드가 할당보다 많으면 문맥 전환 비용만 늘어난다.
    threads: int = 0


def _use_nvenc(ffmpeg: str) -> bool:
    """이번 인코딩에 h264_nvenc를 쓸지 정한다.

    `TRANSCODE_ENCODER`가 `nvenc`면 확인 없이 쓰고, `libx264`면 쓰지 않는다.
    비었거나 `auto`면 NVENC로 실제 인코딩되는 서버일 때만 쓴다(`_nvenc_works`).
    """
    wanted = os.environ.get("TRANSCODE_ENCODER", "auto")
    if wanted == "nvenc":
        return True
    return wanted == "auto" and _nvenc_works(ffmpeg)


@cache
def _nvenc_works(ffmpeg: str) -> bool:
    """프레임 하나를 h264_nvenc로 인코딩해 보고 성공하면 참을 돌려준다. 프로세스마다 한 번 확인한다.

    ffmpeg 빌드에 인코더가 있는지만 보면 NVENC가 없는 GPU(A100·H100 등)나 드라이버의 영상
    라이브러리가 컨테이너에 들어오지 않은 경우에도 참이 되어 모든 변환이 실패한다.
    비식별화 모듈(`essential_deident.video`)도 같은 방법으로 확인한다.
    """
    if shutil.which(ffmpeg) is None:
        return False
    try:
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:d=0.1", "-frames:v", "1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


def _video_args(spec: TargetSpec, nvenc: bool) -> list[str]:
    """영상 인코더 인자를 만든다.

    NVENC는 crf 대신 고정 품질(`-cq`)을 같은 값으로 쓴다. 같은 값이면 libx264보다 파일이 조금
    커지는 경향이 있다. NVENC의 h264는 8비트만 인코딩하므로 10비트 원본도 `yuv420p`로 맞춘다.
    """
    if nvenc:
        return ["-c:v", "h264_nvenc", "-preset", _NVENC_PRESET.get(spec.preset, "p4"),
                "-rc", "vbr", "-cq", str(spec.crf), "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf)]


def build_h264_command(
    *, input_uri: str, output_path: Path, spec: TargetSpec, ffmpeg: str = _DEFAULT_FFMPEG,
    nvenc: bool | None = None,
) -> list[str]:
    """h264 인코딩 argv를 만든다.

    단위 테스트가 scale 생략과 progress 플래그를 검증할 수 있게 따로 둔다.

    Args:
        nvenc: h264_nvenc를 쓸지. None이면 서버에서 NVENC로 인코딩되는지 보고 정한다(`_use_nvenc`).
    """
    if nvenc is None:
        nvenc = _use_nvenc(ffmpeg)
    vf = ["-vf", f"scale=-2:{spec.height}"] if spec.height else []
    # `-threads`는 놓인 위치의 파일에만 적용된다. `-i` 앞은 디코더, 출력 앞은 인코더다.
    # 필터(스케일) 스레드는 따로 `-filter_threads`로 묶는다. 셋 중 하나라도 비우면 그 단계가
    # 호스트 코어 수만큼 스레드를 만든다.
    threads = ["-threads", str(spec.threads)] if spec.threads else []
    out_threads = ([*threads, "-filter_threads", str(spec.threads)] if spec.threads else [])
    return [
        ffmpeg, "-y", *threads, *guard_args(input_uri), "-i", input_uri, *vf,
        *_video_args(spec, nvenc), *out_threads,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",   # 진행 상황을 key=value 줄로 낸다. 리스 연장에 쓴다
        str(output_path),
    ]


def transcode_streaming(
    input_uri: str,
    output_path: Path,
    spec: TargetSpec,
    *,
    on_progress=None,
    cancel=None,
    ffmpeg: str = _DEFAULT_FFMPEG,
) -> None:
    """input(경로 또는 presigned URL)을 spec대로 h264 인코딩한다.

    Args:
        input_uri: 로컬 경로 또는 http(s) URL. URL이면 ffmpeg가 스트리밍으로 읽는다.
        on_progress: `fn(seconds: float)`. 인코딩한 미디어 시간을 받는다. progress 줄마다 호출한다
            (호출자는 이 값으로 리스 연장과 정체 감시를 한다).
        cancel: `.is_set()`이 있는 이벤트. 참이 되면 프로세스를 끝내고 TranscodeError를 낸다.

    NVENC 인코딩이 NVENC·CUDA 오류로 실패하면 libx264로 한 번 더 인코딩한다(`_NVENC_FAILURE_RE`).
    시험 인코딩은 통과했어도 GPU의 동시 인코딩 세션 한도나 GPU 메모리 부족으로 실제 인코딩이
    실패할 수 있다. 입력 실패처럼 인코더와 무관한 실패와 취소는 다시 하지 않는다.

    Raises:
        TranscodeError: rc≠0이거나 취소된 경우. stderr 끝부분은 쿼리스트링(서명)을 지운 뒤 싣는다.
    """
    nvenc = _use_nvenc(ffmpeg)
    try:
        _run(build_h264_command(input_uri=input_uri, output_path=output_path, spec=spec,
                                ffmpeg=ffmpeg, nvenc=nvenc), spec, on_progress, cancel)
    except _CancelledError:
        raise
    except TranscodeError as e:
        if not nvenc or not _NVENC_FAILURE_RE.search(e.stderr or str(e)):
            raise
        log.warning("NVENC 인코딩 실패, libx264로 다시 인코딩한다: %s", e)
        _run(build_h264_command(input_uri=input_uri, output_path=output_path, spec=spec,
                                ffmpeg=ffmpeg, nvenc=False), spec, on_progress, cancel)


class _CancelledError(TranscodeError):
    """리스 상실이나 진행 정체로 인코딩을 멈춘 경우. 다른 인코더로 다시 하지 않는다."""


def _run(cmd: list[str], spec: TargetSpec, on_progress, cancel) -> None:
    """ffmpeg 명령 하나를 실행하고 진행을 전달한다.

    Raises:
        _CancelledError: cancel 이벤트가 참이 된 경우.
        TranscodeError: rc≠0인 경우.
    """
    # stderr는 파일로 받는다. stdout(progress)만 읽으면 stderr 파이프가 가득 차서 교착 상태가 된다.
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
        try:
            for line in proc.stdout:  # `-progress`는 key=value 줄 스트림이다
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise _CancelledError(f"{spec.label} 취소됨 (리스 상실 또는 진행 정체)")
                if on_progress and line.startswith("out_time_ms="):
                    # ffmpeg의 out_time_ms는 이름과 달리 마이크로초다
                    with contextlib.suppress(ValueError):
                        on_progress(int(line.split("=", 1)[1]) / 1_000_000)
        finally:
            proc.stdout.close()
        rc = proc.wait()
        if rc != 0:
            err.seek(0)
            text = _QUERY_RE.sub("?…", err.read().decode("utf-8", errors="replace"))
            raise TranscodeError(f"ffmpeg {spec.label} failed (rc={rc}): {text[-300:]}",
                                 stderr=text[-4000:])

