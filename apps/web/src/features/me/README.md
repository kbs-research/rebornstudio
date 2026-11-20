# features/me

## 목적

개인 작업공간을 담당합니다. 로그인한 사람이 자기 일을 보는 화면입니다. 홈(`/`), 내 작업(`/my/work`), 내 활동(`/my/activity`), 내 계정(`/account`)이 여기 있습니다.
그 본문 부품, 내 라벨링 기록의 이름·표기 규칙, 두 대기열(라벨링·검수)의 보유 조회와 다음 작업 호출도 이 폴더에 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 키 팩토리 `meKeys`. 두 대기열의 보유 목록·대기 수·다음 작업, 프로젝트 나가기, 비밀번호 변경, 로그인 기록, 프로필 저장, 선택 동의 철회, 내 판정 목록, 라벨링 기록(목록·상세) |
| `routes.tsx` | 라우트 두 묶음. `app/router.tsx`가 홈(`meHomeRoutes`)은 일반 멤버 라우트 맨 앞에, 나머지 세 화면(`meRoutes`)은 개요 뒤에 펼칩니다. 가드는 없습니다. 역할에 맞지 않으면 각 화면이 알맞은 화면으로 보냅니다 |
| `index.ts` | 밖으로 내보내는 것: 두 대기열의 조회 호출과 `meKeys`, 다음 작업 훅(`useLabelQueue`·`useReviewQueue`)과 워크벤치 주소(`workbenchPath`), 보유 무효화(`invalidateHolding`). 화면과 부품은 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 화면 네 개 |
| [`components/`](./components/README.md) | 내 작업과 내 활동의 역할별 본문, 대기열 훅 |
| [`lib/`](./lib/README.md) | 보유 무효화, 캡션 미리보기 선택, 내 라벨링 기록 사전, 내 판정 목록 표기 |

## 설계 이유

- 보유 목록·대기 수 키는 작업 단계 화면의 대기열(`features/pipeline`), 라벨링 워크벤치, 검수 화면이 같은 배열로 읽고 무효화합니다.
  값이 한 글자라도 다르면 한쪽에서 다음 작업을 받아도 다른 쪽의 수가 그대로 남습니다. 그래서 키를 `meKeys` 하나로 만들고 `index.ts`로 내보냅니다. `…All` 키는 프로젝트를 뺀 앞부분이므로 전체 무효화에 씁니다.
- 보유 목록의 순서(재작업 먼저, 그다음 받은 순)는 서버(`work_service.held_order`)가 정합니다. 화면은 다시 정렬하지 않습니다. 다음 작업은 서버가 이 순서의 첫 건을 다시 줍니다.

## 의존성

- 부르는 것: `shared/ui/*`·`shared/api/*`·`shared/lib/*`·`shared/hooks/useOutsideClose`, `shell/PersonalHeader`, `store/auth`·`store/project`, `lib/roles`·`role_label`·`anon`·`vocab`, `components/ReadOnlyLabel`,
  `features/projects`(`useProjects`·`projectKeys`·`ProjectsPage`), `features/auth`(비밀번호 규칙), `features/pipeline`(대기열 낱말·`pipelineKeys`),
  `features/library`(`fetchClip`·`libraryKeys`·`clipSummary`), `features/workbench`(`finalText`)
- 부르는 쪽: `app/router.tsx`, `features/pipeline`(대기열 패널·검수 화면), `features/workbench`(`useLabelWorkbench`), `features/insights`(개요 작업자 카드의 대기 수)

## 남은 일

- 없음.
