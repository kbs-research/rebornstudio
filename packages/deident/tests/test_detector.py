"""ONNX Runtime 해석 코드가 OpenCV FaceDetectorYN과 같은 상자를 내는지 확인한다."""

import cv2
import numpy as np
from essential_deident.detector import DetectorConfig, FaceDetector, default_model_path, nms


def test_decode_matches_opencv_on_batch():
    """낮은 임계값의 잡음 이미지 묶음에서 두 구현의 상자가 하나씩 대응한다.

    얼굴 사진 없이도 임계값을 낮추면 후보 상자가 나오므로 해석 계산을 비교할 수 있다.
    묶음으로 돌려 이미지별로 자르는 계산도 같이 확인한다. 임계값을 0.02까지 낮추면 겹친 후보가
    너무 많아 NMS가 같은 점수의 상자 중 다른 것을 남기는 경우가 생기므로 0.05를 쓴다.
    """
    rng = np.random.default_rng(0)
    imgs = [rng.integers(0, 255, (360, 640, 3), dtype=np.uint8) for _ in range(3)]
    thr = 0.05
    det = FaceDetector(DetectorConfig(score=thr))
    ref = cv2.FaceDetectorYN.create(default_model_path(), "", (640, 360), thr, 0.3, 5000)
    total = 0
    for img, mine in zip(imgs, det._run(imgs), strict=True):
        mine = nms(mine, 0.3)
        _, faces = ref.detect(img)
        faces = [] if faces is None else faces
        assert len(mine) == len(faces)
        for f in faces:
            box = np.array([f[0], f[1], f[0] + f[2], f[1] + f[3]])
            assert any(np.abs(box - m[:4]).max() < 0.5 and abs(f[14] - m[4]) < 1e-3
                       for m in mine)
        total += len(faces)
    assert total > 0


def test_detect_batch_keeps_boxes_inside_frame():
    """원본 좌표로 돌린 상자는 프레임 밖으로 나가지 않는다."""
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    for x1, y1, x2, y2, _ in FaceDetector(DetectorConfig(score=0.02)).detect_batch([frame])[0]:
        assert 0 <= x1 <= x2 <= 640 and 0 <= y1 <= y2 <= 360
