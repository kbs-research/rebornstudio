"""얼굴 상자를 모자이크로 가린다."""

from __future__ import annotations

import cv2
import numpy as np

PAD = 0.15
MOSAIC_SCALE = 0.06


def pad_box(box: tuple, pad: float, width: int, height: int) -> tuple[int, int, int, int]:
    """상자를 가로세로 pad 비율만큼 넓히고 프레임 안 정수 좌표로 자른다."""
    x1, y1, x2, y2 = box[:4]
    w, h = x2 - x1, y2 - y1
    return (max(0, int(np.floor(x1 - w * pad))), max(0, int(np.floor(y1 - h * pad))),
            min(width, int(np.ceil(x2 + w * pad))), min(height, int(np.ceil(y2 + h * pad))))


def mosaic(frame: np.ndarray, boxes: list[tuple], pad: float = PAD,
           scale: float = MOSAIC_SCALE) -> np.ndarray:
    """프레임의 상자 영역을 모자이크로 바꾼다. 프레임을 직접 고친다.

    영역을 가로세로 scale 비율로 줄였다가 최근접 보간으로 다시 키운다. 작은 얼굴은 한 칸이 되어
    단색으로 덮인다.
    """
    h, w = frame.shape[:2]
    for box in boxes:
        x1, y1, x2, y2 = pad_box(box, pad, w, h)
        if x2 <= x1 or y2 <= y1:
            continue
        roi = frame[y1:y2, x1:x2]
        sw = max(1, round((x2 - x1) * scale))
        sh = max(1, round((y2 - y1) * scale))
        small = cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_AREA)
        frame[y1:y2, x1:x2] = cv2.resize(small, (x2 - x1, y2 - y1),
                                         interpolation=cv2.INTER_NEAREST)
    return frame
