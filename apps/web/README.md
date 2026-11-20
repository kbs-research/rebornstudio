# apps/web

## 목적

RebornStudio의 웹 화면입니다. Vite로 빌드하는 React 단일 페이지 앱이고, 라우팅은 React Router v7(라이브러리 모드)을 씁니다.
라벨러, 검수자, 관리자가 같은 앱을 열고, 역할에 따라 보이는 화면이 달라집니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `src/` | 앱 소스. 폴더 구성은 [`src/README.md`](./src/README.md)에 있습니다 |
| `scripts/` | 웹 소스를 읽는 정적 검사(CI 게이트). 목록은 [`scripts/README.md`](./scripts/README.md)에 있습니다 |
| `index.html` | Vite 진입 HTML. `src/main.tsx`를 불러오고 파비콘을 겁니다. 기본 탭 제목은 제품 이름과 에디션 이름(`RebornStudio Essential`)입니다 |
| `public/` | 빌드 결과 루트에 그대로 복사되는 정적 파일. 파비콘 둘(`favicon.svg`, 구형 브라우저와 `/favicon.ico` 직접 요청용 `favicon.ico`)만 둡니다. 이 폴더의 파일은 모두 공개 주소로 나가므로 설명 파일을 두지 않고 이 표에 적습니다 |
| `vite.config.ts` | 개발 서버(3000, `/api` 프록시), 경로 별칭 `@`(`src/`), 번들 청크 분할, vitest 설정(`node` 환경, `TZ=Asia/Seoul`) |
| `postcss.config.js` | Tailwind v4 PostCSS 플러그인(`@tailwindcss/postcss`) 하나만 등록합니다 |
| `tsconfig.json` | TypeScript strict 설정 |
| `package.json` | `@reborn/web` 패키지. 스크립트는 `dev`·`build`(`tsc -b && vite build`)·`preview`·`lint`(`tsc --noEmit`)·`test`(`vitest run`)입니다 |

## 실행

```bash
pnpm install                          # 워크스페이스 루트에서 먼저 한 번
pnpm --filter @reborn/web dev         # http://localhost:3000
pnpm --filter @reborn/web build       # dist/ 생성
pnpm --filter @reborn/web test        # 순수 로직 단위 테스트
```

로컬 개발에서는 보통 따로 띄우지 않습니다. `make dev-up`이 compose 서비스 `web`으로 같은 개발 서버를 띄웁니다.
`/api` 프록시 대상은 `VITE_API_PROXY_TARGET`으로 정합니다. 호스트에서 띄우면 기본값 `http://localhost:8000`을 쓰고, compose 안에서는 `http://api:8000`을 씁니다.
운영 이미지는 `dist/`를 nginx로 서빙합니다(`infra/docker/web.Dockerfile`).

## 설계 이유

- 지원하는 최소 가로 폭은 1280px입니다. 영상을 보며 타임코드를 찍는 화면이라 모바일은 지원하지 않습니다.
  데스크톱 레이아웃은 `lg:`(1024px)부터 적용되므로 1280px도 1920px과 같은 레이아웃을 씁니다.
- Tailwind는 v4를 씁니다. CSS 진입점은 `src/styles/globals.css`의 `@import "tailwindcss";` 한 줄이고, `tailwind.config.ts`는 없습니다.
  색·간격·글자 값은 `src/tokens.css`의 CSS 변수에 두고, `globals.css`의 `@theme inline`이 그 변수를 유틸로 만듭니다.
  값을 런타임 변수로 두었기 때문에 다크 모드와 흑연 테마 범위(`data-surface`)에서 같은 유틸이 다른 색으로 그려집니다.
- 서버 호출은 기능 폴더의 `api.ts`와 `src/shared/api/`에서만 하고, 쿼리 키는 키 팩토리로만 만듭니다.
  경로 문자열과 키 배열이 화면마다 흩어지면 서버 경로가 바뀌거나 무효화할 키가 하나 빠져도 오류 없이 낡은 화면이 남습니다.
  CI에서는 `scripts/check-feature-boundaries.mjs --strict`가 위반을 막습니다.
- 요청·응답 타입은 `@reborn/contracts`에서 가져옵니다. 서버 OpenAPI 문서에서 생성한 타입이라, 서버 스키마가 바뀌면 `tsc`가 어긋난 호출부를 찾아냅니다.

## 의존성

- 실행: React 18, React Router v7, TanStack Query v5, Zustand v5, React Hook Form, Vidstack(영상 플레이어), lucide-react(아이콘), `@fontsource/*`(자체 호스팅 글꼴)
- 빌드: Vite 6, TypeScript 5, Tailwind v4, vitest
- 워크스페이스: `@reborn/contracts`(`packages/reborn-contracts/typescript`). 서버 스키마를 바꾼 뒤 `make openapi && make contracts`로 갱신합니다
- 부르는 쪽: compose `web` 서비스(`infra/compose/compose.dev.yaml`), 운영 이미지(`infra/docker/web.Dockerfile`), CI lint 잡(`scripts/`의 검사)
