# features/workbench/pages

## 목적

완료 워크벤치 화면을 둡니다. `../routes.tsx`가 이 화면을 산출물 열람 가드(`workResult`) 아래에 연결합니다. 라벨링 워크벤치는 라우트가 `../components/LabelWorkbench.tsx`를 바로 그립니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `completed_workbench.tsx` | `/p/:slug/completed` | 완료 워크벤치 `CompletedWorkbenchPage`. 왼쪽 레일에 검색과 완료 목록(offset 페이지로 이어 받음)을 두고, 고른 클립을 같은 화면에서 봅니다. 본문은 `features/pipeline`의 `CompletedClipView`입니다. 머리의 수는 완료 요약을 한 번 받아 씁니다. 클립 사이 이동은 머리 줄의 `이전 클립`·`다음 클립` 버튼이나 목록의 행으로 합니다 |

## 설계 이유

- 완료본 확인은 이 클립을 납품해도 되는지 수십 번 연속으로 판단하는 일입니다. 건마다 상세 화면으로 갔다가 돌아오면 이동에 드는 수고가 확인보다 커집니다. 그래서 목록과 본문을 한 화면에 둡니다.
- 완료 단계의 표(`features/pipeline`의 `DonePanel`)는 그대로 둡니다. 정렬·훑어보기·내보내기 진입에는 표가 낫고, 이 화면은 연속 확인용입니다.
- 본문은 완료 상세와 같은 부품을 씁니다. 복사해서 쓰면 두 화면이 결국 달라집니다.
- 좁은 폭에서는 목록과 본문 중 하나만 보이고, 둘 사이를 오갑니다. 좁은 폭에 목록·무대·정보를 함께 넣으면 어느 것도 제대로 쓸 수 없습니다.

## 의존성

- `../api.ts`(`fetchCompletedPage`·`fetchCompletedSummary`·`workbenchKeys`), `../components/*`(머리 줄·단축키), `features/pipeline`(`CompletedClipView`·`COMPLETED_FILTERS`), `features/projects`, `shared/ui/*`
