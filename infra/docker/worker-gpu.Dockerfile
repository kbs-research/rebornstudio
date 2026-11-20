# worker-gpu 이미지. worker-jobs와 같은 잡 워커(인코딩·장면 분할·publish·비식별화)를 GPU 서버에서 돌린다.
# 소스와 잠금 파일은 worker-jobs.Dockerfile과 같다. 다른 것은 셋이다.
#   CUDA·cuDNN 런타임 위에 둔다 · ONNX Runtime을 같은 버전의 GPU 런타임으로 바꾼다 ·
#   NVENC(h264_nvenc)가 되는 배포판 ffmpeg를 쓴다.
# GPU가 없는 서버는 이 이미지 대신 worker-jobs 이미지를 쓴다(compose.gpu.yaml을 얹지 않는다).
#
# 베이스의 CUDA·cuDNN 버전은 ONNX Runtime GPU 런타임이 요구하는 버전(CUDA 12, cuDNN 9)에 맞춘다.
# ONNX Runtime을 올려 그 요구가 바뀌면 이 줄도 같이 올린다.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

# 컨테이너 툴킷이 드라이버 라이브러리를 넣는 범위. `video`가 없으면 NVENC 라이브러리가 들어오지 않아
# 인코딩이 libx264로 넘어간다. `compute`가 없으면 ONNX Runtime이 CUDA를 찾지 못해 CPU로 돈다.
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

# ffmpeg: 배포판 빌드에 NVENC가 들어 있다(실행 시점에 드라이버의 libnvidia-encode를 읽는다).
# 변환(reborn_transcode)과 비식별화가 시험 인코딩으로 NVENC가 되는지 보고 쓴다.
# 배포판 ffmpeg는 GPL 구성 요소(libx264 등)를 켠 빌드라 GPL-2.0 이상이다. 앱은 ffmpeg를 별도 프로세스로 부른다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv 버전은 다른 파이썬 Dockerfile과 같게 고정한다. 올릴 때는 넷(api·worker-io·worker-jobs·worker-gpu)을 같이 올린다.
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

# 베이스에 파이썬 3.11이 없어 uv가 받은 독립 실행 파이썬을 쓴다. 버전은 pyproject의 requires-python과 맞춘다.
ENV UV_PYTHON=3.11 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy
# 파이썬은 root로 먼저 받아 둔다. 실행 사용자는 그 인터프리터를 읽기만 하고 고쳐 쓰지 못한다.
RUN uv python install "$UV_PYTHON" && rm -rf /root/.cache/uv

# 비 root 사용자(uid·gid 10001)로 실행한다. 워커는 외부 영상을 ffmpeg로 열고 api는 인터넷 요청을 받으므로,
# 프로세스가 침해돼도 컨테이너 안 root 권한(패키지 설치·이미지 파일 변조)을 얻지 못하게 한다.
# 소스는 root 소유(읽기 전용)로 복사하고 `/app` 디렉터리와 `.venv`만 이 사용자 것으로 둔다. 침해된 프로세스가
# 앱 코드를 고쳐 쓰지 못한다. `uv run`은 기동마다 `.venv`를 점검하고 잠금 파일을 쓰므로 `.venv`는 이 사용자가
# 써야 한다. 그래서 `uv sync`도 이 사용자로 돌린다. 끝에서 `chown -R`을 하면 `.venv` 전체가 새 레이어로
# 한 번 더 복사된다. uv 캐시는 홈(`/home/app/.cache/uv`)에 둔다.
# GPU 장치 파일은 컨테이너 툴킷이 모든 사용자가 읽고 쓰도록 넣으므로 그룹을 더하지 않는다.
# 실행 중 쓰는 곳은 `/tmp`뿐이다(beat 일정 파일, 잡 임시 폴더). 스크래치 볼륨은 이미지의 `/tmp`(1777)를
# 처음 만들 때 복사하므로 따로 권한을 주지 않는다.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
    && install -d -o app -g app /app

WORKDIR /app
USER app
# 빌드 때만 켠다(ARG는 실행 환경에 남지 않는다). 실행 중 `uv run`이 다시 설치할 일이 생겨도
# 그때 전부 컴파일하느라 기동이 늦어지지 않는다.
ARG UV_COMPILE_BYTECODE=1

# uv 워크스페이스는 멤버 pyproject가 모두 있어야 파싱된다(worker-jobs.Dockerfile과 같은 제약).
COPY pyproject.toml uv.lock ./
COPY apps/api-gateway/pyproject.toml ./apps/api-gateway/
COPY workers/worker-io/pyproject.toml ./workers/worker-io/
COPY packages/reborn-contracts/ ./packages/reborn-contracts/
COPY packages/reborn-transcode/ ./packages/reborn-transcode/
COPY packages/deident/ ./packages/deident/
COPY workers/worker-jobs/pyproject.toml ./workers/worker-jobs/
# --locked: uv.lock이 pyproject와 어긋나면 잠금 파일을 고쳐 쓰지 않고 빌드를 멈춘다.
RUN uv sync --locked --package worker-jobs --no-install-project

COPY workers/worker-jobs/ ./workers/worker-jobs/
RUN uv sync --locked --package worker-jobs
# 바이트코드를 빌드에서 미리 만든다. 소스는 root 소유라 실행 사용자가 `__pycache__`를 쓰지 못해,
# 미리 만들지 않으면 프로세스마다 기동할 때 전부 다시 컴파일한다. 설치 패키지(`.venv`)는
# `UV_COMPILE_BYTECODE`로 `uv sync`가 만든다. 편집 설치인 앱 소스만 컴파일하고 `packages/`는 건드리지 않는다.
# 그 패키지는 `src` 폴더 시각이 uv 캐시 키라서 `__pycache__`를 만들면 `uv run`이 기동마다 낡은 설치로 보고 다시 빌드한다.
USER root
RUN /app/.venv/bin/python -m compileall -q workers
USER app

# 잠금 파일의 CPU 런타임을 같은 버전의 GPU 런타임으로 바꾼다. 두 패키지는 같은 모듈(onnxruntime)을 담아
# 함께 두면 나중에 설치한 쪽이 파일을 덮는다. 그래서 CPU 런타임을 지운 뒤 GPU 런타임을 넣는다.
# 이 환경은 잠금 파일과 다르므로 실행할 때 `uv run`을 쓰지 않는다. `uv run`은 잠금 파일대로
# 다시 맞추면서 CPU 런타임을 되돌린다(compose.yaml의 worker-gpu command가 venv의 celery를 직접 부른다).
RUN ORT_VERSION="$(uv pip show --python /app/.venv/bin/python onnxruntime | sed -n 's/^Version: //p')" \
    && test -n "$ORT_VERSION" \
    && uv pip uninstall --python /app/.venv/bin/python onnxruntime \
    && uv pip install --python /app/.venv/bin/python "onnxruntime-gpu==${ORT_VERSION}"

CMD ["/app/.venv/bin/celery", "-A", "worker_jobs.celery_app", "worker", "-l", "info", \
     "-Q", "q.transcode,q.deident", "--prefetch-multiplier", "1"]
