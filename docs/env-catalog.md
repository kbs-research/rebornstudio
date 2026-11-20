# 환경변수 사전

앱, 워커, 배포가 읽는 환경변수의 뜻과 표시를 이 문서 하나에 적습니다. 값을 채우는 판은 둘입니다.

| 판 | 파일 | 담는 것 |
|---|---|---|
| 개발(로컬) | 저장소 루트 `.env.example` → `.env` | 로컬 개발에서 실제로 채우는 키 |
| 운영(서버) | `infra/deploy/env.example` → `/opt/rebornstudio/.env` | 서버에서 채우는 키. prod compose가 `${VAR:?}`로 요구하는 키를 모두 담습니다 |

스크립트와 테스트만 쓰는 변수(`ADMIN_SEED_*`·`MAIU_LIVE_TEST` 등)는 각 파일의 docstring에 있습니다.
개발 판에 적어 쓰는 시드 키는 아래 «개발 시드» 절에 둡니다.

## 읽는 곳

config는 실행 위치와 무관하게 저장소 루트의 `.env`를 읽습니다. `REBORN_ENV_FILE`로 다른 파일을 가리킬 수 있고,
빈 값이면 파일을 읽지 않습니다. 테스트는 빈 값으로 로컬 `.env`를 막습니다. 환경변수는 언제나 `.env`보다 우선합니다.
이미지에는 `.env`가 들어가지 않습니다(`.dockerignore`). 운영 compose는 `env_file`이 아니라 `environment:`만 쓰므로
`x-app-env`에 없는 키는 서버 `.env`에 넣어도 컨테이너로 전달되지 않습니다.
DB를 쓰지 않는 작업 워커(worker-gpu)는 `x-app-env` 대신 `x-jobs-env`와 서비스별 `environment:`만 받습니다.
그래서 `DATABASE_URL`·`JWT_SECRET`·`JWT_REFRESH_SECRET`·`CREDENTIALS_KEY`가 작업 컨테이너에 없습니다. 작업 코드가 새 키를 읽으면 그쪽에 더합니다.

키 이름은 `config.Settings` 필드명을 대문자로 씁니다. prefix도 alias도 없습니다. 이름이 틀리면 pydantic이
`extra="ignore"`로 버리므로 오류 없이 기능만 동작하지 않습니다. 키를 더할 때는 `config.py`를 먼저 봅니다.

## 표시

| 표시 | 뜻 |
|---|---|
| `local` | 로컬 개발에서 채웁니다. 개발 판에 있어야 합니다. 비우면 그 기능이 동작하지 않습니다 |
| `prod 필수` | 서버 `.env`에서 비면 배포나 기동이 멈춥니다(`${VAR:?}` 또는 `_enforce_prod_secrets`). 운영 판에 있어야 합니다 |
| `prod` | 서버에서 값을 정합니다. 비우면 기본값을 씁니다 |
| `선택` | `config.py` 기본값으로 충분합니다. 기본값과 근거는 `config.py`에 있습니다 |
| `worker 전용` | `config.Settings` 밖에 있습니다. 워커가 `os.environ`으로 직접 읽습니다 |
| `os.environ` | `config.Settings` 밖에 있습니다. 앱 코드가 `os.environ`으로 직접 읽습니다 |
| `배포 전용` | 컨테이너로 전달되지 않습니다. prod compose 치환과 `deploy.sh`만 씁니다 |
| `uvicorn` | 앱이 아니라 uvicorn이 읽습니다 |

`scripts/ci/check-env-catalog.py`가 이 표를 `config.py`, prod compose, 두 판과 대조합니다. 표의 형식(`| \`KEY\` | 표시 | 뜻 |`)을
바꾸면 그 스크립트도 같이 고칩니다.

## 실행 환경

| 키 | 표시 | 뜻 |
|---|---|---|
| `ENVIRONMENT` | local · prod | local·test가 아니면 비밀값과 CORS를 검사합니다(`_enforce_prod_secrets`). 비우면 `prod`입니다. 운영은 `prod`이고, 로컬 개발은 개발 판의 `local`, 개발 compose는 `infra/compose/dev-defaults.env`의 `local`, 테스트는 conftest의 `test`가 값을 줍니다. 값 없이 호스트에서 스크립트를 돌리면 개발용 비밀값이 거절되어 시작하지 못합니다 |
| `LOG_LEVEL` | 선택 | 앱 로그 레벨. `logging_setup`은 같은 이름을 `os.environ`에서 직접 읽습니다 |

## DB · Redis

| 키 | 표시 | 뜻 |
|---|---|---|
| `DATABASE_URL` | local | 로컬 DB 주소. prod compose는 이 값을 받지 않고 `POSTGRES_PASSWORD`로 조립합니다 |
| `REDIS_URL` | local | 로컬 Redis 주소. prod compose는 이 값을 받지 않고 서비스 이름으로 정합니다 |
| `DB_POOL_SIZE` | 선택 · prod | api 한 대의 커넥션 풀. postgres `max_connections`와 함께 정합니다 |
| `DB_MAX_OVERFLOW` | 선택 · prod | 같은 풀의 초과 허용 수 |

## 오브젝트 스토리지 (NCP Object Storage)

주소, 버킷, 키는 env에 두지 않습니다. 자격증명 연결(서비스 NCP Object Storage, DB에 `CREDENTIALS_KEY`로 암호화)에
등록하고 프로젝트가 분석 연동에서 고릅니다. 로컬도 같습니다([`install.md`](install.md) «외부 서비스»).

| 키 | 표시 | 뜻 |
|---|---|---|
| `S3_PRESIGN_TTL_SECONDS` | 선택 | presigned URL 수명. 계정과 무관한 동작값입니다 |
| `S3_MAX_UPLOAD_BYTES` | 선택 | 업로드 크기 상한. 계정과 무관한 동작값입니다 |

## 인증 · 보안

`local · prod 필수` 시크릿이 local·test 밖에서 비었거나 `dev-`로 시작하거나 32자 미만이면 기동이 실패합니다.
만드는 명령: `openssl rand -hex 32`.

| 키 | 표시 | 뜻 |
|---|---|---|
| `JWT_SECRET` | local · prod 필수 | access 토큰 서명 키 |
| `JWT_REFRESH_SECRET` | local · prod 필수 | refresh 토큰 서명 키 |
| `CREDENTIALS_KEY` | prod 필수 | 연결 자격증명 암호화 키(Fernet). 이 값만 바꾸면 저장된 자격증명을 복호화할 수 없습니다. 바꿀 때는 이전 키를 `CREDENTIALS_KEY_PREVIOUS`로 옮기고 재암호화 명령을 돌립니다(`infra/deploy/README.md` «자격증명 암호화 키 교체»). 키에는 쉼표를 쓰지 않습니다. 비우면 `config.py`의 고정 기본값을 씁니다. 로컬도 처음 세팅할 때 각자 난수로 채웁니다(`.env.example` 안내) |
| `CREDENTIALS_KEY_PREVIOUS` | 선택 · prod | 키를 바꾸는 동안 복호에만 쓰는 이전 키 목록(쉼표 구분). 새 시크릿은 늘 `CREDENTIALS_KEY`로 암호화합니다. 재암호화(`python -m api_gateway.cli.rotate_credentials_key --apply`) 뒤 드라이런의 `previous`가 0이면 비웁니다. 평소에는 비어 있습니다 |
| `JWT_ACCESS_TTL_MINUTES` | local · prod | access 토큰 수명(분). 15분은 토큰을 탈취당했을 때 노출 시간을 줄이려고 정한 값입니다 |
| `JWT_REFRESH_TTL_DAYS` | local · prod | refresh 토큰 하나의 수명(일). 교체할 때마다 새로 세지만 `JWT_SESSION_MAX_DAYS`를 넘지 않습니다 |
| `JWT_SESSION_MAX_DAYS` | local · prod | 로그인 세션의 절대 수명(일, 1 이상). 로그인 시각부터 세고 refresh 교체로 늘어나지 않습니다. 지나면 다시 로그인합니다. 줄이면 이미 발급한 세션에도 바로 적용됩니다 |
| `CORS_ORIGINS` | local · prod | 별도 origin의 FE를 허용합니다. refresh·logout의 출처 확인도 이 목록과 서버 자신의 출처(요청 scheme과 `Host`)를 허용합니다. prod는 same-origin이 기본이라 빈 값이 정상입니다. 앞단 프록시가 `Host`를 바꿔 넘기면 사용자가 여는 주소(`https://…`)를 여기에 적습니다. local 밖에서 localhost를 넣으면 기동이 실패합니다 |
| `RATE_LIMIT_AUTH_PER_MIN` | 선택 · prod | 틀린 비밀번호·쓸 수 없는 재설정 링크의 IP별 분당 한도와 비밀번호 변경 실패의 사용자별 분당 한도. 기본 10입니다. 로그인·비밀번호 변경·재설정 링크 확인·재설정 확정이 버킷을 따로 씁니다. 성공은 세지 않으므로 한 사무실이 IP 하나를 같이 써도 정상 로그인은 서로의 한도를 쓰지 않습니다. 넘으면 그 IP의 맞는 비밀번호도 창(1분)이 끝날 때까지 429입니다. 계정 단위 무차별 대입은 (이메일, IP) 연속 5회·이메일 50회 실패 잠금이 따로 막습니다 |
| `RATE_LIMIT_REFRESH_PER_MIN` | 선택 · prod | 쿠키가 없거나 무효인 토큰 갱신의 IP별 분당 한도. 기본 300입니다. 유효한 쿠키의 갱신은 세지 않습니다 |
| `RATE_LIMIT_REFRESH_SESSION_PER_MIN` | 선택 · prod | 로그인 세션 하나의 토큰 갱신 분당 한도. 기본 60입니다. 탭들은 갱신을 한 번으로 합치므로 새로고침을 1초에 한 번씩 1분 동안 이어야 닿습니다 |
| `RATE_LIMIT_CSP_REPORT_PER_MIN` | 선택 · prod | CSP 위반 보고 수집(`POST /csp-report`)의 주소별 분당 한도. 기본 60입니다. 인증 없는 경로라 주소로 셉니다 |
| `MAX_REQUEST_BODY_BYTES` | 선택 | JSON 본문 크기 상한 |
| `CONNECTION_HOST_ALLOWLIST` | 선택 · prod | 연결 주소 검사가 막는 호스트 가운데 예외로 받을 호스트 이름이나 IP(쉼표 구분, 정확히 일치). 연결 주소는 http(s)만 받고 사용자 이름·비밀번호를 넣지 못하며, 루프백·링크 로컬(169.254.0.0/16)·메타데이터 호스트·`localhost`·점 없는 이름(compose 서비스 이름)을 막습니다. 사설 대역은 받습니다. 기본은 비어 있습니다 |
| `FORWARDED_ALLOW_IPS` | prod · uvicorn | api가 `X-Forwarded-For`·`X-Forwarded-Proto`를 믿을 프록시 대역. 이 대역에서 온 `X-Forwarded-Proto: https`가 있어야 로그인 쿠키에 `Secure`가 붙습니다. 운영은 비워 둡니다. 비우면 web과 api만 붙는 edge 네트워크의 서브넷(`EDGE_SUBNET`)이라, 워커 등 다른 컨테이너가 꾸민 헤더는 믿지 않습니다. 앞단 로드밸런서는 여기가 아니라 `WEB_TRUSTED_PROXIES`에 적습니다. `*`나 도커 대역 전체(172.16.0.0/12)로 넓히지 않습니다(`infra/docker/api.Dockerfile`) |

## MAIU (NCP Media Intelligence)

키, API 주소, 워크스페이스, 분석 프로젝트는 env에 두지 않습니다. 자격증명 연결(서비스 Media Intelligence)에 등록하고
프로젝트가 분석 연동에서 고릅니다.

| 키 | 표시 | 뜻 |
|---|---|---|
| `MAIU_ENABLED` | local · prod | `true`면 실제 MI(`NaverMaiuClient`), `false`면 가짜(`FakeMaiuClient`)가 응답합니다. 계정 값이 아니라 동작 모드라서 env에 남깁니다. 연결을 고르지 않은 프로젝트를 가짜로 돌리면 운영에서 가짜 결과가 납품될 수 있으므로 스위치 하나로 둡니다 |
| `ALLOW_FAKE_MAIU` | 선택 · prod | local·test 밖에서 `MAIU_ENABLED=false`(가짜 MI)로 기동할 때만 `true`로 줍니다. 기본 `false`면 `_enforce_prod_secrets`가 기동을 막습니다. 가짜 MI는 오류 없이 가짜 분석 결과를 돌려주므로 값이 빠진 운영이 조용히 가짜 결과를 납품으로 흘려보내는 것을 막습니다 |
| `MAIU_SCENE_RANGE` | local | 장면 길이(`EXTRA_SHORT`·`SHORT`·`MEDIUM`·`LONG`) |
| `MAIU_PERSON_COUNT` | local | 인물 분석 수(0~10, 0이면 생략) |
| `MAIU_HTTP_TIMEOUT` | local | MI 요청 타임아웃(초) |
| `MAIU_USE_INDEX_API` | 선택 | 신 MI 경로(`assets/{id}/index`) 사용 여부 |
| `MAIU_SWEEP_STALE_MINUTES` | 선택 | 진행 중 상태를 멈춘 것으로 보는 임계(분) |
| `MAIU_COST_PER_HOUR_KRW` | 선택 | 비용 예측 단가(원/시간) |

## 영상 규격 검사 · 처리

| 키 | 표시 | 뜻 |
|---|---|---|
| `PROBE_MAX_BYTES` | 선택 | MI에 보내기 전 ffprobe 규격 게이트의 파일 크기 상한 |
| `PROBE_MAX_DURATION_SECONDS` | 선택 | 같은 게이트의 길이 상한 |
| `PROBE_CODEC_WHITELIST` | 선택 | 같은 게이트의 허용 코덱 |
| `PROBE_CONTAINER_WHITELIST` | 선택 | 같은 게이트의 허용 컨테이너 |
| `PROBE_MIN_HEIGHT` | 선택 | 같은 게이트의 최소 높이 |
| `PROBE_MAX_HEIGHT` | 선택 | 같은 게이트의 최대 높이 |
| `PROBE_TIMEOUT_SECONDS` | 선택 | ffprobe 한 번의 시간 상한 |
| `CHECKSUM_BATCH` | 선택 | 원본 체크섬 백필의 회차당 개수 |
| `CHECKSUM_BUDGET_SECONDS` | 선택 | 같은 백필의 회차당 시간 예산 |
| `TRANSCODE_MAX_INFLIGHT` | 선택 · prod | 트랜스코딩 동시 상한(기본 2). 재생본·장면 분할·publish가 단계마다 따로 셉니다 |
| `TRANSCODE_THREADS` | 선택 · prod | 인코딩 1건의 스레드 수. `GPU_WORKER_CPUS ÷ GPU_WORKER_CONCURRENCY`. 워커도 `os.environ`에서 직접 읽습니다 |
| `DEIDENT_MAX_INFLIGHT` | 선택 · prod | 비식별화 동시 상한(기본 1). 모델이 워커 안에서 GPU 메모리를 쓰므로 GPU 한 장이면 1입니다 |

## 워커 전용

| 키 | 표시 | 뜻 |
|---|---|---|
| `WORKER_CONCURRENCY` | 선택 · 배포 전용 | worker(짧은 작업과 beat)의 동시 실행 수. 비우면 6 |
| `GPU_WORKER_CONCURRENCY` | 선택 · 배포 전용 · worker 전용 | worker-gpu(인코딩·비식별화)의 동시 실행 수. 비우면 2(인코딩 하나와 비식별화 하나). worker-gpu 컨테이너에도 넘겨 `DEIDENT_THREADS` 기본값의 분모로 씁니다 |
| `QC_CONCURRENCY` | 선택 · 배포 전용 | worker-qc(캡션 품질검수)의 동시 실행 수. 비우면 4. 산식은 `infra/compose/compose.yaml` |
| `DEIDENT_MODEL` | 선택 · worker 전용 | 비식별화 어댑터 이름. 비우면 `essential-yunet`. 검증용 `passthrough`는 가리지 않습니다 |
| `TRANSCODE_ENCODER` | 선택 · worker 전용 | 변환 영상 인코더. `nvenc`·`libx264`로 고정하고, 비우거나 `auto`면 NVENC로 시험 인코딩이 되는 서버에서만 NVENC를 씁니다. NVENC 인코딩이 NVENC·CUDA 오류(세션 한도·장치 없음·GPU 메모리 부족)로 실패할 때만 libx264로 다시 인코딩합니다 |
| `ESSENTIAL_DEIDENT_ENCODER` | 선택 · worker 전용 | 비식별화 결과 영상 인코더. 값의 뜻은 `TRANSCODE_ENCODER`와 같습니다 |
| `DEIDENT_THREADS` | 선택 · worker 전용 | 비식별화 잡 하나의 CPU 스레드 수(추론과 결과 인코딩). 비우면 컨테이너 CPU 상한 ÷ `GPU_WORKER_CONCURRENCY`(최소 1) |
| `OMP_NUM_THREADS` | 선택 · worker 전용 | OpenMP 스레드 수. worker-gpu는 1로 둡니다. 추론과 인코딩 스레드는 `DEIDENT_THREADS`·`TRANSCODE_THREADS`가 정합니다 |
| `FFMPEG_BIN` | 선택 · worker 전용 | ffmpeg 실행 파일 경로 |
| `FFPROBE_BIN` | 선택 · worker 전용 | ffprobe 실행 파일 경로 |
| `CELERY_TASK_ALWAYS_EAGER` | 선택 · worker 전용 | 1이면 Celery 태스크를 호출 프로세스 안에서 바로 돌립니다(워커 테스트용) |

## 배포 전용 (서버 `.env` → prod compose 치환)

| 키 | 표시 | 뜻 |
|---|---|---|
| `IMAGE_REGISTRY` | prod 필수 · 배포 전용 | NCP Container Registry 주소 |
| `IMAGE_TAG` | prod 필수 · 배포 전용 | `deploy.sh`가 커밋 sha로 넘깁니다. `.env`에는 적지 않습니다 |
| `POSTGRES_PASSWORD` | prod 필수 · 배포 전용 | postgres 비밀번호. prod compose가 이 값으로 `DATABASE_URL`을 조립합니다 |
| `GPU_WORKER_CPUS` | prod · 배포 전용 | worker-gpu의 CPU 상한. 산식은 `infra/compose/compose.prod.yaml` |
| `GPU_ENABLED` | 선택 · 배포 전용 | `true`이면 배포 스크립트가 `compose.gpu.yaml`을 얹어 worker-gpu가 GPU 이미지와 GPU 한 장을 씁니다. GPU와 NVIDIA Container Toolkit이 없는 서버에서는 비웁니다 |
| `PG_SHARED_BUFFERS` | prod · 배포 전용 | postgres shared_buffers |
| `REDIS_MAXMEMORY` | prod · 배포 전용 | Redis 메모리 상한 |
| `BACKUP_BUCKET` | prod 필수 · 배포 전용 | 백업 버킷. 산출물 버킷과 다른 버킷을 씁니다. 비면 배포가 시작되지 않습니다. 주기(12시간)·보관(30일)·키 접두어(`backups/postgres`)는 고정입니다. 개발은 값이 있을 때만 백업을 띄웁니다 |
| `BACKUP_ACCESS_KEY` | prod 필수 · 배포 전용 | 백업 버킷 키. 백업은 앱이 아니라 pg-backup 컨테이너가 DB 없이 실행하므로 자격증명 연결을 읽지 못합니다. 그래서 env로 받습니다. NCP 키가 env에 남는 유일한 곳이고 앱은 이 값을 읽지 않습니다 |
| `BACKUP_SECRET_KEY` | prod 필수 · 배포 전용 | 같은 키의 비밀 |
| `WEB_TRUSTED_PROXIES` | prod · 배포 전용 | web nginx가 `X-Forwarded-For`·`X-Forwarded-Proto`를 믿을 앞단 TLS 프록시. 공백이나 쉼표로 나눈 IP 주소·CIDR 목록이고, `host`는 도커 호스트(web 컨테이너의 기본 게이트웨이)입니다. 비우면 아무도 믿지 않아 api는 web에 연결한 주소를 사용자 주소로, web이 받은 scheme(http)을 요청 scheme으로 봅니다. 로드밸런서가 TLS를 끝내면 그 대역을, 같은 서버의 호스트 프록시가 끝내면 `host`를 적습니다. 빠뜨리면 https로 접속해도 로그인 쿠키에 `Secure`가 붙지 않고 토큰 갱신이 403 `origin_not_allowed`로 거절됩니다. 형식이 틀리거나 `/0`이면 web 컨테이너가 시작하지 않습니다(`infra/docker/web-proxy-trust.sh`) |
| `EDGE_SUBNET` | 선택 · 배포 전용 | web과 api만 붙는 운영 edge 네트워크의 서브넷. 기본 172.31.250.0/28이고 `FORWARDED_ALLOW_IPS` 기본값이 이 값을 따릅니다. 서버의 다른 도커 네트워크나 VPC 대역과 겹쳐 배포가 네트워크 생성에서 멈출 때만 적습니다 |
| `CSP_STORAGE_ORIGINS` | prod · 배포 전용 | 운영 web CSP의 `img-src`·`media-src`·`connect-src`에 여는 저장소 출처. 공백으로 나눈 `https://호스트[:포트]` 목록이고 호스트 맨 앞에만 `*.`를 쓸 수 있습니다. 비우면 NCP 객체 저장소(`https://kr.object.ncloudstorage.com`)입니다. 형식이 틀리면 web 컨테이너가 시작하지 않습니다(`infra/docker/web-csp.sh`). 다른 엔드포인트의 저장소 연결을 쓰면 그 출처를 더합니다 |

## 개발 시드 (`scripts/dev/seed_dev_clip.py`)

`make seed-dev`가 만드는 장면 영상은 저장소에 있는 실제 장면 클립을 가리킵니다. 시드는 환경변수를 먼저 보고,
없으면 저장소 루트 `.env`를 읽습니다. 버킷이나 클립 목록이 비면 장면 영상을 만들지 않고 나머지 시드만 넣습니다.

| 키 | 표시 | 뜻 |
|---|---|---|
| `SEED_SCENE_BUCKET` | local · os.environ | 장면 영상이 가리키는 장면 클립이 있는 버킷. 이 버킷을 설정에 적은 저장소 연결이 있어야 재생됩니다 |
| `SEED_SCENE_CLIPS` | local · os.environ | 장면 영상의 클립 목록. `장면:객체키`를 쉼표로 잇습니다. 키 파일명은 `…_{시작ms}_{끝ms}_raw.mp4` 형식입니다 |
| `SEED_MAIU_ARTIFACT_KEY` | local · os.environ | 장면 클립을 자른 원본의 MI 결과 JSON 키. `--storage-connection`으로 고른 연결의 버킷에서 읽습니다. 비면 클립 적재를 건너뜁니다 |

## 여기 없는 것

아래 이름은 읽는 코드가 없습니다. 넣어도 아무 일도 일어나지 않습니다. 표에 키로 적지 않습니다.

- `S3_ENDPOINT`·`S3_REGION`·`S3_BUCKET`·`S3_ACCESS_KEY`·`S3_SECRET_KEY`·`NCP_OBJECT_STORAGE_ENDPOINT`·`NCP_OBJECT_STORAGE_REGION`·
  `NCLOUD_ACCESS_KEY`·`NCLOUD_SECRET_KEY`·`MAIU_BASE_URL`·`MAIU_WORKSPACE_NAME`·`MAIU_PROJECT_ID`: NCP 계정 값입니다.
  자격증명 연결에 등록하고 프로젝트가 고릅니다. 연결의 주소, 리전, API 주소를 비우면 NCP 공용 값을 씁니다.
- `MAIU_DEFAULT_BUCKET`: 업로드 대상 버킷은 프로젝트가 고른 저장소 대상입니다.
- `CELERY_BROKER_URL`·`CELERY_RESULT_BACKEND`: 워커는 `REDIS_URL`만 봅니다(`worker_io/celery_app.py`).
- `HF_TOKEN`: 읽는 코드가 없습니다.
- `MAIU_WEBHOOK_TOKEN`·`MAIU_WEBHOOK_SECRET`·`MAIU_WEBHOOK_IP_ALLOWLIST`·`RATE_LIMIT_WEBHOOK_PER_MIN`: MI 웹훅을 받는
  라우트가 없습니다. 분석 결과는 분석 결과 회수 작업이 폴링으로 가져옵니다. 서버 `.env`에 남아 있어도 컨테이너로 전달되지 않습니다.
- `SLACK_WEBHOOK_URL`: Slack 발송 기능이 없습니다. 감사 이벤트로 대신합니다.
- `SMTP_HOST`: 메일 발송 기능이 없습니다. prod는 토큰을 응답에서 감추기만 합니다.
- `OTEL_EXPORTER_OTLP_ENDPOINT`: opentelemetry SDK와 collector가 없습니다.
