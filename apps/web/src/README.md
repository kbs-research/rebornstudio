# src

## 목적

웹 앱 소스의 최상위 폴더입니다. 진입점 `main.tsx`가 공급자와 라우터를 연결합니다.
나머지 코드는 세 묶음으로 나뉩니다. 앱 골격(`app/`·`shell/`·`shared/`), 도메인 기능(`features/<도메인>/`), 여러 기능이 같이 쓰는 도메인 코드(`components/`·`hooks/`·`lib/`·`store/`·`routes/`)입니다.
경로 별칭 `@`가 이 폴더를 가리킵니다(`vite.config.ts`).

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `main.tsx` | 진입점. 저장된 테마를 렌더 전에 적용하고(`store/theme`), 글꼴·`tokens.css`·`styles/globals.css`를 불러옵니다. access 토큰은 메모리에만 있으므로 렌더 전에 refresh 쿠키로 세션을 복구하고(`restoreSession`), 그 뒤에 `AppProviders` 안에 `RouterProvider`를 그립니다 |
| `tokens.css` | 디자인 토큰(CSS 변수). 종이 테마와 흑연 테마의 값이 여기 있습니다. 유틸 대응은 [`styles/README.md`](./styles/README.md)에 있습니다 |
| `vite-env.d.ts` | Vite 전역 타입(`import.meta.env`, 에셋 import) |
| [`app/`](./app/README.md) | 앱 조립. 라우트 트리, 보호 셸, 전역 주소 리다이렉트, 전역 공급자, 쿼리 클라이언트 |
| [`shell/`](./shell/README.md) | 셸 부품. 사이드바, 상단바, 내비 표, 브레드크럼, 권한 가드와 안내 |
| [`shared/`](./shared/README.md) | 도메인을 모르는 공용 코드. UI 부품, 훅, 유틸, API 클라이언트 |
| `features/<도메인>/` | 도메인 기능. 폴더마다 `api.ts`(서버 호출·키 팩토리), `routes.tsx`(라우트 배열), `index.ts`(밖에 공개하는 것), `pages/`·`components/`·`lib/`가 있습니다 |
| [`components/`](./components/README.md) | 여러 기능이 쓰는 도메인 부품(`StatusBadge`·`ReadOnlyLabel`) |
| [`hooks/`](./hooks/README.md) | 여러 기능이나 셸이 쓰는 도메인 훅 |
| [`lib/`](./lib/README.md) | 여러 기능이 쓰는 도메인 규칙(역할 표, 기간 판정, 판정 값 이름 등) |
| [`store/`](./store/README.md) | 앱 전체가 읽는 Zustand 스토어(로그인 사용자, 고른 프로젝트, 테마) |
| [`routes/`](./routes/README.md) | 도메인이 없는 라우트 화면(디자인 시스템 견본). 라우트 묶음이 어느 기능에 있는지 찾는 표도 있습니다 |
| [`styles/`](./styles/README.md) | Tailwind 진입 CSS와 토큰 유틸 연결 |

기능 폴더는 다음과 같습니다. 화면·부품·규칙의 목록은 각 폴더 README에 있습니다.

| 기능 | 맡는 화면 |
|---|---|
| [`features/auth/`](./features/auth/README.md) | 로그인 전 화면(로그인, 비밀번호 재설정, 최초 설정, 개인정보 안내)과 동의 화면 |
| [`features/me/`](./features/me/README.md) | 개인 작업공간(홈, 내 작업, 내 활동, 내 계정) |
| [`features/projects/`](./features/projects/README.md) | 프로젝트 목록·생성·설정, 앱 전체가 읽는 프로젝트 조회 훅 |
| [`features/insights/`](./features/insights/README.md) | 개요와 통계 |
| [`features/ingest/`](./features/ingest/README.md) | 영상 등록과 준비 단계 |
| [`features/library/`](./features/library/README.md) | 콘텐츠(영상·클립 카탈로그)와 영상 상세 |
| [`features/pipeline/`](./features/pipeline/README.md) | 작업 단계, 검수·비식별화 신고 처리·캡션 품질검수, 완료 클립 상세 |
| [`features/workbench/`](./features/workbench/README.md) | 라벨링 워크벤치, 완료 워크벤치, 워크벤치 공용 부품 |
| [`features/people/`](./features/people/README.md) | 프로젝트 멤버 |
| [`features/audit/`](./features/audit/README.md) | 감사 로그 |
| [`features/platform/`](./features/platform/README.md) | 플랫폼 관리(최고관리자) |

## 설계 이유

- `app/`·`shell/`·`shared/`는 도메인을 모르는 골격이고, 도메인 코드는 `features/<도메인>/`에 둡니다.
  한 기능의 파일이 여러 폴더에 흩어지면 그 기능을 고칠 때 바깥 코드가 함께 깨지는지 알 수 없습니다.
- `components/`·`hooks/`·`lib/`·`store/`에는 둘 이상의 기능이나 셸이 읽는 도메인 코드만 둡니다.
  이런 코드를 한 기능에 넣으면 나머지 기능이 그 기능의 `index.ts`를 거쳐 읽어야 합니다.
  파일마다 쓰는 곳은 각 폴더 README의 "여기에 남은 이유" 절에 적습니다. 한 기능만 쓰게 되면 그 기능의 `lib/`·`components/`로 옮깁니다.
- 경계 규칙은 네 가지입니다. `api()`는 `features/*/api.ts`와 `shared/api/`에서만 부릅니다. 쿼리 키는 키 팩토리로만 만듭니다.
  다른 기능은 `index.ts`로만 가져옵니다. `shared/`는 `shared/` 밖을 가져오지 않습니다.
  CI에서는 `scripts/check-feature-boundaries.mjs --strict`가 위반을 막습니다.
- 순수 로직 테스트(`*.test.ts`)는 대상 파일 옆에 둡니다. 테스트가 다른 기능의 사전을 `index.ts`로 가져와도 돌도록, `vite.config.ts`의 `test.server.deps.inline`이 영상 플레이어 패키지를 변환합니다.
  자세한 기준은 [`lib/README.md`](./lib/README.md)의 "단위 테스트" 절에 있습니다.

## 의존성

- 외부 패키지는 [`../README.md`](../README.md)의 의존성 절에 있습니다.
- 서버 타입: `@reborn/contracts`

## 남은 일

- 없음. 새 도메인 화면은 `features/<도메인>/`에 만듭니다. 새 화면을 만들 때 확인할 항목은 [`routes/README.md`](./routes/README.md)에 있습니다.
