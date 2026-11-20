# features/people/components

## 목적

멤버 화면의 본문, 사람 서랍, 서랍 안의 설정 탭과 확인 창을 둡니다. 사람 서랍은 다른 기능도 `../index.ts`에서 가져가 엽니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `MembersPanel.tsx` | 멤버 화면 본문. 읽기 전용 멤버 표(멤버·역할·상태·보유 중·최근 활동)와 초대 창을 둡니다. 행을 누르면 사람 서랍이 열립니다. 머리 요약 줄의 수(`memberFacts`)도 여기서 셉니다 |
| `PersonDrawer.tsx` | 사람 서랍. 실적·설정 탭을 둡니다. 멤버가 아닌 사람(통계에만 행이 있는 사람)에게는 실적 탭만 보입니다. 설정에 저장하지 않은 변경이 있으면 닫기 전에 확인합니다. 본문만 `PersonPanel`로 따로 쓸 수도 있습니다 |
| `PersonSettings.tsx` | 서랍의 설정 탭. 역할을 바꾸고 저장 막대로 보냅니다. 역할 변경·제거 확인 창, 나간·거절·승인 대기 행의 다시 초대를 둡니다. 제거 확인 창은 보유한 라벨링 작업이 대기열로 돌아간다는 사실을 씁니다 |
| `FactBox.tsx` | 확인 창과 서랍의 회색 사실 상자(이름과 값을 한 줄씩) |

## 설계 이유

- 확인 창은 같은 틀을 씁니다. 대상이 든 한 문장, 무엇이 어떻게 되는지 보여 주는 사실 상자, 되돌리는 방법 한 줄 순서입니다. 사실을 문장 안에 넣으면 비교하기 어렵고 수가 눈에 띄지 않습니다.
- 관리자가 자기 역할을 낮추면 권한을 잃는다고 경고합니다. 저장 뒤에는 프로젝트 목록을 다시 받아 화면이 새 역할을 따르게 합니다.

## 의존성

- `../api.ts`, `../lib/*`, `shared/ui/*`(`DataTable`·`Modal`·`Drawer`·`WorkerCell`·`failToast` 등), `components/StatusBadge`, `lib/role_label`·`person`
- `features/insights`(실적 조회·키·표기), `features/projects`(`projectKeys`)
- 부르는 쪽: `../pages/people.tsx`, `../index.ts`를 거쳐 `features/insights`

## 남은 일

- 없음.
