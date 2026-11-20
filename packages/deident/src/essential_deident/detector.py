"""YuNet 얼굴 검출기. ONNX Runtime으로 돌리고 GPU가 있으면 CUDA로 돌린다.

검출은 두 번 한다. 원본 크기로 모든 얼굴을 찾고, 2배로 키운 입력에서는 작은 얼굴만 받는다.
YuNet은 720p 원본에서 24px 미만 얼굴을 절반 가까이 놓친다. 2배 입력은 작은 얼굴을 잘 찾지만
넓은 영역을 큰 얼굴로 잘못 잡으므로, 2배 입력의 결과는 작은 상자만 합친다.
설정값은 허용형 라이선스 모델 비교에서 고른 값이다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib import resources

import cv2
import numpy as np

from .cpu import job_threads

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float, float]

STRIDES = (8, 16, 32)


@dataclass(frozen=True)
class DetectorConfig:
    """검출 설정. 값은 허용형 라이선스 모델 비교 실험에서 고른 설정이다."""

    score: float = 0.4
    nms: float = 0.3
    small_scale: float = 2.0
    small_max_h: float = 40.0
    batch_size: int = 8


def default_model_path() -> str:
    """저장소에 포함한 동적 입력 YuNet 모델의 경로를 돌려준다."""
    return str(resources.files("essential_deident") / "models" / "yunet_2023mar_dynamic.onnx")


def _providers() -> list[str]:
    """쓸 수 있는 실행 장치를 고른다. CUDA가 있으면 CUDA를 먼저 쓴다."""
    import onnxruntime as ort

    wanted = os.environ.get("ESSENTIAL_DEIDENT_DEVICE", "auto")
    available = ort.get_available_providers()
    if wanted == "cpu" or "CUDAExecutionProvider" not in available:
        return ["CPUExecutionProvider"]
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def _cuda_expected() -> bool:
    """CUDA로 돌 것으로 기대되면 참이다.

    설정이 `cuda`이거나, `auto`인데 런타임에 CUDA 실행 장치가 있으면 기대한다.

    CUDA 런타임이 있어도 드라이버·라이브러리가 맞지 않으면 ONNX Runtime은 오류 없이 CPU로 돈다.
    그러면 GPU 서버에서 처리 시간이 수십 배로 늘어도 결과는 정상이라 원인을 찾기 어렵다.
    """
    import onnxruntime as ort

    wanted = os.environ.get("ESSENTIAL_DEIDENT_DEVICE", "auto")
    return wanted == "cuda" or (
        wanted == "auto" and "CUDAExecutionProvider" in ort.get_available_providers())


def session_options(threads: int):
    """CPU 추론 스레드를 묶은 ONNX Runtime 세션 설정을 만든다.

    기본값은 연산 내부 스레드를 코어 수만큼 만들고 일이 없을 때도 스레드가 돌며 기다린다.
    워커 하나에서 잡 여럿이 돌면 그 스레드들이 CPU 상한을 넘어 서로의 시간을 빼앗는다.
    그래서 연산 내부 스레드는 `threads`, 연산 사이 스레드는 1로 두고 대기 중 회전을 끈다.
    """
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return opts


def _pad_to_stride(img: np.ndarray, stride: int = 32) -> np.ndarray:
    """오른쪽과 아래를 0으로 채워 가로세로를 stride의 배수로 맞춘다."""
    h, w = img.shape[:2]
    ph, pw = -(-h // stride) * stride, -(-w // stride) * stride
    if (ph, pw) == (h, w):
        return img
    out = np.zeros((ph, pw, 3), dtype=img.dtype)
    out[:h, :w] = img
    return out


def decode(outputs: dict[str, np.ndarray], n: int, height: int, width: int,
           score: float) -> list[np.ndarray]:
    """모델 출력을 이미지별 [x1, y1, x2, y2, score] 배열로 바꾼다.

    출력은 묶음 차원이 앵커 차원에 합쳐진 [1, N×앵커, C] 모양이다.
    이미지 순서대로 이어 붙어 있으므로
    stride별 앵커 수로 나눠 이미지별로 자른다. 점수는 sqrt(cls × obj)이고 상자는 격자 위치에서
    중심 오프셋과 로그 크기로 복원한다. OpenCV FaceDetectorYN과 같은 계산이다.

    Args:
        outputs: 출력 이름별 배열(`cls_8`·`obj_8`·`bbox_8` 등).
        n: 묶음 안의 이미지 수.
        height: stride 배수로 맞춘 입력 높이.
        width: stride 배수로 맞춘 입력 너비.
        score: 남길 최소 점수.

    Returns:
        이미지별 (K, 5) 배열 목록. 좌표는 모델 입력 픽셀 기준이다.
    """
    per_image: list[list[np.ndarray]] = [[] for _ in range(n)]
    for s in STRIDES:
        rows, cols = height // s, width // s
        cls = outputs[f"cls_{s}"].reshape(n, rows * cols)
        obj = outputs[f"obj_{s}"].reshape(n, rows * cols)
        bbox = outputs[f"bbox_{s}"].reshape(n, rows * cols, 4)
        conf = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        grid_r, grid_c = np.divmod(np.arange(rows * cols), cols)
        for i in range(n):
            keep = conf[i] >= score
            if not keep.any():
                continue
            b = bbox[i][keep]
            cx = (grid_c[keep] + b[:, 0]) * s
            cy = (grid_r[keep] + b[:, 1]) * s
            w = np.exp(b[:, 2]) * s
            h = np.exp(b[:, 3]) * s
            per_image[i].append(np.stack(
                [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, conf[i][keep]], axis=1))
    return [np.concatenate(p) if p else np.zeros((0, 5), np.float32) for p in per_image]


def nms(boxes: np.ndarray, thr: float) -> np.ndarray:
    """점수가 높은 상자부터 남기고 많이 겹친 상자를 지운다."""
    if len(boxes) == 0:
        return boxes
    rects = np.stack([boxes[:, 0], boxes[:, 1], boxes[:, 2] - boxes[:, 0],
                      boxes[:, 3] - boxes[:, 1]], axis=1).tolist()
    keep = cv2.dnn.NMSBoxes(rects, boxes[:, 4].tolist(), 0.0, thr)
    return boxes[np.array(keep, dtype=int).flatten()]


class FaceDetector:
    """프레임 묶음에서 얼굴 상자를 찾는다. 같은 영상의 프레임은 크기가 같다고 본다."""

    def __init__(self, config: DetectorConfig | None = None, model_path: str | None = None):
        import onnxruntime as ort

        self.config = config or DetectorConfig()
        # 전처리(크기 조정)는 프레임 묶음 단위라 OpenCV 내부 스레드가 이득보다 경합을 만든다.
        cv2.setNumThreads(1)
        self.session = ort.InferenceSession(model_path or default_model_path(),
                                            session_options(job_threads()),
                                            providers=_providers())
        self.output_names = [o.name for o in self.session.get_outputs()]
        #: CUDA를 기대했는데 CPU로 돌면 참이다. 결과 보고의 `notices`에 남긴다.
        self.cpu_fallback = self.device == "cpu" and _cuda_expected()
        if self.cpu_fallback:
            log.warning("CUDA로 추론할 것으로 기대했지만 CPU로 돕니다. "
                        "드라이버와 CUDA 라이브러리를 확인합니다(설정 %s)",
                        os.environ.get("ESSENTIAL_DEIDENT_DEVICE", "auto"))

    @property
    def device(self) -> str:
        """실제로 쓰는 실행 장치 이름."""
        return "cuda" if "CUDAExecutionProvider" in self.session.get_providers() else "cpu"

    def _run(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        """같은 크기 프레임 묶음을 한 번 추론해 이미지별 상자를 돌려준다."""
        padded = [_pad_to_stride(f) for f in frames]
        h, w = padded[0].shape[:2]
        blob = np.ascontiguousarray(np.stack(padded).transpose(0, 3, 1, 2), dtype=np.float32)
        raw = self.session.run(self.output_names, {"input": blob})
        out = dict(zip(self.output_names, raw, strict=True))
        return decode(out, len(frames), h, w, self.config.score)

    def detect_batch(self, frames: list[np.ndarray]) -> list[list[Box]]:
        """BGR 프레임 목록에서 프레임별 얼굴 상자를 찾는다.

        Returns:
            프레임별 [(x1, y1, x2, y2, score), ...]. 좌표는 원본 프레임 픽셀 기준이다.
        """
        cfg = self.config
        results: list[list[Box]] = []
        for start in range(0, len(frames), cfg.batch_size):
            chunk = frames[start:start + cfg.batch_size]
            base = self._run(chunk)
            k = cfg.small_scale
            big = [cv2.resize(f, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR) for f in chunk]
            small = self._run(big)
            for frame, b, sm in zip(chunk, base, small, strict=True):
                sm = sm.copy()
                sm[:, :4] /= k
                sm = sm[(sm[:, 3] - sm[:, 1]) < cfg.small_max_h]
                merged = nms(np.concatenate([b, sm]), cfg.nms)
                fh, fw = frame.shape[:2]
                merged[:, [0, 2]] = merged[:, [0, 2]].clip(0, fw)
                merged[:, [1, 3]] = merged[:, [1, 3]].clip(0, fh)
                results.append([tuple(float(v) for v in row) for row in merged])
        return results
