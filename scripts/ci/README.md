# scripts/ci

## 목적

CI(`.github/workflows/ci.yml`)와 `make verify`가 부르는 게이트, 그 도우미, 개발자가 PR 전에 실행하는 검사를 둡니다.
모두 저장소 루트에서 실행합니다. 웹 소스만 보는 검사는 `apps/web/scripts/`에 있습니다.

## 주요 파일

### 게이트(CI lint 잡이 실행하고, 실패하면 멈추는 검사)

| 파일 | 방향 | 잡아내는 것 |
|---|---|---|
| `check-error-codes.py` | 서버 → 화면 | 서버가 던지는 에러 코드가 화면 사전(`apps/web/src/shared/api/error_text.ts`)에 없는 경우. 없으면 화면이 일반 문구만 표시합니다. 화면에 닿지 않는 코드는 그 파일의 `EXPECTED`에 이유와 함께 적습니다 |
| `check-event-labels.py` | 서버 → 화면 | 서버가 남기는 `event_type` 리터럴이 화면의 `EVENTS` 표(`apps/web/src/features/audit/lib/event_label.ts`)에 없는 경우. 없으면 감사 로그가 원문(`caption_qc.held` 등)을 그대로 표시합니다 |
| `check-jsonb-merge.py` | 동시 쓰기 | `video.extra`·`clip.extra`를 파이썬에서 합쳐 통째로 다시 쓰는 코드. 읽고, 외부 호출을 하고, 다시 쓰는 사이에 다른 워커가 쓴 키가 사라집니다. 병합은 DB(`merge_extra`)가 합니다 |
| `check-dead-docstrings.py` | 문서 → 문법 | docstring 위치가 아닌 문자열. Python은 첫 문장일 때만 docstring으로 쓰고 나머지는 버립니다 |
| `check-env-catalog.py` | 코드 → 배포 | 환경변수 이름이 config `Settings`, 사전(`docs/env-catalog.md`), 운영 compose(`x-app-env`·`${VAR:?}`), 운영 예시(`infra/deploy/env.example`), 개발 예시(루트 `.env.example`)에서 맞지 않는 경우. 운영 compose는 `environment:`만 쓰므로 `x-app-env`에 없는 키는 서버 `.env`에 있어도 컨테이너에 전달되지 않습니다. 이름과 전달 여부만 보고 값은 보지 않습니다 |
| `check-retry-caps.py` | 재시도 | 재시도 상한을 둔 모듈에 일시 오류를 구분하는 코드가 없는 경우. 구분하지 않으면 네트워크 순단 몇 번에 영구 실패가 됩니다. 판정 기준은 `apps/api-gateway/api_gateway/services/transient.py`이고, 구분하는 표현이 있는지만 봅니다 |
| `check-missing-routes.mjs` | 화면 → 서버 | 화면이 부르는데 서버(`docs/api/openapi.json`)에 없는 경로. 그 화면은 404를 받고 "데이터 없음"처럼 동작합니다 |
| `check-request-bodies.mjs` | 화면 → 서버 | 화면이 본문 없이 보내는 변경 요청 중 서버가 본문을 요구하는 것. `api()` 옵션은 `RequestInit`이라서 `tsc`가 잡지 못하고, 서버는 422를 냅니다 |

### 사람이 읽고 판단하는 검사와 도우미

| 파일 | 역할 |
|---|---|
| `verify.sh` | CI `lint` 잡과 계약 drift(`--all`이면 pytest까지)를 로컬에서 같은 명령으로 실행합니다. `make verify`·`make verify-all` |
| `check-orphan-routes.mjs` | 서버에 있는데 화면이 부르지 않는 경로를 셉니다. 화면을 붙일지 경로를 제거할지는 사람이 정합니다. 부르지 않는 것이 정상이면 그 파일의 `EXPECTED`에 이유와 함께 적습니다. CI에서 빠져 있습니다 |
| `check-event-labels.mjs` | f-string으로 조립되는 `event_type`·`target_kind`까지 펼쳐 `EVENTS`·`TARGETS` 표와 대조합니다. CI와 `verify.sh` 어디에도 등록되어 있지 않습니다 |
| `check-comment-only.py` · `strip-comments.mjs` | 주석만 바뀐 변경인지 확인합니다. 코드(docstring을 뺀 AST, 주석을 뺀 TS·JS 출력)와 도구 지시 주석이 그대로인지, 주석 속 참조·식별자·수치가 빠지지 않았는지 봅니다. `python3 scripts/ci/check-comment-only.py [BASE]` |
| `check-comment-style.py` · `list-comments.mjs` | 주석 말투를 영역별·규칙별로 셉니다. 기본은 보고만 하고, 규칙 표본이 깨지면 종료 코드 2로 멈춥니다. CI는 지시어·강조기호·대시잇기·1인칭 네 규칙을 `--strict`로 막습니다 |
| `check-doc-prose.py` | 문서 산문에서 `문장 — 문장`으로 이어 붙인 곳을 셉니다. 보고만 합니다 |
| `check-versions.py` | 워크스페이스 pyproject.toml·package.json, api 앱 버전, `uv.lock`, `docs/api/openapi.json`의 버전 표기가 루트 `pyproject.toml`과 같은지 봅니다. 릴리스 때 버전을 올린 뒤 실행합니다. CI에 넣을지는 정하지 않았습니다 |
| `list_error_codes.py` | `raise HTTPException`을 AST로 모두 뽑습니다. 상태 코드를 스스로 정하는 하위 클래스(`InUseError`)는 `_FIXED_STATUS` 표로 함께 뽑습니다. grep은 여러 줄 raise, f-string, 변수 detail을 놓칩니다. `check-error-codes.py`가 import합니다 |

## 설계 이유

### 화면과 서버의 어긋남을 두 방향으로 따로 세는 이유

한 방향만 재면 반대쪽 어긋남이 쌓입니다. 경로는 `check-missing-routes`(화면에서 서버 방향)와 `check-orphan-routes`(서버에서 화면 방향)가 나눠 셉니다.
응답 모양(필드·타입)은 두 게이트 모두 보지 않습니다. 응답 모양은 `make contracts`가 만든 타입과 `tsc`가 잡습니다.
에러 코드는 서버에서 화면 방향만 셉니다. 추출기는 `raise` 위치의 문자열 리터럴만 보는데, 역할 검사 코드는 상수와 `require_*` 헬퍼를 거쳐 변수로 던져집니다.
그래서 반대 방향을 켜면 쓰이고 있는 키가 쓰이지 않는 것으로 나옵니다. 그 키를 지우면 화면이 일반 문구만 표시하게 됩니다.

`check-event-labels`는 확장자만 다른 파일 두 개입니다. `.py`는 서버 리터럴을, `.mjs`는 조립된 이름까지 봅니다. 두 파일의 방향이 이름에 드러나지 않으므로 고칠 때 두 파일을 같이 엽니다.

### 새 게이트는 한 번 일부러 실패시켜 본다

게이트가 통과했다는 사실만으로는 실제로 검사하고 있다고 보장할 수 없습니다. 경로를 정규화하는 순서가 틀리면 정상 경로를 모두 "없다"고 판정합니다.
마지막 조각만 비교하면 다른 경로의 호출을 같은 것으로 봅니다. 그래서 새 게이트는 사전에서 한 줄 빼기, 새 위반 넣기처럼 일부러 실패를 만들어 종료 코드 1이 나오는지 확인합니다.

### CI 검사를 로컬에서 먼저 실행한다

```
make verify          CI lint 잡 + 계약 drift (DB 불필요)
make verify-all      위 + pytest (private DB)
make hooks-install   푸시 전에 verify를 자동 실행 (건너뛰기: --no-verify)
```

CI 실행 한 번은 수 분이 걸립니다. 실패를 보고 고쳐 다시 푸시하면 그 왕복을 모두 기다려야 하므로 로컬에서 먼저 거릅니다.
Actions 실행 시간이 과금되는 저장소에서는 왕복마다 비용도 듭니다(잡 하나가 최소 1분으로 계산됩니다).
`verify.sh`는 `ci.yml`의 명령을 그대로 옮겼습니다. CI와 달리 끝까지 실행한 뒤 실패를 모아 알립니다. 두 파일이 다르면 로컬 결과를 믿을 수 없으므로 함께 고칩니다.

`verify.sh`의 자기 점검은 `ci.yml`의 `run:` 줄에서 `scripts/**/*.{py,mjs}` 게이트를 모두 뽑아 로컬 실행 목록과 비교합니다. 로컬에만 있고 CI에 없는 게이트도 실패로 봅니다.
일부러 CI 밖에 둔 검사는 `ci_exempt`에 이유와 함께 적습니다(`check-orphan-routes`·`check-utilities`·`check-doc-prose`).
vitest처럼 스크립트가 아닌 단계는 이 점검에 잡히지 않으므로 직접 맞춥니다.

`verify.sh`는 로컬 환경에서 생길 수 있는 문제 두 가지를 피해 갑니다. 호스트 npm 캐시에 root 소유 파일이 있으면 `npx`가 EACCES로 실패하므로 `npm_config_cache`를 다른 위치로 돌립니다.
`pnpm`이 PATH에 없으면 `apps/web/node_modules/.bin`의 설치본을 직접 부릅니다. CI 러너에서는 두 문제가 생기지 않습니다.

### 반복된 실수를 막는 장치

반복된 실수를 막는 규칙마다 지키는 장치가 있는지 적습니다. 새 규칙을 적을 때는 이 표에 게이트를 더할 수 있는지 먼저 봅니다.

| 규칙 | 지키는 장치 |
|---|---|
| 전 행이 같은 값인 열을 만들지 않는다 · 줄바꿈 유틸을 요소마다 붙이지 않는다 | `apps/web/scripts/check-false-signals.mjs` |
| 일시 오류를 영구 실패로 만들지 않는다 | `check-retry-caps.py` |
| 하드코딩 색 금지 | `apps/web/scripts/check-utilities.mjs` |
| 화면 문구는 제품 문구다 · 결과와 다음 할 일을 한 문장에 넣지 않는다(일부) | `apps/web/scripts/check-copy.mjs` |
| JSONB를 파이썬에서 합쳐 다시 쓰지 않는다 | `check-jsonb-merge.py` |
| 락 키는 DB 범위로 | `services/joblock.py` 안에 있어서 호출하는 쪽이 틀릴 수 없습니다 |
| 도메인 간 import는 허용 목록에 | `lint-imports`(CI) |
| 화면의 수는 분모를 밝힌다 · 없는 다음 단계를 지어내지 않는다 · 판정 규칙은 한 곳에만 · 비가역 액션의 근거를 같은 화면에 · 문자열 치환은 실패하게 · 한 건의 실패가 배치를 데려가지 않는다 · advisory lock은 전용 커넥션에서 · 워커는 DB에 붙지 않는다 | 사람(리뷰) |

사람이 맡는 규칙은 뜻을 봐야 판정할 수 있습니다. 수가 한 페이지분인지 전체인지는 그 수 옆에 쓰인 말로 정해집니다. 고정 문자열과 계산값은 타입이 같습니다.
두 구현이 같은 뜻인지는 이름도 언어도 달라서 기계가 비교하지 못합니다. 그래서 게이트가 없는 규칙은 리뷰에서 확인합니다.

## 의존성

- 파이썬 게이트는 표준 라이브러리만 씁니다(`python3`로 바로 실행). `list_error_codes.py`만 `uv run --package api-gateway`로도 실행됩니다.
- JS 게이트는 Node만 씁니다. `list-comments.mjs`·`strip-comments.mjs`는 호출하는 파이썬 검사가 `TS_PATH`로 넘긴 TypeScript 설치본을 씁니다.
- 읽는 것: `apps/api-gateway`, `apps/web/src`, `docs/api/openapi.json`, `docs/`, `infra/`, `.github/workflows/ci.yml`
- 부르는 쪽: `.github/workflows/ci.yml`의 `lint` 잡, `verify.sh`, 루트 `Makefile`(`verify`·`verify-all`·`hooks-install`)

## 남은 일

- `check-event-labels.mjs`를 CI에 넣을지, 이름으로 방향이 드러나게 바꿀지 정하지 않았습니다.
