# shared

## 목적

도메인을 모르는 공용 코드를 둡니다. 화면 부품(`ui/`), React 훅(`hooks/`), 표기·유틸(`lib/`), 서버 호출 입구(`api/`) 네 폴더로 나뉩니다.
이 코드는 역할·프로젝트·작업 같은 서버 개념을 판단하지 않고, 어느 화면에서 불러도 같은 일을 합니다.

## 주요 파일

| 폴더 | 내용 |
|---|---|
| [`ui/`](./ui/README.md) | 디자인 토큰 기반 UI 부품(버튼, 표, 모달, 드로어, 빈 상태, 지표 줄 등) |
| [`hooks/`](./hooks/README.md) | 도메인 없는 훅(`useOutsideClose`·`useDialogFocus`·`useDocumentTitle`·`useMediaQuery`·`useUnsavedGuard`) |
| [`lib/`](./lib/README.md) | 도메인 없는 표기·유틸(`cn`, 날짜·시각, 조사, 초·바이트 표기, 날짜 문자열, diff, 보안 컨텍스트 대체 구현 등) |
| [`api/`](./api/README.md) | API 클라이언트(`client.ts`), 인증 토큰과 세션(`auth.ts`), 오류 문구 사전(`error_text.ts`) |

## 설계 이유

- `shared/`는 `features/`를 가져오지 않습니다. 공용 부품이 도메인을 알면 도메인을 옮기거나 고칠 때마다 공용 부품도 같이 바뀝니다.
- `shared/`는 `shared/` 밖(`lib/`·`store/`·`components/`·`hooks/`·`shell/`)도 가져오지 않습니다. 예외는 계약 타입 `@reborn/contracts`와 외부 패키지입니다.
  `scripts/check-feature-boundaries.mjs`의 규칙 4·5가 두 경우를 세고, CI는 `--strict`로 한 건이라도 있으면 막습니다.
- 공용 부품이 도메인 파일을 읽어야 한다면 그 부품이 정말 공용인지부터 확인합니다.
  역할 표를 읽는 권한 안내(`AccessDenied`)와 라우트 오류 화면(`RouteError`)은 `shell/`에 둡니다.
  상태 이름 사전을 읽는 `StatusBadge`는 `components/`에 둡니다.
- 별도 UI 패키지로 분리하지 않습니다. 이 부품을 쓰는 앱이 하나뿐이고, Tailwind `@theme`과 `scripts/check-utilities.mjs`가 이 앱의 빌드 CSS를 전제로 합니다.

## 의존성

- 부르는 것: `react`·`react-dom`, `react-router-dom`(주소에 상태를 두는 `table_state`·`PeriodSelect`), `react-hook-form`(`FormError`), `zustand`, `clsx`·`tailwind-merge`, `lucide-react`, `@reborn/contracts`
- 부르는 쪽: 앱 전체(`app/`·`shell/`·`features/`·`components/`·`hooks/`·`lib/`·`routes/`)

## 남은 일

- 없음.
