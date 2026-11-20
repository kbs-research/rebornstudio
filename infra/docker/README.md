# docker

## 목적

서비스 이미지의 Dockerfile과 web nginx 설정을 둡니다. 모든 Dockerfile은 저장소 루트를 빌드 context로 씁니다.
운영 이미지는 CI(`images` 잡)가 빌드해 컨테이너 레지스트리에 `rebornstudio-<이름>:sha-<커밋 sha>`로 올리거나, 서버에서 `infra/deploy/build-images.sh`가 같은 이름으로 빌드합니다.

## 주요 파일

| 파일 | 이미지 | 내용 |
|---|---|---|
| `api.Dockerfile` | `rebornstudio-api` | Python 3.11 slim, uv, api-gateway. ffmpeg 없음. uvicorn을 `--proxy-headers`로 실행합니다. migrate 서비스도 이 이미지를 씁니다 |
| `worker-io.Dockerfile` | `rebornstudio-worker-io` | ffmpeg 포함(probe·썸네일). api-gateway 소스와 worker-io. 기본 명령은 beat를 포함한 단일 워커이고, compose가 서비스별 명령으로 덮어씁니다 |
| `worker-jobs.Dockerfile` | `rebornstudio-worker-jobs` | ffmpeg 포함(인코딩·씬 분할·비식별화). worker-jobs와 공용 패키지만 있고, api-gateway·worker-io는 `pyproject.toml`만 있습니다. GPU가 없는 서버의 worker-gpu가 씁니다 |
| `worker-gpu.Dockerfile` | `rebornstudio-worker-gpu` | worker-jobs와 같은 소스를 CUDA·cuDNN 런타임 위에 둡니다. ONNX Runtime을 같은 버전의 GPU 런타임으로 바꾸고, NVENC가 되는 배포판 ffmpeg를 씁니다. NVENC로 인코딩되면 변환과 비식별화가 NVENC를, 아니면 libx264를 씁니다. `GPU_ENABLED=true`일 때 `compose.gpu.yaml`이 고릅니다 |
| `web.Dockerfile` | `rebornstudio-web` | node 22에서 웹을 빌드하고 nginx 1.27 이미지에 정적 파일을 담습니다. nginx는 `nginx` 사용자(uid·gid 101)로 돕니다 |
| `web-nginx.conf` | — | web 이미지의 nginx 설정. `/healthz`, `/api/` 프리픽스 제거 프록시, `/assets/` 장기 캐시, SPA 폴백, 쿼리 문자열을 뺀 접근 로그 형식(`no_query`) |
| `web-nginx-headers.conf.template` | — | 보안 응답 헤더(nosniff·DENY·Referrer-Policy·Permissions-Policy·강제 CSP·위반 보고 끝점)의 템플릿. CSP의 저장소 출처 자리를 `web-csp.sh`가 채워 `/etc/nginx/generated/security-headers.conf`를 만듭니다 |
| `web-csp.sh` | — | web 컨테이너 시작 스크립트(`/docker-entrypoint.d/35-reborn-csp.sh`). `CSP_STORAGE_ORIGINS`를 검사해 헤더 템플릿에 넣습니다. 비면 NCP 객체 저장소 출처이고, 형식이 틀리면 실패로 끝나 컨테이너가 뜨지 않습니다. 이미지 빌드도 기본값으로 한 번 실행합니다 |
| `web-proxy-trust.sh` | — | web 컨테이너 시작 스크립트(`/docker-entrypoint.d/36-reborn-proxy-trust.sh`). `WEB_TRUSTED_PROXIES`를 검사해 믿는 앞단 설정(`set_real_ip_from`과 `$from_trusted_proxy`)을 만듭니다. 비면 아무도 믿지 않고, 형식이 틀리거나 `/0`이면 실패로 끝나 컨테이너가 뜨지 않습니다. 이미지 빌드도 빈 값으로 한 번 실행합니다 |
| `web-nginx-api-proxy.conf` | — | `/api` location들이 같이 쓰는 프록시 헤더와 버퍼링 설정. 이미지 안 경로는 `/etc/nginx/snippets/api-proxy.conf` |
| `pgbackup.Dockerfile` | `rebornstudio-pgbackup` | postgres 16 alpine에 aws-cli·redis-cli·bash를 더하고 `infra/backup/`의 스크립트를 담습니다. 기본 12시간 주기로 `backup.sh`를 실행합니다 |

```bash
docker build -f infra/docker/api.Dockerfile -t rebornstudio/api .
```

## 설계 이유

- 모든 이미지가 root가 아닌 고정 사용자로 돕니다. api·worker-io·worker-jobs·worker-gpu는 `app`(uid·gid 10001), web은 nginx 이미지의 `nginx`(uid·gid 101), pgbackup은 postgres 이미지의 `postgres`(uid·gid 70)입니다. 워커는 외부 영상을 ffmpeg로 열고 web과 api는 인터넷 요청을 받으므로, 프로세스가 침해돼도 컨테이너 안 root 권한(패키지 설치·이미지 파일 변조)을 얻지 못하게 합니다.
  - 파이썬 이미지는 소스를 root 소유로 복사하고 `/app` 디렉터리와 `.venv`, 홈(uv 캐시)만 `app`에게 줍니다. `uv run`이 기동마다 `.venv`를 점검하므로 `uv sync`도 `app`으로 돌립니다. 소스 옆 `__pycache__`를 쓰지 못하므로 바이트코드는 빌드에서 만듭니다(`UV_COMPILE_BYTECODE`와 `compileall`). `packages/`는 `src` 폴더 시각이 uv 캐시 키라 컴파일하지 않습니다. 만들면 `uv run`이 기동마다 다시 빌드합니다.
  - worker-gpu는 uv가 받는 파이썬을 root로 먼저 설치해 실행 사용자가 인터프리터를 고쳐 쓰지 못하게 합니다. GPU 장치 파일은 컨테이너 툴킷이 모든 사용자에게 열어 주므로 그룹을 더하지 않습니다.
  - 실행 중 쓰는 곳은 `/tmp`뿐입니다(beat 일정 파일 `/tmp/celerybeat-schedule`, 잡 임시 폴더 `/tmp/rb-*`, 덤프 임시 폴더). 스크래치 볼륨은 처음 만들 때 이미지의 `/tmp`(1777)를 복사하므로 따로 권한을 주지 않습니다.
  - web은 pid 파일을 `/tmp`에 쓰고, 임시 폴더(`/var/cache/nginx`)와 시작 스크립트가 만드는 설정 폴더(`/etc/nginx/generated`)만 `nginx`에게 줍니다. 80은 컨테이너 네트워크의 비특권 포트 시작값을 0으로 두어 엽니다(`compose.prod.yaml`의 `sysctls`). 포트를 바꾸지 않아 이전 이미지로 되돌려도 compose의 포트 연결이 맞습니다.
  - `tests/contract/test_container_users.py`가 각 Dockerfile의 마지막 `USER`와 사용자 생성 줄을 대조합니다.
- 의존성 설치와 소스 복사를 나눕니다. `pyproject.toml`과 `uv.lock`을 먼저 복사해 `uv sync --no-install-project`를 실행하고, 그다음 소스를 복사해 `uv sync`를 실행합니다.
  그래서 소스만 바뀌면 의존성 단계를 다시 빌드하지 않습니다.
- uv 이미지는 버전 태그(`ghcr.io/astral-sh/uv:0.12.22`)로 고정하고, `uv sync`에 `--locked`를 붙입니다.
  `latest`를 쓰면 같은 커밋도 빌드한 날에 따라 다른 버전의 uv가 잠금 파일을 읽습니다.
  `--locked`가 없으면 `uv.lock`이 `pyproject.toml`과 어긋날 때 빌드가 잠금 파일을 고쳐 쓰고, 검증하지 않은 버전이 이미지에 들어갑니다. uv를 올릴 때는 네 Dockerfile(api·worker-io·worker-jobs·worker-gpu)을 같이 올립니다.
- uv 워크스페이스 멤버를 더하면 api·worker-io·worker-jobs 세 Dockerfile에 그 `pyproject.toml` COPY를 같이 더합니다. 빠지면 `uv sync`가 워크스페이스를 읽지 못해 빌드가 실패합니다.
- ffmpeg는 워커 이미지 두 개에만 넣습니다. api는 요청 처리만 하고, 미디어 작업은 모두 워커에서 비동기로 실행합니다.
  두 워커 이미지는 같은 베이스와 같은 ffmpeg 패키지를 씁니다. 버전이 다르면 같은 영상을 워커마다 다르게 판정합니다.
- worker-jobs 이미지에는 api-gateway·worker-io 소스가 없습니다. 잡 코드가 그 패키지를 import하면 이미지 안에서 바로 실패합니다.
  이렇게 루트 `.importlinter`의 경계를 실행 환경에서도 한 번 더 지킵니다.
  반대로 api·worker-io 이미지에는 `packages/reborn-transcode`가 들어 있어, 서버 코드가 산출 검사 같은 그 패키지의 함수를 import합니다. ffprobe를 실행하는 probe는 ffmpeg가 있는 worker-io(`q.media`)에서 실행합니다.
- api는 신뢰하는 프록시가 보낸 `X-Forwarded-For`만 믿습니다. 신뢰 목록은 uvicorn 표준 환경변수 `FORWARDED_ALLOW_IPS`(IP·CIDR 쉼표 구분, 없으면 127.0.0.1)에서 읽습니다.
  운영 compose는 web과 api만 붙는 edge 네트워크의 서브넷(`EDGE_SUBNET`, 기본 172.31.250.0/28)을 기본값으로 줍니다. 이 설정이 없으면 web nginx 뒤의 모든 요청이 nginx 주소 하나에서 온 것으로 보여, 로그인 한도가 플랫폼 전체에 하나로 적용됩니다.
  도커 대역 전체(172.16.0.0/12)를 믿으면 같은 서버의 워커 컨테이너(외부 영상을 ffmpeg로 여는 곳)가 꾸민 헤더로 감사 기록의 출처와 https 여부를 바꿀 수 있습니다. `*`로 모두 믿으면 사용자도 같은 일을 할 수 있습니다.
  개발 compose는 포트가 루프백에만 열리고 앞단이 vite 하나라 도커 대역을 그대로 둡니다.
- 접근 로그는 `no_query` 형식으로 남깁니다. 요청 줄은 받은 경로의 `?` 앞까지, Referer도 `?` 앞까지만 씁니다. 같은 오리진의 Referer에는 앞 화면 주소의 쿼리가 그대로 실리므로 함께 자릅니다. `access_log`는 server 블록에 두어 이미지 기본 `nginx.conf`의 `main` 형식 로그를 상속하지 않게 합니다. 오류 로그(`error_log warn`)는 nginx가 요청 줄 전체를 남기므로 쿼리가 남을 수 있습니다.
- web nginx는 `X-Forwarded-For`를 받은 값에 덧붙이지 않고 `$remote_addr` 하나로 덮어씁니다. 앞단 없이 80을 여는 운영에서 덧붙이면 사용자가 보낸 값이 api에 그대로 닿습니다.
  앞단 TLS 프록시는 `WEB_TRUSTED_PROXIES`에 적습니다. 시작 스크립트(`web-proxy-trust.sh`)가 그 주소마다 `set_real_ip_from`을 만들어, 그 앞단에서 온 연결의 `$remote_addr`를 앞단이 넘긴 사용자 주소로 바꿉니다(`real_ip_recursive on`). 이미지를 다시 빌드하지 않고 `.env`만 바꿔 web을 다시 만들면 됩니다.
- `X-Forwarded-Proto`는 믿는 앞단(`$from_trusted_proxy`가 1)이 보낸 `http`·`https`만 넘기고, 그 밖에는 web nginx가 받은 scheme(http)을 넘깁니다(`web-nginx.conf`의 `map`). 사용자가 직접 보낸 값은 버립니다.
  `$scheme`만 넘기면 앞단에서 TLS를 끝내도 api가 늘 http로 보아 로그인 쿠키에 `Secure`가 붙지 않고 refresh의 출처 비교가 어긋납니다.
  api는 uvicorn `--proxy-headers`로 이 값을 요청 scheme에 반영하고(신뢰 프록시 `FORWARDED_ALLOW_IPS`에서 온 요청만), 그 scheme을 진단 표시, 저장소 CORS 점검의 주소, 로그인 쿠키의 `Secure`, refresh·logout의 출처 비교에 씁니다.
  `host`는 web 컨테이너의 기본 게이트웨이입니다. 같은 서버의 호스트 프록시가 `127.0.0.1:80`으로 넘기면 도커가 그 연결을 게이트웨이 주소에서 온 것으로 넘깁니다.
- nginx는 location에 `add_header`가 하나라도 있으면 server 수준의 `add_header`를 상속하지 않습니다. 그래서 보안 헤더를 파일 하나에 두고, server와 `add_header`를 쓰는 location이 모두 그 파일을 include합니다.
  location에 `add_header`를 더하면 그 location에도 `security-headers.conf` include를 더합니다. 빠지면 그 경로의 응답에만 보안 헤더가 없습니다.
- CSP는 강제 모드(`Content-Security-Policy`)입니다. 스크립트는 같은 출처의 번들만 받습니다(인라인 스크립트와 eval 없음). 스타일은 영상 플레이어가 style 속성 문자열을 쓰므로 인라인을 엽니다. 글꼴은 빌드가 CSS에 넣는 data: 글꼴을 엽니다. 영상·썸네일·첨부와 업로드는 저장소 presigned 주소를 쓰므로 img-src·media-src·connect-src에 저장소 출처만 엽니다. 저장소 엔드포인트가 http면 정책이 막으므로 https로 씁니다. 업로드 전 길이를 읽는 video 요소는 blob:을 씁니다. 외부 스크립트·프레임·플러그인은 없습니다(`object-src 'none'`, `frame-ancestors 'none'`). 정책은 `tests/contract/test_web_nginx_security.py`가 시작 스크립트로 만든 헤더 파일을 읽어 대조합니다.
- 저장소 출처는 배포 설정 `CSP_STORAGE_ORIGINS`(공백 구분 `https://호스트[:포트]`)로 받습니다. nginx 설정 파일은 환경변수를 읽지 못하므로 시작 스크립트가 템플릿을 채웁니다. 비면 NCP 객체 저장소(`https://kr.object.ncloudstorage.com`)입니다. 서버의 presigned 주소는 경로 방식이라 버킷 하위 도메인은 열지 않습니다.
  형식이 틀린 값은 기본값으로 바꾸지 않고 시작을 멈춥니다. 기본값으로 띄우면 다른 저장소를 쓰는 배포가 브라우저에서만 깨지고, 시작 실패는 `deploy.sh`의 web 헬스 확인이 바로 잡습니다. 세미콜론·따옴표·경로가 든 값은 정책에 다른 지시어를 끼워 넣을 수 있어 받지 않습니다.
- 위반 보고는 `report-uri /api/csp-report`와 `report-to csp`(`Reporting-Endpoints`)로 api의 수집 경로에 보냅니다. api는 막힌 주소의 출처와 화면 경로만 api 로그(로거 `reborn.csp`)에 남깁니다. `report-to`는 https 사이트에서만 동작하므로 http로 연 운영에서 Chromium 계열 브라우저는 `report-uri`로 보냅니다.
- MI가 주는 장면 썸네일 주소는 열지 않습니다. 분석 직후 클립 썸네일은 그 주소(1시간 만료)를 쓰는데, 호스트가 저장소 출처 밖이면 브라우저가 막고 화면은 빈 썸네일 자리를 그립니다. 썸네일 워커가 저장소에 프레임을 올리면 저장소 주소로 바뀝니다.
- 설정 문법은 이미지 없이 확인합니다. 이 폴더에서 `docker run --rm --add-host api:127.0.0.1 -e CSP_STORAGE_ORIGINS= -e WEB_TRUSTED_PROXIES=host -v $PWD/web-nginx.conf:/etc/nginx/conf.d/default.conf:ro -v $PWD/web-nginx-headers.conf.template:/etc/nginx/reborn/security-headers.conf.template:ro -v $PWD/web-csp.sh:/web-csp.sh:ro -v $PWD/web-proxy-trust.sh:/web-proxy-trust.sh:ro -v $PWD/web-nginx-api-proxy.conf:/etc/nginx/snippets/api-proxy.conf:ro nginx:1.27-alpine sh -c 'sh /web-csp.sh && sh /web-proxy-trust.sh && nginx -t'`를 실행합니다. `api` 업스트림은 시작할 때 이름을 풀기 때문에 `--add-host`가 필요합니다.
- HSTS는 넣지 않습니다. web은 TLS 없이 80만 열고, HSTS는 https 응답에서만 효과가 있습니다. TLS를 켜면 그때 더합니다.
- pgbackup 이미지는 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`를 설정합니다. aws-cli가 기본으로 켜는 무결성 체크섬을 NCP Object Storage가 거부하고, 그 오류는 `AccessDenied`로 나타납니다.

## 의존성

- 빌드 입력: 루트 `pyproject.toml`·`uv.lock`·`package.json`·`pnpm-workspace.yaml`·`pnpm-lock.yaml`, `apps/`·`packages/`·`workers/`, `infra/backup/`
- 부르는 쪽: `infra/compose/compose.dev.yaml`(개발 빌드), `.github/workflows/ci.yml`의 `images` 잡, `infra/deploy/build-images.sh`

## 남은 일

- 저장소 연결을 NCP 밖 엔드포인트로 더하면 그 출처를 `CSP_STORAGE_ORIGINS`에 사람이 더해야 합니다. 연결 목록에서 출처를 자동으로 모으지 않습니다.
