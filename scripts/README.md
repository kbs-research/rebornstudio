# scripts

## 목적

저장소 루트에서 실행하는 보조 스크립트입니다. 누가 언제 부르는지에 따라 세 폴더로 나눕니다.
웹 소스만 보는 검사는 여기에 없고 `apps/web/scripts/`에 있습니다(`check-copy`·`check-surface` 등).

## 주요 파일

| 폴더 | 부르는 쪽과 시점 | 내용 |
|---|---|---|
| [`ci/`](ci/README.md) | CI(`ci.yml`)와 `make verify`가 매번, 개발자가 PR 전에 | `check-*` 게이트와 도우미, `verify.sh` |
| [`dev/`](dev/README.md) | 개발자가 로컬에서 필요할 때 | 시드, 토큰, OpenAPI 생성 |
| [`ops/`](ops/README.md) | 운영자가 운영 DB·NCP·운영 서버에 한 번씩 | 백필, 측정, 점검, 저장소 업로드 |

## 설계 이유

### 호출하는 쪽에 따른 폴더 구분

폴더는 파일 이름이 아니라 호출하는 쪽을 기준으로 정합니다. 새 스크립트는 아래 순서로 판단합니다.

1. `ci.yml`이나 `ci/verify.sh`가 부르면 `ci/`에 둡니다. 게이트가 import하는 도우미도 `ci/`에 둡니다. 같은 폴더에 있어야 `sys.path`에 경로 하나만 넣으면 됩니다.
2. 로컬 DB와 로컬 화면에만 쓰고 운영에서 실행하면 안 되는 스크립트는 `dev/`에 둡니다.
3. 운영 데이터나 외부 서비스에 쓰거나, 운영 데이터를 재는 일회성 작업은 `ops/`에 둡니다.

판단이 엇갈리는 파일은 아래처럼 정했습니다.

| 파일 | 폴더 | 이유 |
|---|---|---|
| `list_error_codes.py` | `ci/` | `check-error-codes.py`가 import하는 추출기입니다. 사람도 사전을 고칠 때 직접 실행합니다 |
| `strip-comments.mjs` | `ci/` | `check-comment-only.py`가 부르는 도우미입니다 |
| `check-comment-only.py` | `ci/` | CI는 부르지 않지만, 주석만 고친 PR마다 사람이 실행하는 검사입니다 |
| `check-orphan-routes.mjs` | `ci/` | 사람이 읽고 판단하는 검사라서 CI에서 빠져 있습니다. 이유는 `verify.sh`의 `ci_exempt`에 있습니다 |
| `check-doc-prose.py` | `ci/` | `verify.sh`가 보고 모드로 실행합니다. 변경을 막지 않습니다 |
| `check-comment-style.py` · `list-comments.mjs` | `ci/` | CI가 네 규칙(지시어·강조기호·대시잇기·1인칭)을 `--strict`로 막고, `verify.sh`는 나머지 규칙을 보고 모드로 셉니다 |
| `check-event-labels.mjs` | `ci/` | 어디에도 등록되지 않은 반대 방향 검사입니다. 같은 이름의 `.py`와 나란히 두어 두 파일을 같이 열게 합니다 |
| `gen_openapi.py` | `dev/` | CI도 drift를 보려고 다시 실행하지만, 게이트가 아니라 생성기입니다. 사람은 `make openapi`로 부릅니다 |
| `seed_admin.py` | `dev/` | CI의 pytest 앞 단계와 `tests/conftest.py`도 부르지만, 하는 일은 개발·테스트 DB 시드입니다. 운영에서는 실행하지 않습니다 |
| `seed_demo.py` | `dev/` | 시연 준비용 시드입니다 |
| `measure_profanity_hits.py` | `ops/` | 운영 캡션으로 비속어 사전을 재는 측정 도구입니다. 게이트가 아닙니다 |

### 실행 위치와 경로

- 스크립트는 저장소 루트에서 실행합니다. `make` 타깃과 문서의 명령이 모두 저장소 루트를 기준으로 합니다.
- 파일 위치로 루트를 찾는 스크립트는 `Path(__file__).resolve().parents[2]`(JS는 `new URL("../..", import.meta.url)`)를 씁니다. 폴더 깊이를 바꾸면 이 값도 고칩니다.
- 서버 코드를 부르는 스크립트는 `uv run --package api-gateway python scripts/<폴더>/<파일>`로 실행합니다.

### 스크립트가 낡지 않게 막는 장치

- `apps/api-gateway/tests/contract/test_scripts_import.py`가 `ci`·`dev`·`ops`의 파이썬 파일을 모두 import합니다. 지워진 심볼을 부르는 스크립트는 이 테스트에서 실패합니다.
  루트에 파이썬 파일이 남거나 비어 있는 하위 폴더가 생겨도 실패합니다.
- `ci/verify.sh`의 자기 점검이 `ci.yml`이 부르는 게이트를 모두 찾아 로컬 실행 목록과 비교합니다.

## 의존성

- `apps/api-gateway`(서버 모델·서비스·설정), `docs/api/openapi.json`, `apps/web/src`(게이트가 읽습니다)
- 부르는 쪽: `.github/workflows/ci.yml`, 루트 `Makefile`, `apps/api-gateway/tests/conftest.py`(`seed_admin`)

## 남은 일

- 없음.
