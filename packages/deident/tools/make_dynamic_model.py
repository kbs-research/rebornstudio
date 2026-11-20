"""OpenCV Zoo의 YuNet 2023mar ONNX를 입력 크기와 묶음 크기가 자유로운 모델로 바꾼다.

원본 모델은 입력이 1×3×640×640으로 고정되어 있다. 그래프의 Reshape가 -1을 쓰므로 입력 차원만
기호로 바꾸면 어떤 크기든 받는다.
이 저장소의 `models/yunet_2023mar_dynamic.onnx`는 이 도구로 만든다.

    uv run --with onnx python tools/make_dynamic_model.py <원본.onnx> <결과.onnx>

원본 주소:
https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
원본 SHA-256: 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
"""

import sys

import onnx


def main(src: str, dst: str) -> None:
    """입력의 N·H·W와 출력의 N·앵커 차원을 기호로 바꿔 저장한다."""
    model = onnx.load(src)
    dims = model.graph.input[0].type.tensor_type.shape.dim
    dims[0].dim_param, dims[2].dim_param, dims[3].dim_param = "N", "H", "W"
    for out in model.graph.output:
        od = out.type.tensor_type.shape.dim
        od[0].dim_param, od[1].dim_param = "N", "A"
    onnx.save(model, dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
