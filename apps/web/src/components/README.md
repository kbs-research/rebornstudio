# components

## 목적

여러 기능 폴더가 같이 쓰지만 한 도메인의 것으로 정할 수 없는 화면 부품을 둡니다.
도메인 부품은 `features/<도메인>/components/`에, 도메인을 모르는 부품은 `shared/ui/`에, 셸 부품은 `shell/`에 있습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `StatusBadge.tsx` | 상태 값(작업·영상 업로드·클립 상태·검수 판정·분석 단계·멤버)을 점 칩(`Chip`의 `variant="dot"`)과 이름으로 그립니다. 색조 하나에 뜻 하나를 둡니다. neutral은 대기, note는 진행 중, ok는 끝남, warn은 사람이 봐야 함, bad는 실패·거절, quiet는 닫힘입니다. 같은 값의 이름이 영역마다 다르면 `domain`을 넘깁니다(`rights`면 `lib/vocab`의 `RIGHTS_LABEL`) |
| `ReadOnlyLabel.tsx` | 제출된 라벨의 캡션을 읽기 전용으로 그립니다. 캡션 부품은 `features/workbench`에서 가져옵니다. 라벨러가 채우는 내용은 캡션 하나라 이전 데이터의 다른 키는 그리지 않습니다 |

## 여기에 남은 이유

| 파일 | 쓰는 곳 | 남은 이유 |
|---|---|---|
| `StatusBadge.tsx` | 콘텐츠·사람·작업 단계·플랫폼·워크벤치, 디자인 시스템 견본(`routes/design_system.tsx`) | 상태 이름 표가 `lib/vocab`을 읽기 때문에 `shared/ui`에 둘 수 없습니다. 한 기능에 넣으면 나머지 기능이 그 기능의 `index.ts`를 거쳐 읽어야 합니다 |
| `ReadOnlyLabel.tsx` | 작업 단계(검수 워크벤치·완료 클립 본문)·개인 작업공간(내 활동의 라벨링 기록 상세) | 워크벤치의 캡션 부품을 읽지만 라벨링 워크벤치 화면은 이 부품을 쓰지 않습니다. 맡을 기능이 정해지면 그 기능으로 옮깁니다 |

## 설계 이유

- 상태 이름과 색조를 화면마다 적으면 같은 `대기`가 영역마다 다른 색이 되어 색이 뜻을 잃습니다. 그래서 상태별 색조와 이름의 표를 `StatusBadge` 한곳에 둡니다.
  칩의 모양(여백·글자·모서리)은 `shared/ui/Chip`이 정하고, 이 부품은 어떤 상태가 어떤 색조인지만 정합니다.
- 검수 판정 이름은 `lib/vocab`의 `VERDICT_LABEL`을 읽습니다. 사전이 둘이면 한쪽만 고쳐져 화면마다 이름이 달라집니다.

## 의존성

- `StatusBadge.tsx`: `shared/ui/Chip`, `lib/vocab`
- `ReadOnlyLabel.tsx`: `features/workbench`(캡션 부품)

## 남은 일

- 없음.
