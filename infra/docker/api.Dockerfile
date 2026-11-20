FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
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

# 의존성을 먼저 복사해 캐시를 살린다. uv 워크스페이스(루트 [tool.uv.workspace]) 멤버 pyproject가
# 모두 있어야 sync가 워크스페이스를 파싱한다. api-gateway가 import하는 워크스페이스 패키지는
# 소스까지 복사한다. pyproject만 두면 sync와 이미지 빌드는 통과하지만 실행 중 import에서 실패하고,
# 빌드가 성공하므로 이미지 게이트가 잡지 못한다.
COPY pyproject.toml uv.lock ./
COPY apps/api-gateway/pyproject.toml ./apps/api-gateway/
COPY packages/reborn-contracts/ ./packages/reborn-contracts/
COPY packages/reborn-transcode/ ./packages/reborn-transcode/
COPY packages/deident/pyproject.toml ./packages/deident/
COPY workers/worker-io/pyproject.toml ./workers/worker-io/
COPY workers/worker-jobs/pyproject.toml ./workers/worker-jobs/
# --locked: uv.lock이 pyproject와 어긋나면 잠금 파일을 고쳐 쓰지 않고 빌드를 멈춘다.
RUN uv sync --locked --package api-gateway --no-install-project

# 소스 코드
COPY apps/api-gateway/ ./apps/api-gateway/
RUN uv sync --locked --package api-gateway
# 바이트코드를 빌드에서 미리 만든다. 소스는 root 소유라 실행 사용자가 `__pycache__`를 쓰지 못해,
# 미리 만들지 않으면 프로세스마다 기동할 때 전부 다시 컴파일한다. 설치 패키지(`.venv`)는
# `UV_COMPILE_BYTECODE`로 `uv sync`가 만든다. 편집 설치인 앱 소스만 컴파일하고 `packages/`는 건드리지 않는다.
# 그 패키지는 `src` 폴더 시각이 uv 캐시 키라서 `__pycache__`를 만들면 `uv run`이 기동마다 낡은 설치로 보고 다시 빌드한다.
USER root
RUN python -m compileall -q apps
USER app

EXPOSE 8000
# 프록시 신뢰. `--proxy-headers`는 신뢰하는 프록시에서 온 요청만
# `X-Forwarded-For`를 읽어 클라이언트 주소를 바꾼다. 신뢰 목록은 uvicorn 표준 환경변수
# `FORWARDED_ALLOW_IPS`(쉼표 구분 IP·CIDR)에서 오고, 없으면 127.0.0.1뿐이다. 그러면 앞단
# nginx(web 컨테이너) 뒤에서 모든 요청이 nginx 주소 하나로 보여 로그인 한도가 플랫폼 전체를
# 하나로 센다. 값은 배포 형상이 정한다(`infra/compose/compose.{dev,prod}.yaml`).
# `*`(아무나 신뢰)는 쓰지 않는다. 헤더 하나로 한도를 비켜 가고 감사 출처를 지어낼 수 있다.
CMD ["uv", "run", "--package", "api-gateway", "uvicorn", \
     "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
