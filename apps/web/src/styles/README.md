# styles

## 목적

Tailwind v4의 CSS 진입점을 두고, 디자인 토큰(`../tokens.css`)을 Tailwind 유틸로 연결합니다. 값은 먹색을 강조색으로 쓰는 디자인 규칙을 따릅니다.
화면은 색·간격·글자를 하드코딩하지 않고 이 폴더에서 만든 유틸만 씁니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `globals.css` | Tailwind 진입(`@import "tailwindcss"`). `@theme inline`으로 `../tokens.css`의 변수를 유틸로 만들고, 글자 단계·모서리·그림자·z-index·base 규칙(`body`의 줄바꿈 규칙 포함)·인쇄 규칙과 인증 화면 왼쪽 패널 배경(`bg-auth-panel`)을 둡니다 |
| `../tokens.css` | 토큰 값을 정하는 파일. 종이 테마(`:root`)와 흑연 테마(다크 모드·`data-surface`) 두 블록, 흑연 척도, 화자 팔레트가 있습니다 |
| `tokens_contrast.test.ts` | `tokens.css`를 읽어, 보조 글자(`label-3`)가 두 테마의 네 면(canvas·sunken·surface·메뉴 면)에서 대비 4.5:1을 넘는지와 `label-2`보다 흐린지 확인합니다 |

## 설계 이유

- 값은 런타임 CSS 변수로 두고, `@theme inline`이 그 변수를 유틸에 연결합니다. 그래서 다크 모드나 흑연 범위 안에서는 같은 유틸(`text-label`·`bg-panel`)이 그 테마의 색으로 그려집니다.
  부품은 자기가 어느 테마 위에 있는지 몰라도 됩니다. 커스텀 그림자도 테마별 값이 필요하므로 `tailwind.config.ts`가 아니라 이 폴더의 `@theme inline`에 둡니다.
- 없는 유틸은 오류 없이 무시됩니다. `apps/web/scripts/check-utilities.mjs`가 소스의 유틸이 빌드 CSS에 있는지, 글자 단계와 모서리 이름 밖의 유틸을 쓰지 않았는지 검사합니다.
- `word-break: keep-all`과 `overflow-wrap: anywhere`는 `body`에 두고 상속시킵니다. `keep-all`만 있으면 URL 같은 긴 문자열이 레이아웃을 밀어내고, `anywhere`만 있으면 한국어가 낱글자 단위로 잘립니다.
  요소마다 다시 붙이지 않습니다(`check-false-signals.mjs`가 셉니다).

## 테마와 적용 범위

| 테마 | 켜는 것 | 쓰는 곳 |
|---|---|---|
| 종이 | 기본(`:root`) | 관리 화면 전부 |
| 흑연 | `:root[data-theme="dark"]` | 계정 메뉴의 다크 모드 스위치 |
| 흑연 | `[data-surface="workbench"]` | 라벨링·검수 워크벤치, 완료 보기. 앱 테마 설정과 관계없이 적용됩니다 |
| 흑연 | `[data-surface="graphite"]` | 사이드바, 인증 화면 왼쪽 패널, 토스트 |

세 흑연 행은 같은 값을 씁니다. 범위 선택자는 그 요소 아래의 변수만 바꾸므로 컴포넌트는 테마를 몰라도 됩니다.
`text-label`·`bg-panel`처럼 같은 유틸을 쓰면 테마에 맞는 색이 나옵니다.
범위 선택자는 그 요소의 자손에만 적용되므로, 포털로 `body`에 붙인 요소는 범위 밖에 있습니다.
그래서 워크벤치는 마운트된 동안 문서 루트에도 `data-surface="workbench"`를 붙입니다(`features/workbench/store.ts`의 `useWorkbenchSurface`). 이렇게 해서 워크벤치 루트 밖에 그려지는 요소까지 흑연으로 그립니다.

## 디자인 이름과 유틸 대응

디자인 규칙에서 부르는 이름(`paper.canvas`·`display` 등)과 이 앱의 유틸 이름이 다릅니다. 화면을 만들 때는 아래 대응을 따릅니다.

| 디자인 이름 | 유틸 | 값(종이 / 흑연) |
|---|---|---|
| paper.canvas | `bg-canvas` | #F4F5F7 / 900 |
| paper.sunken | `bg-canvas-2` | #EEF0F3 / 850 |
| paper.surface | `bg-panel` | #FFFFFF / 800 |
| (메뉴·모달 면) | `bg-raised` | #FFFFFF / 750 |
| paper.line | `border-separator` | #E3E6EA / 700 |
| paper.line-strong | `border-separator-strong` | #CDD2D9 / 600 |
| ink · ink-2 · ink-3 · ink-4 | `text-label` · `text-label-2` · `text-label-3` · `text-label-4` | #16181C · #4D535D · #646B76 · #9AA0A9 / 흑연 100 · 300 · #8A919C · 500 |
| 주 버튼 먹색 채움 | `bg-accent-fill text-accent-fg` | ink / 흑연 100 |
| 선택 표시·밑줄 탭·스위치 | `bg-accent` · `text-accent` · `border-accent` | ink / 흑연 100 |
| amber.500 · 600 | `bg-amber` · `bg-amber-strong` | |
| amber.700 · text-dark | `text-amber-text` | |
| amber.soft · soft-dark | `bg-amber-soft` | |
| amber.line-dark | `border-amber-line` | |
| amber.on | `text-on-amber` | |
| focus | `ring-focus` · `outline-focus` · `border-focus` | amber.500 |
| semantic ok·warn·bad·info | `ok`·`warn`·`bad`·`note` 네 계열(`bg-*`·`bg-*-muted`·`text-*-text`) | |
| elevation card · pop · overlay | `shadow-e1` · `shadow-pop` · `shadow-e3` | |
| radius control · card · overlay · pill | `rounded-control` · `rounded-card` · `rounded-overlay` · `rounded-capsule` | 7 · 10 · 12 · 9999 |
| 흑연 척도 | `var(--graphite-950)`~`var(--graphite-100)` | 토큰만 있습니다. 화면은 위 시맨틱 유틸을 씁니다 |

글자 단계:

| 디자인 이름 | 유틸 | 크기·굵기 |
|---|---|---|
| display | `text-display` | 32 / 700, -0.02em |
| title | `text-title-1` | 24 / 700, -0.01em |
| heading | `text-headline` | 16 / 600 |
| body | `text-body`(= `text-callout`) | 14 / 400, 행간 1.6 |
| body-strong | `text-body font-semibold` | 14 / 600 |
| caption | `text-footnote` | 13 / 400 |
| label | `text-caption font-semibold` | 12 / 600 |
| micro | `text-micro` | 11 / 500 |
| code | `font-mono text-caption` | 12 |

`text-title-2`(20)와 `text-nano`(10)는 위 글자 단계 밖의 크기입니다. 지금 쓰는 화면이 있어 남겨 두지만, 새 화면에는 쓰지 않습니다.

## 글꼴

IBM Plex Sans KR(본문)과 JetBrains Mono(타임코드·ID)를 `@fontsource` 패키지로 자체 호스팅합니다.
폐쇄망에 배포해 외부 CDN에 접속할 수 없어도 같은 글꼴로 그리기 위해서입니다. `src/main.tsx`가 굵기별 CSS를 가져옵니다.

| 글꼴 | 패키지 | 라이선스 | 싣는 굵기 |
|---|---|---|---|
| IBM Plex Sans KR | `@fontsource/ibm-plex-sans-kr` | SIL Open Font License 1.1 (IBM Corp.) | 400 · 500 · 600 · 700 |
| JetBrains Mono | `@fontsource/jetbrains-mono` | SIL Open Font License 1.1 (JetBrains Mono Project Authors) | 400 · 500 · 600 |

OFL은 글꼴 파일을 앱과 함께 배포하도록 허용합니다. 글꼴 파일만 따로 팔 수 없고, 고친 글꼴에는 예약 이름을 쓸 수 없습니다.
라이선스 전문은 각 패키지의 `LICENSE`에 있습니다.
한글은 unicode-range 조각(굵기당 약 95개)으로 나뉘어 있어, 브라우저는 화면에 나온 글자가 든 조각만 내려받습니다.
빌드 산출물에는 조각 파일이 모두 들어갑니다(약 10MB).

## 의존성

- `tailwindcss` ^4, `@tailwindcss/postcss`: 저장소 루트에서 `pnpm install`을 먼저 실행해야 합니다.
- `@fontsource/*`: 위 글꼴.

## 남은 일

- 흑연 테마의 `warn`·`note` 값은 종이 테마와 같은 색상에서 밝기만 올린 임시 값입니다. 흑연 테마용 값을 정하면 맞춥니다.
- `text-label-3`(ink-3)는 종이·흑연 네 면에서 대비 4.5:1을 넘습니다(종이 4.71~5.38, 흑연 4.54~5.65).
  다만 `bg-fill` 칩 위에서는 4.5:1에 못 미칩니다(종이 canvas 위 4.38, 흑연 800 위 4.05). 칩 안의 읽을 글자에는 `text-label-2`를 씁니다.
- `text-label-4`(ink-4)는 대비가 2.6:1이라 장식(구분점·빈 값 `—`·아이콘)과 비활성 컨트롤에만 씁니다. 읽을 글자에는 `text-label-3` 이상을 씁니다.
