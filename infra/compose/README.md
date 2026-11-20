# compose

## 목적

개발과 운영의 docker compose 형상입니다. 공통 파일 하나에 환경별 덮어쓰기 파일을 얹습니다.
오케스트레이터 없이 워크로드를 서비스(web, api, 워커 셋)로 나눕니다. 그래서 자원, 실패, 배포, 스케일을 서비스 단위로 분리할 수 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `compose.yaml` | 공통. 서비스 목록, 워커 소비 큐(`command`), 의존 순서, 헬스 검사 명령, 프로필 |
| `compose.dev.yaml` | 개발. 저장소에서 이미지 빌드, 호스트 포트, 루트 `.env`, 소스 마운트. 프로젝트 이름 `essential` |
| `compose.prod.yaml` | 운영. 레지스트리 이미지(`IMAGE_TAG`), 공통 env(`x-app-env`)와 잡 워커 env(`x-jobs-env`), 로그 회전, 재시작, 자원 상한. 프로젝트 이름 `rebornstudio-essential` |
| `dev-defaults.env` | 개발 compose가 루트 `.env`보다 먼저 읽는 기본값(`ENVIRONMENT=local`). 루트 `.env`에 같은 키가 있으면 그 값을 씁니다 |
| `compose.gpu.yaml` | GPU 서버. worker-gpu가 GPU 이미지(`rebornstudio-worker-gpu`)와 GPU 한 장을 씁니다. `.env`의 `GPU_ENABLED=true`일 때 배포 스크립트가 얹습니다 |

```
개발  docker compose -f compose.yaml -f compose.dev.yaml   (make dev-up)
운영  docker compose -f compose.yaml -f compose.prod.yaml  (infra/deploy/deploy.sh · dc.sh)
GPU   위 운영 조합 뒤에 -f compose.gpu.yaml (GPU_ENABLED=true일 때 배포 스크립트가 얹음)
```

뒤 파일이 앞 파일을 덮습니다. 매핑(`environment`·`healthcheck`·`depends_on`)은 키 단위로 합쳐지고, `command`처럼 목록 하나인 값은 통째로 바뀝니다.

### 서비스와 큐

| 서비스 | 소비 큐 | 이미지 | 성격 |
|---|---|---|---|
| `postgres` · `redis` | — | 공개 이미지 | 상태 저장. 운영에서는 호스트 포트를 열지 않습니다 |
| `web` | — | 개발 `node:22-slim`(vite dev), 운영 `rebornstudio-web`(nginx) | 운영에서 유일한 공개 포트(80). SPA 정적 파일과 `/api` 프록시 |
| `api` | — | api | FastAPI. 무상태이고 공유 상태는 Redis에 있습니다 |
| `migrate` | — | api | `alembic upgrade head` 한 번 실행(`ops` 프로필) |
| `worker` | `q.maiu` · `q.media` · `default` | worker-io | 짧은 작업. MI 폴링, 썸네일·probe, 회수·내보내기, DB를 쓰지 않는 작업의 투입·회신. beat를 품습니다(`-B`). 반드시 하나만 띄웁니다. `WORKER_CONCURRENCY`(기본 6) |
| `worker-qc` | `q.qc` | worker-io | 캡션 품질검수. 한 건이 모델 응답을 수십 초 기다립니다. `QC_CONCURRENCY`(기본 4) |
| `worker-gpu` | `q.transcode` · `q.deident` | worker-jobs | 긴 작업. 인코딩·재생본·씬 분할과 비식별화(모델 `DEIDENT_MODEL`, 비우면 `essential-yunet`). 프로필 없이 늘 뜹니다. `GPU_WORKER_CONCURRENCY`(기본 2) |
| `pg-backup` | — | pgbackup | Postgres 백업. 운영은 늘 뜨고 12시간마다 · 30일 보관입니다. 개발은 `backup` 프로필입니다 |

큐 라우팅은 `reborn_contracts.queues`의 `TASK_ROUTES`가 정합니다. 워커 `command`에서 큐를 빼면 그 큐의 태스크는 아무도 소비하지 않습니다.

### 프로필

| 프로필 | 서비스 | 켜는 방법 |
|---|---|---|
| `ops` | `migrate` | `run`으로만 부릅니다(`make dev-migrate`, `deploy.sh`) |
| `backup` | `pg-backup` | 개발만 씁니다. 루트 `.env`의 `BACKUP_BUCKET`에 값이 있으면 켭니다(`infra/deploy/profiles.sh`). 운영 `pg-backup`은 프로필 없이 늘 뜹니다 |

## 설계 이유

### 서비스 정의는 공통 파일 한 곳에 있다

두 파일이 같은 서비스를 따로 정의하면 개발과 운영의 워커 형상이 달라지고, 큐 분리를 개발에서 검증할 수 없습니다.
그래서 서비스 목록과 `command`는 `compose.yaml`에만 둡니다. 공통 파일에 두지 않는 것은 두 가지입니다.

- `${VAR:?}`: compose는 프로필과 무관하게 파일 전체를 보간합니다. 공통 파일에 두면 운영 비밀 하나가 개발 기동을 막습니다. 필수 값은 `compose.prod.yaml`이 요구합니다.
- 환경마다 값이 다른 것: 덮어쓰기 파일은 값을 더하거나 바꿀 수는 있지만 지울 수는 없습니다. 값을 지워야 하는 차이는 공통 파일에 두지 않고 각 환경 파일에 둡니다.

### 프로젝트 이름을 고정한다

덮어쓰기 파일이 `name:`으로 프로젝트 이름을 고정합니다(개발 `essential`, 운영 `rebornstudio-essential`). 볼륨 이름(`essential_pg-data`·`rebornstudio-essential_pg-data`)이 이 이름에서 나옵니다. 이름이 다른 compose 스택과는 같은 기계에 있어도 서로의 컨테이너와 볼륨을 덮지 않습니다.
그래서 파일 위치가 바뀌어도 기존 DB 볼륨을 쓰고, `-p`를 빠뜨린 명령이 빈 DB로 새 스택을 띄우지 않습니다.

### 워커는 실행 시간으로 셋으로 묶는다

서버 한 대에 워커 컨테이너를 큐마다 두면 컨테이너가 일곱 개가 됩니다. 실행 시간이 비슷한 큐끼리 묶어 셋으로 둡니다.

- `worker`: 초 단위 작업(MI 폴링, 썸네일·probe, 회수·내보내기·콜백)과 beat입니다. beat가 둘이면 주기 작업이 두 번 실행되므로 이 서비스는 레플리카를 늘리지 않습니다.
- `worker-qc`: 캡션 품질검수는 투입 상한이 없어(5초마다 최대 50건) 밀린 승인분이 한꺼번에 들어옵니다. 짧은 작업과 섞으면 회수·콜백이 그 뒤로 밀리므로 따로 둡니다.
- `worker-gpu`: 인코딩과 비식별화는 한 건이 수십 분입니다. 동시 실행 수는 인코딩 하나와 비식별화 하나가 같이 도는 2이고, 그 조합이 GPU 메모리의 최대 사용량을 정합니다. 디스패치 상한보다 실행 수가 적어 큐에서 기다리는 잡이 생기는데, 투입 리스(4시간)는 그 대기 시간까지 포함합니다.
- `worker-gpu`는 프로필 없이 늘 뜹니다. 비식별화를 켤지는 프로젝트 설정이 정하므로, 이 워커를 `.env`로 끄면 `q.deident` 소비자가 없어져 리스 만료와 재투입이 반복됩니다.
- 세 워커 모두 실행 슬롯이 빌 때만 다음 메시지를 받습니다(`--prefetch-multiplier 1`, `worker`는 `-O fair`도). 미리 받아 둔 메시지는 다른 슬롯이 비어도 그 자리에서 기다립니다.
- `worker-gpu`는 실행 프로세스를 20건마다 새로 만들고(`--max-tasks-per-child 20`), 시작할 때 6시간 넘은 잡 임시 폴더(`/tmp/rb-*`)를 지웁니다. 강제 종료된 잡의 원본과 산출이 스크래치 볼륨에 남기 때문입니다.
- 세 워커는 헬스 검사로 자기 노드 이름(`worker@`·`qc@`·`gpu@` + 컨테이너 호스트 이름)에 `celery inspect ping`을 보냅니다. 프로세스가 살아 있어도 브로커 연결이 끊기면 일을 받지 않기 때문입니다. `command`의 `-n`을 바꾸면 헬스 검사의 노드 이름도 같이 바꿉니다. 기동 유예는 300초입니다. 배포 스크립트가 헬스 검사가 있는 서비스의 healthy를 기다립니다.
- 운영 `web`은 `api`가 healthy가 된 뒤에 뜹니다. 먼저 뜨면 첫 요청이 502로 끝납니다.

### 운영은 서버 한 대에 모두 둔다

저장소와 분석이 NCP에 있으므로 컴퓨트와 Postgres·Redis도 같은 서버에 둡니다. 그래서 관리형 DB의 백업, PITR, 페일오버는 없습니다.
백업은 `infra/backup/`이 맡고, 페일오버는 없습니다. 규모가 커지면 상태 저장 서비스만 NCP Cloud DB로 옮기는 것이 첫 확장 방향입니다.

- 인코딩이나 CPU로 도는 비식별화가 모든 코어를 쓰면 Postgres 응답이 늦어져 API 전체가 느려집니다. 그래서 `worker-gpu`에 CPU 상한(`GPU_WORKER_CPUS`)을 둡니다.
- 상한 안에서도 잡마다 스레드 수를 묶습니다. 컨테이너 안의 코어 수는 호스트 값이라, 묶지 않으면 잡 둘이 각자 호스트 코어 수만큼 스레드를 만들어 문맥 전환으로 시간을 씁니다.
  인코딩은 `TRANSCODE_THREADS`(디코더·인코더·필터), 비식별화는 `DEIDENT_THREADS`(추론·결과 인코딩, 비우면 CPU 상한 ÷ `GPU_WORKER_CONCURRENCY`)를 쓰고, `OMP_NUM_THREADS=1`로 OpenMP 스레드를 막습니다.
- 인코딩 임시 파일은 전용 볼륨(`transcode-scratch`)에 씁니다. 디스크가 차면 Redis AOF 쓰기가 실패해 워커가 중단됩니다.
  필요한 크기는 대략 `(원본 + 산출) × 동시 실행 수`입니다.
- 컨테이너 로그는 `json-file` 드라이버의 크기 상한(50MB × 5)으로 회전합니다. 기본값은 무제한이라 디스크를 채웁니다.
- 컨테이너는 root로 돌지 않습니다(`infra/docker/README.md`). 운영 web은 비 root nginx가 80을 열도록 그 컨테이너 네트워크에만 `net.ipv4.ip_unprivileged_port_start=0`을 둡니다. 개발 compose는 같은 이미지를 빌드하고 소스를 마운트하므로, 마운트한 소스는 실행 사용자가 읽을 수만 있으면 됩니다.
- 앱 설정의 `ENVIRONMENT` 기본값은 `prod`입니다. 값이 빠진 배포도 비밀값 검사를 받습니다. 개발 compose는 `dev-defaults.env`로 `local`을 채우고, 운영 파일은 `${ENVIRONMENT:-prod}`로 넘깁니다.
- 운영 파일은 web과 api만 붙는 `edge` 네트워크(서브넷 `EDGE_SUBNET`, 기본 172.31.250.0/28)를 둡니다. web은 edge에만, api는 default와 edge에 붙고, api가 믿는 프록시 대역(`FORWARDED_ALLOW_IPS`)의 기본값이 이 서브넷입니다. 워커는 default에만 있어 api에 꾸민 `X-Forwarded-*`를 보내도 반영되지 않습니다.
- 운영 파일은 `env_file`이 아니라 `environment:`(`x-app-env`)만 씁니다. 서버 `.env`에 있어도 `x-app-env`에 없는 키는 컨테이너에 전달되지 않습니다.
  앱 설정의 필수 시크릿이 `x-app-env`에 있는지는 `scripts/ci/check-env-catalog.py`가 CI에서 검사합니다.
- DB를 쓰지 않는 잡 워커(worker-gpu)는 `x-app-env` 대신 `x-jobs-env`를 받습니다. 잡 코드가 읽는 키(`REDIS_URL`·`TRANSCODE_THREADS`·`DEIDENT_*`)만 넘기고,
  `DATABASE_URL`, JWT 시크릿, `CREDENTIALS_KEY`는 넘기지 않습니다. 잡 코드가 새 env를 읽게 되면 `x-jobs-env`나 그 서비스의 `environment:`에 더합니다.
- `DATABASE_URL`은 env로 받지 않고 `POSTGRES_PASSWORD`로 조립합니다. 따로 받으면 postgres 서비스의 비밀번호와 달라질 수 있습니다.
  그 어긋남은 기동 실패가 아니라 인증 실패로 나타나 원인을 찾기 어렵습니다.
- 이미지는 서버에서 빌드하지 않고 레지스트리에서 당깁니다. 태그는 `sha-<커밋 전체 sha>`이고 `latest`는 쓰지 않습니다. 롤백할 때는 이전 sha로 다시 배포합니다.

### 개발은 소스를 마운트한다

api와 워커는 `apps/api-gateway`·`workers/worker-io`(잡 워커는 `workers/worker-jobs`와 `packages/deident`)와 공용 패키지 `packages/reborn-contracts/python`·`packages/reborn-transcode`를 마운트합니다.
이미지 안의 워크스페이스 패키지는 editable 설치라서 마운트한 소스를 그대로 읽습니다.
공용 패키지를 마운트하지 않으면 계약을 바꾼 변경이 들어와도 api와 워커가 이미지를 만들 때의 계약으로 실행됩니다. 그러면 새 필드를 모르는 응답 모델이 500을 냅니다.

- api는 `--reload`로 바로 다시 뜨고 공용 패키지도 감시합니다. 도커 볼륨은 파일 이벤트가 오지 않는 경우가 있어 폴링 감시(`WATCHFILES_FORCE_POLLING`)를 켭니다.
- api 컨테이너를 새로 만들면 마운트한 패키지 재설치와 바이트코드 캐시 없는 import 때문에 첫 응답까지 1~2분이 걸립니다.
  헬스 검사에 기동 유예(`start_period`)를 두어, 기동 중의 실패로 unhealthy가 되어 web이 뜨지 않는 일을 막습니다.
- 워커는 Celery에 reload 기능이 없어서 재시작해야 변경이 반영됩니다.
- web은 소스를 마운트한 vite dev 서버입니다. `node_modules`는 익명 볼륨이라서 호스트 설치본(darwin 바이너리)이 컨테이너를 덮지 않습니다.
  `/api` 프록시 대상은 서비스 이름(`VITE_API_PROXY_TARGET: http://api:8000`)입니다.
- 호스트 포트를 5433·6380으로 둔 것은 호스트의 postgres(5432)·redis(6379)와 겹치지 않게 하기 위해서입니다. CI도 같은 `5433:5432` 매핑을 씁니다.
- 개발 호스트 포트(기본 5433·6380·8000·3000)는 모두 `127.0.0.1`에만 묶습니다. 주소 없이 쓰면 `0.0.0.0`에 열려 같은 망의 다른 PC가 개발 DB, Redis, API에 접속할 수 있습니다.
  바인딩을 바꾼 뒤에는 `make dev-up`으로 컨테이너를 다시 만들어야 새 바인딩이 적용됩니다. 재시작만으로는 옛 바인딩이 남습니다.

### 처음 띄울 때

새 체크아웃이나 볼륨을 지운 DB에서는 아래 순서로 띄웁니다.

```bash
make dev-up        # 컨테이너 기동. 마이그레이션은 돌리지 않습니다
make dev-migrate   # 스키마 생성(alembic upgrade head)
make seed-dev      # super_admin 시드(멱등)와 라벨링 워크벤치 시드
make dev-token     # 로그인 세션. 출력의 둘째 줄을 http://localhost:3000 콘솔에 붙여 넣으면 refresh 쿠키를 심고 새로고침합니다
```

- `make dev-migrate` 전에는 테이블이 없어서 워커와 beat가 `UndefinedTable`을 로그에 남기고 api 요청이 500으로 끝납니다.
  마이그레이션이 끝나면 다음 주기 작업부터 정상으로 돌고, 컨테이너를 다시 띄울 필요는 없습니다.
- `make seed-dev`에 `STORAGE_CONNECTION=<NCP 저장소 연결 이름>`을 주면 그 연결로 샘플 영상까지 올립니다.
  연결은 화면의 "자격증명 연결"에 먼저 등록합니다. 처음에는 연결 없이 실행해 관리자 계정부터 만듭니다.
- 화면은 refresh 토큰을 HttpOnly 쿠키로만 받고 access 토큰은 메모리에만 둡니다. 그래서 `make dev-token`의 출력은 `localStorage`에 넣는 값이 아니라 쿠키를 심는 콘솔 한 줄입니다. 콘솔을 연 주소가 api의 `CORS_ORIGINS`에 없으면 refresh가 403이고 로그인 화면으로 돌아갑니다. API 스크립트용 access 토큰은 `make dev-token ACCESS=1`로 받습니다.
- 포트를 바꿔 띄웠으면(`DEV_PG_PORT` 등) `make seed-dev`·`make dev-token`에도 같은 값을 줍니다. 두 명령은 호스트에서 그 포트로 DB에 접속합니다.
- 마이그레이션 파일이 늘었을 때도 `make dev-migrate`를 다시 실행합니다. 이미 적용한 단계는 건너뜁니다.

`make dev-up`이 마이그레이션을 함께 돌리지 않는 이유는 둘입니다.
운영도 `up`이 아니라 배포 스크립트(`infra/deploy/deploy.sh`)의 별도 단계에서 `migrate`를 실행합니다. `migrate` 서비스를 `ops` 프로필에 두어 `up`에 끼지 않게 한 것도 같은 이유입니다.
또 마이그레이션은 되돌리기 어렵습니다. 기능 브랜치 체크아웃에서 스택을 띄울 때마다 그 브랜치의 마이그레이션이 개발 DB에 자동으로 적용되면, 머지되지 않은 스키마가 DB 장부(`alembic_version`)에 남습니다.
그래서 적용은 명령으로 따로 실행합니다.

### 평소 명령

```bash
make dev-up        # 전체 기동. .env가 켜는 프로필도 같이 뜹니다
make dev-down      # 종료(볼륨 유지)
make dev-logs      # 로그
make dev-ps        # 상태
make dev-migrate   # 마이그레이션 한 번 실행(테스트 DB는 호스트 alembic으로 따로)

# 워커에 코드 변경 반영
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml \
  restart worker worker-qc worker-gpu

# pyproject.toml·uv.lock·Dockerfile을 바꿨을 때
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml up -d --build
```

| 접속 | |
|---|---|
| 웹 | http://localhost:3000 |
| API 문서 | http://localhost:8000/docs · `/healthz` · `/readyz` |
| PostgreSQL | `localhost:5433` (reborn / reborn / rebornstudio) |
| Redis | `localhost:6380` |

같은 PC에서 다른 스택이 이 포트를 쓰고 있으면 `make dev-up DEV_WEB_PORT=3480 DEV_API_PORT=8480 DEV_PG_PORT=5480 DEV_REDIS_PORT=6480`처럼 바꿔 띄웁니다. 값은 루트 `Makefile`이 환경변수로 넘기고 `compose.dev.yaml`이 읽습니다. 루트 `.env`는 compose 보간에 쓰이지 않으므로 거기에 적어도 바뀌지 않습니다.

## 의존성

- 이미지: `infra/docker/`의 Dockerfile(개발은 빌드하고, 운영은 `IMAGE_REGISTRY`에서 당깁니다)
- 설정: 개발은 루트 `.env`, 운영은 서버의 `/opt/rebornstudio/.env`(키 목록은 `infra/deploy/env.example`, 뜻은 `docs/env-catalog.md`)
- 부르는 쪽: 루트 `Makefile`, `infra/deploy/deploy.sh`·`dc.sh`

## 남은 일

- TLS 종료가 없습니다. web은 80 포트만 엽니다. TLS는 앞단(NCP 로드밸런서 또는 호스트 nginx)에서 종료합니다.
- 알림 수신처(Alertmanager)가 없습니다.
