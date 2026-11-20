"""영상 파일 하나를 비식별화해 결과 영상, 좌표 파일, 결과 보고를 만든다.

영상을 두 번 읽는다. 첫 번째는 검출, 두 번째는 가림과 인코딩이다. 프레임 전체를 메모리에 올리지
않고 상자만 보관하기 위해서다. 두 번 읽은 프레임 수가 다르면 상자가 엉뚱한 프레임에 그려지므로
실패로 처리한다.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from . import coordinates, render, tracking, video
from .detector import DetectorConfig, FaceDetector
from .video import DeidentError

MODEL_NAME = "essential-yunet"
MODEL_VERSION = "yunet-2023mar"


@dataclass(frozen=True)
class Options:
    """처리 설정. RebornStudio는 이 값을 고정해 쓴다."""

    detector: DetectorConfig = field(default_factory=DetectorConfig)
    linger: int = 5
    pad: float = render.PAD
    mosaic_scale: float = render.MOSAIC_SCALE


def anonymize(src: str, dst: str, coords_path: str | None = None, *,
              options: Options | None = None, detector: FaceDetector | None = None,
              on_progress: Callable[[str, int], None] | None = None) -> dict:
    """원본 영상의 얼굴을 모자이크로 가린 결과 영상을 만든다.

    Args:
        src: 원본 영상 경로.
        dst: 결과 영상(MP4) 경로.
        coords_path: 좌표 파일 경로. 없으면 좌표 파일을 쓰지 않는다.
        options: 처리 설정. 없으면 기본값을 쓴다.
        detector: 미리 만든 검출기. 여러 영상을 처리할 때 모델을 한 번만 올리려고 받는다.
        on_progress: (단계 이름, 처리한 프레임 수)를 받는 함수. 하트비트에 쓴다.

    Returns:
        처리 결과 보고. RebornStudio Pro가 비식별화 이력에 남기는 필드와 이름이 같다.

    Raises:
        DeidentError: 영상을 읽거나 인코딩하지 못한 경우, 두 번 읽은 프레임 수가 다른 경우.
    """
    opts = options or Options()
    started = time.monotonic()
    info = video.probe(src)
    det = detector or FaceDetector(opts.detector)
    progress = on_progress or (lambda _stage, _n: None)

    per_frame: list[list] = []
    batch: list = []
    size = None
    t0 = time.monotonic()
    for frame in video.frames(src):
        size = size or (frame.shape[1], frame.shape[0])
        batch.append(frame)
        if len(batch) == opts.detector.batch_size:
            per_frame += det.detect_batch(batch)
            batch = []
            progress("detect", len(per_frame))
    if batch:
        per_frame += det.detect_batch(batch)
    total = len(per_frame)
    if total == 0 or size is None:
        raise DeidentError("영상에서 프레임을 한 장도 읽지 못했습니다")
    t_detect = time.monotonic() - t0

    frame_boxes: dict[int, list[tuple]] = defaultdict(list)
    for i, raw in enumerate(per_frame):
        frame_boxes[i] += [b[:4] for b in raw]
    hist = tracking.track(per_frame, info.fps, opts.detector.score)
    filled = tracking.fill(frame_boxes, hist, total, opts.linger)

    t0 = time.monotonic()
    width, height = size
    writer = video.Writer(dst, src, width, height, info)
    rendered = 0
    try:
        for i, frame in enumerate(video.frames(src)):
            if i >= total:
                break
            writer.write(render.mosaic(frame, frame_boxes.get(i, []), opts.pad,
                                       opts.mosaic_scale))
            rendered += 1
            if rendered % 300 == 0:
                progress("render", rendered)
    finally:
        writer.close()
    if rendered != total:
        raise DeidentError(f"두 번 읽은 프레임 수가 다릅니다 (검출 {total}, 가림 {rendered})")
    t_render = time.monotonic() - t0

    detected = sum(1 for raw in per_frame if raw)
    report = {
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "device": det.device,
        "masked": detected > 0,
        "review_needed": detected == 0,
        "review": ["no-detections"] if detected == 0 else [],
        # CUDA를 기대했는데 CPU로 돈 경우를 남긴다. 처리 시간이 길어진 원인을 이력에서 찾을 수 있다.
        "notices": ["cpu-fallback"] if getattr(det, "cpu_fallback", False) else [],
        "frames": total,
        "detected_frames": detected,
        "detection_rate": round(detected / total, 4),
        "boxes": sum(len(r) for r in per_frame),
        "filled_boxes": filled,
        "elapsed_s": round(time.monotonic() - started, 1),
        "detect_s": round(t_detect, 1),
        "render_s": round(t_render, 1),
        "source_duration_s": round(info.duration_s, 1),
    }
    if coords_path:
        coordinates.write(coords_path, frame_boxes, fps=info.fps, frame_count=total,
                          width=width, height=height, pad=opts.pad)
        report["coordinates"] = coordinates.summary()
    return report
