# reborn-contracts/typescript

## 목적

`@reborn/contracts` 패키지입니다. 서버의 OpenAPI 명세(`docs/api/openapi.json`)에서 생성한 TypeScript 타입을 화면(`apps/web`)에 제공합니다.
타입만 담고 실행 코드는 없습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `package.json` | 패키지 정의. `gen` 스크립트가 `openapi-typescript`로 `src/api.ts`를 만듭니다. `exports`는 `.`과 `./api` |
| `tsconfig.json` | 타입 검사 설정(`lint` 스크립트가 `tsc --noEmit`로 씁니다) |
| [`src/`](src/README.md) | 생성된 타입(`api.ts`)과 진입점(`index.ts`) |

## 설계 이유

- 타입을 손으로 쓰지 않고 명세에서 생성합니다. 서버 모델을 바꾸면 `make contracts`(pnpm이 없으면 npx)로 다시 만듭니다.
  손으로 쓴 타입은 서버가 필드를 바꿔도 컴파일이 통과해서, 어긋남이 화면에서만 드러납니다.
- 생성물(`src/api.ts`)도 커밋합니다. CI가 명세에서 다시 생성해 커밋된 파일과 비교하므로, 커밋하지 않으면 그 비교가 실패합니다.
- 화면은 `tsconfig.json`의 `paths`로 이 폴더의 `src/index.ts`를 직접 가리킵니다(`apps/web/tsconfig.json`).
  `node_modules`의 설치본이 아니라 저장소 안의 파일을 읽으므로 생성 직후 바로 반영됩니다.

## 의존성

- 생성 입력: `docs/api/openapi.json`(`make openapi`가 만든다)
- 개발 의존성: `openapi-typescript`, `typescript`
- 부르는 쪽: `apps/web`(`import type { components } from "@reborn/contracts"`)

## 남은 일

- 없음.
