# reborn-contracts/typescript/src

## 목적

`@reborn/contracts`의 소스입니다. `docs/api/openapi.json`에서 생성한 타입과, 그 타입을 다시 내보내는 진입점이 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `api.ts` | `openapi-typescript`가 생성한 파일. `paths`·`components`·`operations` 타입이 있습니다. 직접 고치지 않습니다 |
| `index.ts` | `api.ts`의 `paths`·`components`·`operations`를 타입으로 다시 내보냅니다 |

## 설계 이유

- `api.ts`를 고치면 다음 `make contracts`가 그 변경을 덮어쓰고, CI의 생성 비교도 실패합니다. 타입을 바꾸려면 서버 모델(`../../python/`)을 고칩니다.
- 화면 코드는 `index.ts`만 가리킵니다. 사용 예는 다음과 같습니다.

```ts
import type { components } from "@reborn/contracts";

type ProjectDTO = components["schemas"]["ProjectDTO"];
```

## 의존성

- 입력: `docs/api/openapi.json`
- 부르는 쪽: `apps/web/src` 전체(타입 import)

## 남은 일

- 없음.
