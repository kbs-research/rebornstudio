# scripts/dev

## 목적

로컬 개발에서 부르는 스크립트입니다. 개발·테스트 DB에 시드를 넣고, 화면 검증용 토큰을 발급하고, OpenAPI 명세를 생성합니다.
운영 환경에서는 실행하지 않습니다.

## 주요 파일

| 파일 | 역할 | 실행 |
|---|---|---|
| `_local_only.py` | 시드 스크립트 세 개가 서버 모듈을 import하기 전에 부르는 확인(`refuse_unless_local`). `DATABASE_URL`의 호스트가 localhost·127.0.0.1·::1이 아니거나 `ENVIRONMENT`가 local·dev·test가 아니면 종료합니다. 거부 문구에는 호스트만 싣습니다 | 시드 스크립트가 부름 |
| `dev_token.py` | 로컬 화면 검증용 세션. 비밀번호 없이, DB에 있는 계정에 앱의 토큰 발급 함수로 만듭니다. 기본 출력은 브라우저 콘솔에 붙여 넣을 한 줄(로그아웃으로 이전 HttpOnly 쿠키를 지운 뒤 `reborn_refresh` 쿠키를 심고 새로고침)이고, `--access`(`ACCESS=1`)는 API 스크립트용 access 토큰 한 줄입니다. 역할별 화면은 그 역할의 계정으로 받습니다. `ENVIRONMENT`가 local·dev·test가 아니거나(값이 없으면 기본값 prod라서 거부) `DATABASE_URL`의 호스트가 로컬(`localhost`·`127.0.0.1`·`::1`)이 아니면 거부합니다. compose 서비스 이름 `postgres`는 `ENVIRONMENT`를 명시했을 때만 받습니다 | `make dev-token [EMAIL=…] [ACCESS=1]` |
| `gen_openapi.py` | FastAPI 앱의 OpenAPI 3.1 명세를 `docs/api/openapi.json`에 씁니다. 깨진 스키마 참조가 있으면 종료 코드 1로 끝납니다. CI의 drift 검사도 이 파일을 다시 실행합니다 | `make openapi` |
| `seed_admin.py` | 개발·테스트용 super_admin 시드(멱등). CI의 pytest 앞 단계와 `apps/api-gateway/tests/conftest.py`도 부릅니다. `ENVIRONMENT`가 local·test가 아니면 거부합니다. 동의 시각도 채워서 업무 API가 `privacy_consent_required`로 막히지 않게 합니다 | `make seed-dev` |
| `seed_dev_clip.py` | 라벨링 워크벤치용 프로젝트·영상·클립·작업을 기본 조직에 넣습니다. `--storage-connection <이름>`을 주면 그 NCP 저장소 연결로 샘플 영상과 MI 결과를 읽고, 그 연결을 시드 프로젝트의 저장소 대상으로 기록합니다. 주지 않으면 저장소를 건드리지 않습니다. 실제 장면 클립을 가리키는 장면 영상은 `SEED_SCENE_*` 값이 있을 때만 만듭니다 | `make seed-dev [STORAGE_CONNECTION=이름]` |
| `seed_dev_volume.py` | 단계 화면(반입~검수)을 운영 밀도로 채웁니다(영상 43, 클립과 작업 수백 건). `dev_volume` 표식이 있는 데이터만 지우고 다시 넣으므로 멱등입니다. 대상은 slug가 `default`인 프로젝트이고, 마이그레이션이 만들지 않으므로 화면에서 먼저 만듭니다. 대상 프로젝트가 고른 저장소 대상을 영상에 기록합니다 | `make seed-volume` / `make seed-volume-clear` |
| `seed_demo.py` | MI 분석을 이미 통과한 프로젝트에 시연용 멤버와 검수 대기 물량을 서비스 계층으로 만듭니다. 가짜 데이터를 만들지 않습니다 | `uv run --package api-gateway python scripts/dev/seed_demo.py [--status\|--clear]` |

## 설계 이유

- super_admin 시드는 마이그레이션이 아니라 스크립트로 둡니다. 마이그레이션은 모든 환경에서 실행됩니다.
  그래서 마이그레이션에 두면 저장소에 적힌 비밀번호를 쓰는 super_admin이 운영 서버에도 생깁니다.
- 토큰 발급에는 비밀번호 경로를 쓰지 않습니다. 개발 비밀번호라도 스크립트나 셸 히스토리에 남기지 않습니다.
  이 도구는 인증을 우회하므로, 운영 자격증명이 로드된 셸에서 실행하면 운영 super_admin 토큰이 터미널에 출력됩니다. 그래서 환경을 확인하고 거부합니다.
- 시드 비밀번호는 env로 받습니다. 없으면 무작위 값을 만들고 출력하지 않습니다. 로그인은 `make dev-token`으로 합니다.
- 시드 스크립트 세 개(`seed_dev_clip.py`·`seed_dev_volume.py`·`seed_demo.py`)는 같은 로컬 확인(`_local_only.py`)을 부릅니다.
  `os.environ.setdefault`는 셸에 export된 운영 `DATABASE_URL`을 덮지 않습니다. 그래서 확인이 없으면 시드가 운영 계정의 비밀번호를 바꾸고 작업을 제출·승인합니다.
  호스트는 URL을 나눠 비교합니다. 문자열 포함 여부로 비교하면 DB 이름에 `localhost`가 들어간 원격 주소도 통과합니다.
- 세 시드 스크립트는 목적이 다릅니다. `seed_dev_clip.py`는 워크벤치 한 건을 정밀하게 만들고, `seed_dev_volume.py`는 목록 화면의 밀도를 만듭니다. `seed_demo.py`는 실제로 분석된 영상 위에 시연 상태를 만듭니다.
- 분석 상태는 화면 판정(`services/media/video/classify.py`)이 읽는 `analysis_job`에 영상 단위 MI 행으로 씁니다.
  진행 중 표본은 가짜 에셋 번호를 쓰므로, `payload.retry_after`를 먼 미래로 두어 분석 결과 회수 작업이 처리하지 않게 합니다. 회수 작업이 처리하면 실제 MI 조회가 실패해 표본이 실패 상태로 넘어갑니다.

## 의존성

- `apps/api-gateway`(모델·서비스·설정). 서버 코드를 부르는 스크립트는 `uv run --package api-gateway`로 실행합니다.
- `tests/conftest.py`는 이 폴더를 `sys.path`에 넣고 `seed_admin.py`를 import합니다. 파일을 옮기면 conftest도 고칩니다.
- `.env`는 저장소 루트의 파일을 읽습니다(`config.py`).

| 환경변수 | 쓰는 스크립트 | 설명 |
|---|---|---|
| `DATABASE_URL` | 시드 전부 · `dev_token.py` | 기본 `postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio` |
| `ADMIN_SEED_EMAIL` · `ADMIN_SEED_PASSWORD` | `seed_admin.py` | 이메일 기본값은 `admin@example.com`. 비밀번호가 없으면 새 계정에만 무작위 값을 주고(출력하지 않음), 이미 있는 계정의 비밀번호는 그대로 둡니다 |
| `SEED_DEMO_PASSWORD` | `seed_demo.py` · `seed_dev_clip.py` | 데모 계정 비밀번호. 없으면 무작위 값을 쓰고 출력하지 않습니다 |
| `SEED_SCENE_BUCKET` · `SEED_SCENE_CLIPS` · `SEED_MAIU_ARTIFACT_KEY` | `seed_dev_clip.py` | 장면 영상이 가리키는 장면 클립의 버킷·`장면:객체키` 목록·MI 결과 JSON 키. 환경변수가 없으면 루트 `.env`를 읽습니다. 버킷이나 목록이 비어 있으면 검토 대기 영상을 건너뜁니다. 형식은 `docs/env-catalog.md`에 있습니다 |

## 남은 일

- 없음.
