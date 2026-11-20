"""영상 메타데이터 확인, 프레임 읽기, 인코딩과 오디오 결합을 맡는다."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache

import cv2
import numpy as np

from .cpu import job_threads

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")


class DeidentError(RuntimeError):
    """비식별화를 끝내지 못한 경우. 메시지는 처리 이력에 그대로 남는다."""


@dataclass(frozen=True)
class VideoInfo:
    """처리에 필요한 영상 정보."""

    fps: float
    fps_text: str
    has_audio: bool
    duration_s: float


def probe(path: str) -> VideoInfo:
    """ffprobe로 프레임률, 오디오 유무, 길이를 읽는다."""
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise DeidentError("영상 정보를 읽지 못했습니다")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise DeidentError("영상 스트림이 없습니다")
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    if fps <= 0:
        raise DeidentError("프레임률을 읽지 못했습니다")
    return VideoInfo(fps=fps, fps_text=rate, duration_s=float(data.get("format", {}).get(
        "duration") or 0), has_audio=any(s.get("codec_type") == "audio" for s in streams))


def frames(path: str) -> Iterator[np.ndarray]:
    """영상의 프레임을 처음부터 BGR로 하나씩 돌려준다."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise DeidentError("영상을 열지 못했습니다")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def _encoder() -> list[str]:
    """영상 인코더 인자를 고른다. NVENC로 인코딩되면 GPU로, 아니면 libx264로 인코딩한다.

    `ESSENTIAL_DEIDENT_ENCODER=nvenc`이면 확인 없이 NVENC를 쓰고, `libx264`이면 늘 libx264를 쓴다.
    """
    wanted = os.environ.get("ESSENTIAL_DEIDENT_ENCODER", "auto")
    if wanted == "nvenc" or (wanted == "auto" and _nvenc_works()):
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


@cache
def _nvenc_works() -> bool:
    """프레임 하나를 h264_nvenc로 인코딩해 보고 성공하면 참을 돌려준다. 프로세스마다 한 번 확인한다.

    ffmpeg 빌드에 인코더가 있는지만 보면 NVENC가 없는 GPU(A100·H100 등)나 드라이버의 영상
    라이브러리가 컨테이너에 들어오지 않은 경우에도 참이 되어, 실제 인코딩에서 비식별화가 실패한다.
    """
    if shutil.which(FFMPEG) is None:
        return False
    try:
        done = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:d=0.1", "-frames:v", "1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


class Writer:
    """BGR 프레임을 H.264로 인코딩하고 원본 오디오를 붙인다. 스레드는 `cpu.job_threads`만큼 쓴다."""

    def __init__(self, dst: str, src: str, width: int, height: int, info: VideoInfo):
        threads = str(job_threads())
        cmd = [FFMPEG, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
               "-r", info.fps_text, "-i", "-"]
        if info.has_audio:
            cmd += ["-i", src, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
        # 인코더와 필터 스레드를 잡 몫으로 묶는다. 입력이 원시 프레임이라 디코더 스레드는 없다.
        cmd += [*_encoder(), "-pix_fmt", "yuv420p", "-threads", threads,
                "-filter_threads", threads, "-movflags", "+faststart", dst]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        """프레임 한 장을 인코더에 넘긴다."""
        assert self.proc.stdin is not None
        self.proc.stdin.write(frame.tobytes())

    def close(self) -> None:
        """입력을 닫고 인코딩이 끝나기를 기다린다. 실패하면 DeidentError를 낸다."""
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode("utf-8", "replace") if self.proc.stderr else ""
        if self.proc.wait() != 0:
            raise DeidentError(f"결과 영상 인코딩에 실패했습니다: {err[-300:]}")


def count_frames(path: str) -> int:
    """결과 영상의 프레임 수를 디코딩해 센다."""
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=False)
    try:
        return int(proc.stdout.strip().split(",")[0])
    except ValueError:
        return -1
