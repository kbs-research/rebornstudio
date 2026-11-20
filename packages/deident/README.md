# packages/deident

RebornStudio의 영상 얼굴 비식별화 모듈입니다. 영상 파일 하나를 받아 얼굴을 모자이크로 가린 MP4, 가림에 쓴 상자의
좌표 파일, 처리 결과 보고를 만듭니다. 저장소·큐·HTTP는 다루지 않고 로컬 파일만 처리합니다.

```python
from essential_deident import anonymize

report = anonymize("in.mp4", "out.mp4", "coords.json")
```

```bash
uv run python -m essential_deident in.mp4 out.mp4 --coords coords.json
```

## 처리 흐름

1. ffprobe로 프레임률, 오디오 유무, 길이를 읽습니다.
2. 영상을 처음 읽으면서 8장씩 묶어 얼굴을 찾습니다. 원본 크기와 2배 크기로 두 번 추론하고, 2배 결과는 40px 미만 상자만 합칩니다.
3. ByteTrack으로 상자를 트랙으로 묶어 검출이 빠진 프레임을 선형 보간으로 채우고, 마지막 관측 뒤 5프레임 동안 상자를 유지합니다.
4. 영상을 다시 읽으면서 상자에 여백 15%를 더해 모자이크로 가리고, ffmpeg로 H.264 인코딩과 원본 오디오(AAC) 결합을 합니다.
5. 두 번 읽은 프레임 수가 다르면 실패로 처리합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `detector.py` | YuNet 추론(ONNX Runtime, CUDA가 있으면 CUDA)과 결과 해석, 두 배율 합치기 |
| `tracking.py` | ByteTrack 추적, 빈 프레임 보간, linger |
| `render.py` | 여백과 모자이크 |
| `coordinates.py` | 좌표 파일(형식 버전 1) |
| `video.py` | ffprobe, 프레임 읽기, 인코딩(NVENC로 시험 인코딩이 되면 NVENC, 아니면 libx264. `ESSENTIAL_DEIDENT_ENCODER`로 고정할 수 있음) |
| `pipeline.py` | 전체 순서와 결과 보고 |
| `cpu.py` | 잡 하나의 CPU 스레드 수. cgroup CPU 상한을 동시 실행 수로 나누고 `DEIDENT_THREADS`로 덮어씁니다 |
| `models/yunet_2023mar_dynamic.onnx` | 입력 차원만 바꾼 YuNet 모델(MIT). `tools/make_dynamic_model.py`로 만듭니다 |

## 설계 이유

- 모델과 설정: 허용형 라이선스 모델 비교에서 고른 설정입니다. YOLO-FaceV2 검출기가 가린 얼굴의 약 87%를 가리고, 놓치는 얼굴은 주로 작은 군중 얼굴과 옆얼굴입니다. 놓친 얼굴은 검수의 "비식별화 미흡" 반려로 잡습니다.
- ONNX Runtime 사용: OpenCV의 `FaceDetectorYN`은 pip 배포판에 CUDA가 없어 GPU 서버에서도 CPU로만 돕니다. ONNX Runtime은 CUDA 실행과 묶음 추론이 됩니다. 결과 해석은 `FaceDetectorYN`과 같은 계산이며, 같은 결과를 내는지 `tests/test_detector.py`가 확인합니다.
- 스레드 수: ONNX Runtime 기본값은 연산 내부 스레드를 코어 수만큼 만들고 대기 중에도 회전합니다. 컨테이너의 코어 수는 호스트 값이라 잡 둘이 같이 돌면 CPU 상한의 몇 배 스레드가 생깁니다. 그래서 연산 내부 스레드는 `cpu.job_threads()`, 연산 사이 스레드는 1로 두고 회전을 끕니다. 결과 인코더(ffmpeg)도 같은 수를 쓰고 OpenCV 스레드는 1입니다.
- CPU 대체 경고: CUDA를 기대했는데(`ESSENTIAL_DEIDENT_DEVICE=cuda`, 또는 `auto`이고 CUDA 런타임이 있음) CPU로 돌면 경고 로그를 남기고 결과 보고의 `notices`에 `cpu-fallback`을 싣습니다. 결과는 정상이라 처리 시간 말고는 드러나지 않기 때문입니다. 실제 장치는 `device`에 있습니다.
- 결과 보고 필드: RebornStudio Pro와 호환되도록 Pro가 비식별화 이력에 남기는 필드(`model`·`masked`·`review_needed`·`review`·`detection_rate`·`coordinates`)와 이름을 맞춥니다. 얼굴을 하나도 찾지 못하면 `masked=false`, `review_needed=true`, `review=["no-detections"]`로 보고합니다.
- 좌표 파일: RebornStudio Pro 비식별화 서버의 좌표 파일 형식 버전 1과 같은 필드를 씁니다. 상자는 여백을 더한 결과 영상 픽셀 좌표이고, 상자가 있는 프레임만 싣습니다.
- 코드 출처: RebornStudio Pro 비식별화 서버(GPL-3.0)의 코드를 쓰지 않습니다. 같은 것은 좌표 파일 형식과 결과 보고 필드 이름뿐입니다.

## 측정값

| 항목 | 값 |
|---|---|
| 검출 결과 | 비교 표본 2,264장에서 YOLO-FaceV2 검출기 대비 가림률 87.0% |
| 처리 속도(Apple M4 CPU) | 검출 프레임당 약 75ms. 20초 720p 영상 처리에 49초 |

1시간 원본을 25분 안에 처리하려면 30fps 기준 프레임당 약 14ms가 필요합니다. CPU 속도로는 이 처리 시간을 맞추지 못합니다. GPU 서버에서는 아직 재지 않았습니다.

## 의존성

- 런타임: numpy, opencv-python-headless(전처리와 NMS), onnxruntime(GPU 서버는 `gpu` 추가 의존성의 onnxruntime-gpu), supervision 0.27(ByteTrack), ffmpeg·ffprobe 실행 파일
- supervision의 ByteTrack은 0.28부터 사용 중단 예고라 0.28 미만으로 묶었습니다.

## 남은 일

- GPU 서버에서 CUDA 추론과 NVENC 인코딩 속도를 재고 1시간 원본을 25분 안에 처리하는지 확인합니다.
- 인터레이스 영상과 OpenCV가 못 읽는 코덱(AV1 등)의 사전 변환은 아직 없습니다. RebornStudio는 원본 등록 때 변환 단계를 거치므로 그 결과를 입력으로 받는 것을 전제로 합니다.
- supervision 0.28 이후의 추적기로 옮길지 정해야 합니다.
