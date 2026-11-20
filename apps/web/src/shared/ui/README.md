# shared/ui

## 목적

도메인을 모르는 UI 부품을 둡니다. 모양은 먹색을 강조색으로 쓰는 디자인 규칙(`src/styles/README.md`)을 따르고, 색·간격·글자는 `src/tokens.css`의 변수를 유틸로 씁니다.
토큰 이름과 유틸의 대응은 [`src/styles/README.md`](../../styles/README.md)에 있습니다. 부품을 새로 만들기 전에 이 폴더의 부품과 슬롯으로 해결되는지 먼저 확인합니다.

## 주요 파일

### 표와 목록

| 파일 | 역할 |
|---|---|
| `DataTable.tsx` | 앱의 모든 표. 정렬·검색창 하나·페이지(`pageSize` 기본 25)·화면이 정하는 밀도(`compact`·`comfortable`·`spacious`)·행 선택(`selectable`·`selectionToolbar`)·행 묶음 머리(`rowGroupHeader`)·좁은 폭 카드 쌓기(`stack`)를 제공합니다. 서버 커서 표는 `pageSize={0}`과 `footer`를 씁니다. 표 상태를 주소에 두려면 `urlState`를 넘깁니다. 열 필터와 표 설정(열 표시·행 간격)은 두지 않습니다 |
| `table_state.ts` | 표 아래 분모 줄 문구(`tableCountLabel`)와, 표 상태를 주소와 연결하는 `useTableUrlState`. 범위 밖 페이지는 마지막 페이지로 맞춥니다 |
| `table_state.test.ts` | 분모 줄 문구와 주소 읽기·쓰기를 확인합니다 |
| `TableCard.tsx` | `Card` + `SectionHeader` + 테두리 없는 `DataTable`. 표 하나로 된 절을 같은 카드 모양으로 그립니다 |
| `SectionHeader.tsx` | 절 머리글(아이콘·제목·보조 설명·수·액션). 카드 안에서는 `CardHeader`를 대신하고, 카드 밖에서는 `bare`로 씁니다. `title`을 넘기지 않으면 수를 왼쪽에, 액션을 오른쪽에 둔 툴바가 됩니다. 제목 태그는 기본 `h2`입니다 |
| `FilterMenu.tsx` | 캡슐 칩과 팝오버로 값 하나를 고르는 필터. 서버에 거는 조건(출처·상태)이나 정렬처럼 화면이 정한 조작에 씁니다. 옵션이 많으면 `searchable`로 입력해 좁힙니다 |
| `WorkerCell.tsx` | 표의 작업자 셀(아바타·이름·이메일). 멤버 표가 씁니다 |
| `EmptyState.tsx` | 빈 목록 안내(문구, 선택 액션, 넘겼을 때만 아이콘). `framed`는 점선 상자, `dense`는 카드 안·팝오버용 압축형입니다 |
| `ErrorState.tsx` | 조회 실패 안내와 다시 시도 버튼. 본문 전체가 실패하면 기본형을, 절 안의 한 줄이면 `inline`을 씁니다 |
| `Skeleton.tsx` | 로딩 골격 블록(`Skeleton`·`SkeletonText`) |
| `PageSkeleton.tsx` | 화면 모양을 미리 그리는 로딩 골격. 블록은 `aria-hidden`이고 바깥에 `role="status"`를 둡니다 |
| `LastUpdated.tsx` | 자동 갱신 화면의 `HH:MM 갱신` 표시와 새로고침 버튼. 알 수 없는 시각은 글자로 표시하지 않습니다 |
| `LastUpdated.test.ts` | `formatUpdatedAt`이 `Infinity`·잘못된 시각을 표시하지 않는지 확인합니다 |

### 지표

| 파일 | 역할 |
|---|---|
| `StatLine.tsx` | 머리글 아래 요약 지표 한 줄. 값이 셋 이상이거나 단위가 길면 `layout="stacked"`로 값을 위에, 이름을 아래에 두 단으로 씁니다 |
| `PeriodSelect.tsx` | 모니터링 화면의 기간 선택. 고른 값은 주소(`?period=`)에 둡니다 |
| `SectionRail.tsx` | 긴 화면의 절 목록을 왼쪽에 두고 지금 보는 절을 표시합니다 |
| `Tooltip.tsx` | 요청할 때만 나오는 보조 설명과 `DefinedTerm`(점선 밑줄은 정의가 있다는 표시) |

### 대화상자와 알림

| 파일 | 역할 |
|---|---|
| `Modal.tsx` | 가운데 창. `size`(sm 400 · md 520 · lg 720px), `tone`(제목 줄 아이콘 배지), `footer`(구분선 아래 고정, 본문만 스크롤)를 받습니다. `dismissible={false}`면 Esc·배경 클릭으로 닫히지 않습니다 |
| `Drawer.tsx` | 오른쪽에서 밀려 나오는 패널. `Modal`과 같은 `icon`·`tone`·`footer` 슬롯이 있습니다 |
| `ShortcutsModal.tsx` | 단축키 도움말 창. 재생 키가 있는 작업 화면(워크벤치·클립 보기)이 같은 창을 씁니다 |
| `Toast.tsx` | 전역 토스트(`ToastProvider`·`useToast`). 흑연 테마(`data-surface="graphite"`)로 그리고, 5초 뒤 사라지며 최대 5개까지 쌓입니다 |
| `failToast.ts` | 뮤테이션 실패를 토스트 두 줄(결과·안내)로 알립니다. 서버가 사유를 주면 그 사유를 씁니다 |
| `StatePanel.tsx` | 셸 본문 영역에 그리는 막힘·실패 안내(아이콘·머리글·첫 문장·안내·사실 줄·버튼). `shell/AccessDenied`·`shell/RouteError`와 보호 셸이 씁니다 |

### 입력

| 파일 | 역할 |
|---|---|
| `field.ts` | 입력란 공통 클래스 `FIELD_BASE`. 기본·hover·focus·오류(`aria-invalid`)·비활성 상태의 모양을 정합니다 |
| `Input.tsx`·`Select.tsx` | 텍스트 입력과 네이티브 select. `field.ts`의 모양을 씁니다 |
| `UnitInput.tsx` | 입력란 안 오른쪽에 단위가 붙는 숫자 입력(`24 시간`) |
| `FormError.tsx` | `react-hook-form`의 검증 실패 문장을 입력란 아래에 그립니다 |
| `Label.tsx` | 폼 라벨 |
| `Checkbox.tsx` | 여러 항목 중에서 고르는 체크박스(16px, 먹색 채움) |
| `Switch.tsx` | 기능 하나를 켜고 끄는 스위치. 지금 상태를 글자(`사용 중`·`사용 안 함`)로도 보여 줍니다 |
| `SegmentedControl.tsx` | 하나만 고르는 토글 묶음(`role="radiogroup"`, ←·→) |
| `DatePicker.tsx` | 날짜 하나 입력. `YYYY-MM-DD`를 직접 입력하거나 달력에서 고릅니다. 쓸 수 없는 날짜가 입력된 동안에는 `onProblem`으로 사유를 전달해, 폼이 제출을 막게 합니다 |
| `DatePicker.test.ts` | 입력 맞춤(`typeYmd`)·날짜 검사(`checkYmd`)·사유(`pendingProblem`)·연도 범위를 확인합니다 |
| `SettingRow.tsx` | 설정 화면의 한 줄(`SettingRow`)과 그 묶음 카드(`SettingCard`). 긴 입력은 `stacked`로 이름 아래 전체 폭에 둡니다 |

### 일반 부품

| 파일 | 역할 |
|---|---|
| `Button.tsx` | `variant`는 `primary`(먹색 채움, 화면당 하나)·`secondary`·`ghost`·`danger-outline`(페이지 안 비가역 동작)·`danger`(확인 창의 마지막 버튼)·`submit`(호박색, 워크벤치 제출)입니다. `size`는 sm 32·md 36·lg 40px입니다. 버튼 모양 링크에는 `buttonClasses`를 씁니다 |
| `Card.tsx` | 페이지 위의 카드(`Card`·`CardHeader`·`CardBody`). 테두리와 `shadow-e1`이 있고, `as`로 `section`·`article` 등의 태그를 씁니다. 카드 안의 면은 평평하게 둡니다 |
| `PageHeader.tsx` | 화면 머리글(제목 `h1`, 제목 옆 `badge`, 아래 `meta`, 오른쪽 액션). 제목을 탭 제목으로 등록합니다. 구분선은 `divider`로 켭니다 |
| `Tabs.tsx` | 접근성을 갖춘 탭 바와 탭 패널(`role="tablist"`, ←·→ 이동). `variant="underline"`은 밑줄 탭입니다 |
| `Chip.tsx` | 캡슐 칩. `soft`(태그·표시)·`dot`(상태)·`count`(수) 세 가지 모양이 있습니다 |
| `IdChip.tsx` | UUID 앞 8자를 보여 주고, 누르면 전체를 복사하는 칩 |
| `Avatar.tsx` | 이름 첫 글자를 담은 원형 칩 |
| `Wordmark.tsx` | 제품 워드마크(호박색 R 표식, 제품 이름, 에디션 이름 `Essential`)와 에디션 이름 표시(`EditionLabel`). 인증 화면·최초 설정·개인정보 안내가 워드마크를, 사이드바 브랜드 줄이 에디션 이름 표시를 씁니다 |
| `Thumb.tsx` | 영상 프레임 썸네일. 이미지가 없으면 seed로 정한 그라디언트를 그립니다. 재생 표시는 `onClick`을 넘길 때만 그립니다 |
| `DiffText.tsx` | 낱말 단위 diff를 본문 안에 그립니다(`shared/lib/diff`) |

## 설계 이유

- 같은 모양을 화면마다 손으로 그리면 한 곳만 고쳐져 화면마다 모양이 달라집니다. 카드를 베낄 때 `shadow-e1`이 빠지면 같은 카드가 화면마다 다른 깊이로 보입니다.
  그래서 손으로 그린 곳이 여럿 보이면 부품에 슬롯이 없는지 먼저 확인하고 슬롯을 더합니다.
  `scripts/check-surface.mjs`가 손으로 베낀 카드를 셉니다.
- `DataTable` 안의 조작은 검색창 하나, 열 머리글 정렬, 페이지 세 가지입니다. 열마다 필터 칩을 두거나 열 표시·행 간격을 사람마다 저장하면 같은 표가 사람마다 다르게 보여, 작업자와 관리자가 같은 수를 보고 이야기하기 어렵습니다.
- `DataTable`의 정렬은 값을 모르는 행(`null`)을 방향과 관계없이 맨 뒤로 보냅니다.
- `DataTable`의 셀은 줄을 바꾸지 않습니다. 셀 안의 `truncate` 글자는 표 영역 폭의 40%를 넘지 않고, 넘는 글은 말줄임표로 잘립니다. 그래서 긴 글 하나가 표를 넓혀 마지막 열을 가로 스크롤 뒤로 밀어내지 않습니다. 긴 글만 담은 열은 `shrink`를 주면 공간이 모자랄 때 먼저 줄어듭니다. 잘리지 않는 글이 함께 있는 열에 `shrink`를 주면 그 글이 옆 셀로 넘칩니다. `truncate`는 블록 요소에 겁니다. 인라인 요소에서는 폭 제한이 적용되지 않습니다.
  열이 값이 없을 때 대신 넣은 수(`-1`·`Number.MAX_SAFE_INTEGER`)를 주면, 정렬을 뒤집었을 때 값을 모르는 행이 맨 앞에 올라와 가장 좋거나 나쁜 값처럼 읽힙니다. 그래서 열은 `?? null`만 줍니다.
- 표 아래 분모 줄은 같은 수를 두 번 쓰지 않고, 여러 페이지면 범위를 씁니다(`39명 중 26–39`). 표 밖에서 거른 행을 넘기는 화면은 `total`로 전체 수를 줍니다.
- `Modal`·`Drawer`의 `footer`는 구분선 아래에 고정합니다. 호출하는 쪽이 액션 줄을 각자 만들면 클래스와 버튼 크기가 화면마다 달라집니다.
- 표시 기간·분모·좋은 방향 같은 지표 설명은 항상 보이는 문구가 아니라 `Tooltip`에 둡니다. 매일 보는 화면에 늘 떠 있는 설명은 읽히지 않고 화면만 복잡하게 만듭니다.
- 이 폴더는 `features/`와 `shared/` 밖을 가져오지 않습니다(`scripts/check-feature-boundaries.mjs` 규칙 4·5).
  역할 표·스토어를 읽는 부품은 `shell/`(`AccessDenied`·`RouteError`), `components/`(`StatusBadge`)에 있습니다.

## 의존성

- 부르는 것: `react`, `react-router-dom`(`table_state`·`PeriodSelect`), `react-hook-form`(`FormError` 타입), `lucide-react`, `shared/lib/*`, `shared/hooks/*`, `shared/api/error_text`(`failToast`)
- 부르는 쪽: 앱 전체

## 남은 일

- 행이 아주 많은 표에 쓸 가상 스크롤은 없습니다. 한 표의 행 수를 페이지로 감당할 수 없게 되면 검토합니다.
