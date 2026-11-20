"""가림에 실제로 쓴 상자를 좌표 파일(JSON)로 남긴다.

형식은 RebornStudio Pro 비식별화 서버의 좌표 파일 형식 버전 1(`SCHEMA_VERSION`)과 같다.
그래서 RebornStudio Pro가 이 파일을 그대로 읽는다.
상자는 여백을 더한 결과 영상 픽셀 좌표이고, 상자가 있는 프레임만 싣는다.
"""

from __future__ import annotations

import json
import os

from .render import pad_box

SCHEMA_VERSION = 1
CONTENT_TYPE = "application/json"
COORDINATE_SPACE = "output_pixels"


def summary() -> dict:
    """결과 보고에 넣을 좌표 파일 설명."""
    return {"uploaded": True, "schema_version": SCHEMA_VERSION,
            "content_type": CONTENT_TYPE, "coordinate_space": COORDINATE_SPACE}


def write(path: str, frame_boxes: dict[int, list[tuple]], *, fps: float, frame_count: int,
          width: int, height: int, pad: float) -> str:
    """좌표 파일을 쓴다. 임시 파일에 다 쓴 뒤 이름을 바꿔 반쯤 쓴 파일이 남지 않게 한다."""
    frames = []
    for f in range(frame_count):
        boxes = [list(pad_box(b, pad, width, height)) for b in frame_boxes.get(f, ())]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if boxes:
            frames.append({"frame": f, "time_ms": round(f * 1000 / fps) if fps else None,
                           "boxes": boxes})
    doc = {
        "schema_version": SCHEMA_VERSION,
        "coordinate_space": COORDINATE_SPACE,
        "box_format": "xyxy",
        "boxes_include_padding": True,
        "sparse_frames": True,
        "source": {"width": width, "height": height},
        "output": {"width": width, "height": height},
        "fps": float(fps),
        "frame_count": int(frame_count),
        "frames": frames,
    }
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path
