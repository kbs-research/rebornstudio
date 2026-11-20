# worker-jobs 이미지. DB 없이 도는 잡 워커(트랜스코딩·씬 분할·publish·비식별화).
# api-gateway와 worker-io 소스를 넣지 않는다. 잡은 페이로드 하나로 일하고 결과를 큐로 회신한다.
FROM python:3.11-slim

# ffmpeg: reborn_transcode의 인코딩·씬 분할·비식별화 러너가 ffmpeg·ffprobe를 부른다.
# worker-io 이미지와 같은 패키지를 넣는다. 한쪽만 올리면 같은 잡이 이미지마다 다른 ffmpeg로 돈다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv 버전을 고정한다. latest를 쓰면 같은 커밋도 빌드한 날에 따라 다른 uv가 잠금 파일을 읽는다.
# 올릴 때는 네 Dockerfile(api·worker-io·worker-jobs·worker-gpu)을 같이 올린다.
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

# 비 root 사용자(uid·gid 10001)로 실행한다. 워커는 외부 영상을 ffmpeg로 열고 api는 인터넷 요청을 받으므로,
# 프로세스가 침해돼도 컨테이너 안 root 권한(패키지 설치·이미지 파일 변조)을 얻지 못하게 한다.
# 소스는 root 소유(읽기 전용)로 복사하고 `/app` 디렉터리와 `.venv`만 이 사용자 것으로 둔다. 침해된 프로세스가
# 앱 코드를 고쳐 쓰지 못한다. `uv run`은 기동마다 `.venv`를 점검하고 잠금 파일을 쓰므로 `.venv`는 이 사용자가
# 써야 한다. 그래서 `uv sync`도 이 사용자로 돌린다. 끝에서 `chown -R`을 하면 `.venv` 전체가 새 레이어로
# 한 번 더 복사된다. uv 캐시는 홈(`/home/app/.cache/uv`)에 둔다.
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

# uv 워크스페이스는 멤버 pyproject가 모두 있어야 파싱된다(api.Dockerfile과 같은 제약).
# worker-jobs가 실제로 import하는 reborn-contracts·reborn-transcode·deident만 소스까지 넣고,
# api-gateway·worker-io는 pyproject만 둔다. 소스가 없으므로 잡 코드가 그쪽을 import하면 이 이미지에서 바로 실패한다.
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
RUN python -m compileall -q workers
USER app

# 기본값은 q.transcode 하나를 prefetch 1로 소비한다. compose 밖에서 이미지 하나만 docker run할 때 쓴다.
# compose(infra/compose/compose.yaml)는 worker-gpu가 command로 큐를 정한다.
CMD ["uv", "run", "--package", "worker-jobs", "celery", "-A", "worker_jobs.celery_app", \
     "worker", "-l", "info", "-Q", "q.transcode", "--prefetch-multiplier", "1"]
