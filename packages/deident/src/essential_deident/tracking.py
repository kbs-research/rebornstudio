"""프레임 사이의 얼굴 상자를 이어 붙여 검출이 빠진 프레임을 채운다.

검출기는 같은 얼굴을 몇 프레임씩 놓친다. 비식별화에서 한 프레임의 누락은 얼굴 노출이므로
ByteTrack으로 상자를 트랙으로 묶고, 트랙의 관측 사이 빈 프레임은 선형 보간으로 채운다.
트랙의 마지막 관측 뒤에는 linger 프레임 동안 상자를 유지한다. 이때 상자는 최근 이동 속도만큼
조금씩 넓힌다. 다음 위치를 모르므로 양쪽으로 넓히는 편이 노출 위험이 적다.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise

import numpy as np
import supervision as sv

from .detector import Box

# ByteTrack은 점수의 절댓값으로 트랙 생성과 매칭을 거른다. 검출 임계값을 통과한 상자는 모두
# 추적 대상이므로, 순위는 유지하고 점수를 [TRACK_FLOOR, 1] 구간으로 올려서 넘긴다.
TRACK_FLOOR = 0.7
TRACK_ACTIVATION = 0.5


def _track_scores(scores: np.ndarray, conf: float) -> np.ndarray:
    """검출 점수를 추적용 점수로 옮긴다. 가림 대상 상자에는 영향을 주지 않는다."""
    t = np.clip((scores - conf) / max(1e-6, 1.0 - conf), 0.0, 1.0)
    return TRACK_FLOOR + (1.0 - TRACK_FLOOR) * t


def track(per_frame: list[list[Box]], fps: float, conf: float) -> dict[int, dict[int, tuple]]:
    """프레임별 검출을 순서대로 넣어 트랙별 관측 위치를 만든다.

    Returns:
        트랙 번호별 {프레임 번호: (x1, y1, x2, y2)}.
    """
    tracker = sv.ByteTrack(track_activation_threshold=TRACK_ACTIVATION,
                           lost_track_buffer=round(fps), frame_rate=round(fps))
    hist: dict[int, dict[int, tuple]] = defaultdict(dict)
    for i, raw in enumerate(per_frame):
        if raw:
            arr = np.array(raw, dtype=float)
            dets = sv.Detections(xyxy=arr[:, :4], confidence=_track_scores(arr[:, 4], conf),
                                 class_id=np.zeros(len(raw), dtype=int))
        else:
            dets = sv.Detections.empty()
        out = tracker.update_with_detections(dets)
        if out.tracker_id is None:
            continue
        for box, tid in zip(out.xyxy, out.tracker_id, strict=True):
            hist[int(tid)][i] = tuple(float(v) for v in box)
    return hist


def fill(frame_boxes: dict[int, list[tuple]], hist: dict[int, dict[int, tuple]],
         total: int, linger: int) -> int:
    """트랙의 빈 프레임과 마지막 관측 뒤 linger 구간에 상자를 더한다.

    Args:
        frame_boxes: 프레임별 가림 상자. 이 함수가 상자를 더한다.
        hist: `track`이 만든 트랙별 관측 위치.
        total: 영상의 프레임 수.
        linger: 마지막 관측 뒤 상자를 유지할 프레임 수.

    Returns:
        더한 상자 수.
    """
    added = 0
    for obs in hist.values():
        frames = sorted(obs)
        for a, b in pairwise(frames):
            for f in range(a + 1, b):
                t = (f - a) / (b - a)
                frame_boxes[f].append(tuple(
                    (1 - t) * va + t * vb for va, vb in zip(obs[a], obs[b], strict=True)))
                added += 1
        last = frames[-1]
        prev = frames[max(0, len(frames) - 6)]
        dt = max(1, last - prev)
        x1, y1, x2, y2 = obs[last]
        px1, py1, px2, py2 = obs[prev]
        vx = abs((x1 + x2) - (px1 + px2)) / 2 / dt
        vy = abs((y1 + y2) - (py1 + py2)) / 2 / dt
        for k in range(1, linger + 1):
            f = last + k
            if f >= total:
                break
            frame_boxes[f].append((x1 - vx * k, y1 - vy * k, x2 + vx * k, y2 + vy * k))
            added += 1
    return added
