# reborn-contracts

## 목적

서버와 화면이 같은 모양의 데이터를 쓰도록 계약을 한 곳에 둡니다. 계약은 네 종류입니다.

- API 요청·응답 스키마(Pydantic v2, `python/`)
- 그 스키마에서 생성한 TypeScript 타입(`typescript/`)
- 워커 태스크 이름·큐 표(`queues.py`와 `queues.md`)
- 화면과 서버가 같은 입력에 같은 답을 내는지 확인하는 판정 표(JSON 네 개)

## 주요 파일

| 파일 | 역할 |
|---|---|
| [`python/`](python/README.md) | Pydantic 스키마 패키지 `reborn_contracts`. 서버가 import하고 OpenAPI 명세의 원본이 됩니다 |
| [`typescript/`](typescript/README.md) | `@reborn/contracts`. `docs/api/openapi.json`에서 생성한 타입(`src/api.ts`) |
| [`queues.md`](queues.md) | 큐·태스크 계약. 큐별 특성, 태스크 대장, DB를 쓰지 않는 작업의 페이로드 스키마, 멱등 근거 |
| `caption-gate.json` | 캡션 제출 게이트 판정 표. 문자열의 토큰 수와 payload의 최종 캡션 문자열 |
| `caption-shapes.json` | 캡션 읽기 규칙 판정 표. payload에서 문장 목록과 최종 문자열을 꺼내는 방법, 문장 분리 규칙 |
| `connection-address.json` | 연결 수정의 시크릿 재입력 판정 표. 연결 종류별 주소 키와, 주소를 바꾸는 수정이 시크릿을 다시 받아야 하는 사례 |
| `labeler-rates.json` | 사람별 실적 표의 승인율·반려율 판정 표. 제출·승인·반려 수에서 서버가 내는 비율과 분모, 그 행에서 화면이 그리는 값과 괄호 |

## 설계 이유

### 스키마는 Python이 원본이다

FastAPI가 `reborn_contracts` 모델을 import하면 그 모델이 OpenAPI 3.1 명세에 그대로 들어갑니다. TypeScript 타입은 그 명세에서 생성합니다.

```bash
make openapi     # scripts/dev/gen_openapi.py → docs/api/openapi.json
make contracts   # openapi-typescript → typescript/src/api.ts
```

두 산출물을 같은 커밋에 넣습니다. CI의 `api-gateway` 잡이 두 파일을 다시 만들어 `git diff --exit-code`로 비교합니다.
`make openapi`만 실행하고 `make contracts`를 빠뜨리면 이 비교에서 실패합니다.

### 판정 표는 두 런타임이 함께 읽는다

캡션 제출 게이트(캡션이 `caption_min_tokens` 이상인가)는 화면과 서버가 같은 기준으로 판정해야 합니다.
서버가 화면보다 엄하면 화면에서 통과한 제출을 서버가 거절하고, 라벨러는 고칠 곳을 찾지 못합니다.
브라우저는 Python을, API는 TypeScript를 실행할 수 없어서 함수를 하나로 합칠 수 없습니다.
그래서 입력과 기대값을 JSON 표에 두고 양쪽 테스트가 같은 표를 읽습니다. 한쪽 구현만 바꾸면 그쪽 테스트가 실패합니다.

사람별 실적 표(`labeler-rates.json`)는 판정을 서버가 하고 화면은 표시만 합니다. 비율과 괄호의 분자·분모가 같은 수를 가리켜야 하므로 사례 하나에 서버의 입력(`counts`)과 출력(`row`), 화면의 출력(`view`)을 함께 둡니다. 서버 테스트는 `counts`로 `row`를 확인하고 화면 테스트는 같은 `row`로 `view`를 확인합니다.

| 표 | 서버 구현 | 화면 구현 | 서버 테스트 | 화면 테스트 |
|---|---|---|---|---|
| `caption-gate.json` | `apps/api-gateway/api_gateway/services/caption.py` | `apps/web/src/features/workbench/lib/tokens.ts`·`caption.ts` | `apps/api-gateway/tests/contract/test_caption_gate.py` | `apps/web/src/features/workbench/lib/tokens.test.ts` |
| `caption-shapes.json` | 같은 파일의 `sentences_of`·`final_text`·`split_sentences` | `caption.ts`의 `sentencesOf`·`finalText`, `components/CaptionView.tsx`의 `splitSentences` | `apps/api-gateway/tests/contract/test_caption_shapes.py` | `apps/web/src/features/workbench/lib/caption.test.ts` |
| `connection-address.json` | `apps/api-gateway/api_gateway/services/stacks/connections.py`의 `ROUTING_KEYS`·`secret_required` | `apps/web/src/features/platform/lib/connection_address.ts`의 `ROUTING_KEYS`·`secretRequired` | `apps/api-gateway/tests/contract/test_connection_address.py` | `apps/web/src/features/platform/lib/connection_address.test.ts` |
| `labeler-rates.json` | `apps/api-gateway/api_gateway/services/analytics/dashboard/classify.py`의 `_rate_fields` | `apps/web/src/features/insights/lib/rates.ts`의 `approvalView`·`rejectionView` | `apps/api-gateway/tests/contract/test_labeler_rates.py` | `apps/web/src/features/insights/lib/rates.test.ts` |

표의 값은 손으로 고치지 않습니다. 세는 법을 바꾸려면 두 구현을 같은 커밋에서 바꾸고 표를 다시 계산해 넣습니다.

### 캡션은 세 모양으로 저장되어 있다

제출 캡션은 문장 배열(`caption.brief_sentences`), 문자열(`caption.brief`), 최상위 `caption` 문자열 중 하나입니다.
문장 배열에 항목이 있으면 배열을 씁니다. 없으면 문자열을 문장으로 나누고 위치에 따라 id를 붙입니다(`s0`, `s1`, …).
품질검수 지적이 이 id를 가리킵니다. 그래서 화면과 서버가 같은 id를 붙여야 지적이 같은 문장에 붙습니다.

문자열은 종결 부호(`.` `?` `!` `。`) 뒤의 공백에서 나눕니다. 말줄임표(`...` `…` `⋯`) 뒤와 닫힌 따옴표 안에서는 나누지 않습니다.
보도 캡션은 `소토 3타점... 기선 제압.`처럼 문장 안에서 말줄임표를 쓰기 때문입니다.
캡션을 읽는 새 코드는 위 두 함수만 부릅니다. `caption.brief`를 직접 읽으면 문장 배열만 있는 제출이 빈 캡션으로 보이고 납품에서 빠집니다.

### 큐 계약은 이름의 단일 출처다

worker-io와 worker-jobs는 서로를 import하지 않고 태스크를 이름으로 주고받습니다. 이름과 큐가 한쪽에만 적혀 있어도 빌드는 통과합니다.
그러면 메시지가 소비자 없는 큐에 쌓이거나, 등록되지 않은 태스크로 버려집니다.
그래서 `reborn_contracts.queues`가 이름·큐·라우팅 표를 갖고, 두 워커가 같은 객체를 읽습니다. 페이로드 스키마와 멱등 근거는 `queues.md`에 있습니다.

## 의존성

- 부르는 쪽: `apps/api-gateway`(스키마), `apps/web`(TypeScript 타입), `workers/worker-io`·`workers/worker-jobs`(`queues`)
- 생성 도구: `scripts/dev/gen_openapi.py`, `openapi-typescript`(`typescript/package.json`의 `gen` 스크립트)

## 남은 일

- 페이로드 스키마는 `queues.md`에만 있습니다. 코드로 검사하는 범위는 이름·큐·라우팅까지입니다.
