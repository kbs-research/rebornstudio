# worker-io 이미지. Celery 워커이고 미디어 워크로드(ffmpeg)를 API 이미지에서 분리한다.
FROM python:3.11-slim

# ffmpeg: run_pending_transcodes(reborn-transcode)가 쓰고, ffprobe 규격 게이트도 여기 붙는다.
# api 이미지에는 넣지 않아 API를 가볍게 유지한다(services/media/transcode.py가
# `from reborn_transcode import ...`를 함수 안에서 lazy import 하는 이유다).
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

# uv 워크스페이스는 멤버 pyproject가 모두 있어야 파싱된다(api.Dockerfile과 동일 제약).
# worker-io는 api-gateway에, api-gateway는 reborn-contracts·reborn-transcode에 워크스페이스로 의존하므로
# 그 소스까지 필요하다. api 이미지와 달리 워커는 transcode 코드를 실제로 실행한다.
COPY pyproject.toml uv.lock ./
COPY apps/api-gateway/ ./apps/api-gateway/
COPY packages/reborn-contracts/ ./packages/reborn-contracts/
COPY packages/reborn-transcode/ ./packages/reborn-transcode/
COPY packages/deident/pyproject.toml ./packages/deident/
COPY workers/worker-io/pyproject.toml ./workers/worker-io/
COPY workers/worker-jobs/pyproject.toml ./workers/worker-jobs/
# --locked: uv.lock이 pyproject와 어긋나면 잠금 파일을 고쳐 쓰지 않고 빌드를 멈춘다.
RUN uv sync --locked --package worker-io --no-install-project

COPY workers/worker-io/ ./workers/worker-io/
RUN uv sync --locked --package worker-io
# 바이트코드를 빌드에서 미리 만든다. 소스는 root 소유라 실행 사용자가 `__pycache__`를 쓰지 못해,
# 미리 만들지 않으면 프로세스마다 기동할 때 전부 다시 컴파일한다. 설치 패키지(`.venv`)는
# `UV_COMPILE_BYTECODE`로 `uv sync`가 만든다. 편집 설치인 앱 소스만 컴파일하고 `packages/`는 건드리지 않는다.
# 그 패키지는 `src` 폴더 시각이 uv 캐시 키라서 `__pycache__`를 만들면 `uv run`이 기동마다 낡은 설치로 보고 다시 빌드한다.
USER root
RUN python -m compileall -q apps workers
USER app

# -Q default,q.maiu,q.media,q.qc: celery_app.task_routes가 큐를 나눈다. 이 CMD는 compose 밖에서
#   이미지 하나만 docker run할 때 쓰는 기본값이다(단일 워커가 네 큐를 소비하고 beat를 함께 돌린다).
#   큐를 빼면 그 큐의 task가 소비되지 않는다(q.maiu를 빼면 5초 reconciler가 멈추고,
#   q.media를 빼면 썸네일·probe가, q.qc를 빼면 캡션 품질검수가 멈춘다).
#   q.transcode는 여기서 소비하지 않는다.
# compose(infra/compose/compose.yaml)는 개발·운영 모두 command를 덮어써 워커를 큐별로 나누고
#   beat는 `worker` 서비스 하나에만 둔다. beat가 둘이면 주기 작업이 두 번 실행된다.
CMD ["uv", "run", "--package", "worker-io", "celery", "-A", "worker_io.celery_app", \
     "worker", "-B", "-l", "info", "-Q", "default,q.maiu,q.media,q.qc"]
