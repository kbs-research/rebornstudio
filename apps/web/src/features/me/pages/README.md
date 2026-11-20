# features/me/pages

## 목적

개인 작업공간의 라우트 화면 네 개를 둡니다. 주소 연결은 `../routes.tsx`가 합니다. 내 작업·내 활동은 주소에 프로젝트가 없고, 사이드바 전환기에서 고른 프로젝트를 따릅니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `home.tsx` | `/` | 홈. 작업 역할만 가진 사람은 그 역할의 첫 화면으로 보내고, 운영하는 사람에게는 프로젝트 목록(`features/projects`의 `ProjectsPage`)을 그립니다. 이 판정은 `lib/roles`의 `homeLanding`이 합니다. 역할을 확인하기 전에는 아무것도 그리지 않습니다 |
| `inbox.tsx` | `/my/work` | 내 작업(`MyWorkPage`). 역할별 본문(`QueueContent`, `../components/MyQueues.tsx`), 참여 정보 한 줄, 프로젝트 나가기(잃게 되는 것을 보여 주는 확인 창)를 둡니다 |
| `my_activity.tsx` | `/my/activity` | 내 활동. 역할을 확인한 뒤 `../components/MyActivity.tsx`를 그립니다 |
| `account.tsx` | `/account` | 내 계정. 기본 정보(필수 동의 시각 포함), 비밀번호 변경, 로그인 기록(로그인·로그인 실패·로그인 잠금·로그아웃)을 둡니다. 이전에 선택 동의한 사람에게만 철회할 개인정보 카드를 그립니다. 프로필은 바뀐 항목이 있을 때만 뜨는 저장 막대로 저장합니다 |

## 설계 이유

- 프로젝트를 고르지 않았으면 내 작업·내 활동 화면에 프로젝트 선택이 필요하다고 표시합니다. 그 안내의 버튼은 사이드바 전환기를 엽니다(`store/project`의 `requestProjectSwitcher`).
- 필수 동의는 계정을 쓰는 조건이므로 내 계정에서 철회할 수 없습니다. 동의 시각만 보이고, 철회는 계정 삭제 요청으로 안내합니다.
  선택 동의를 철회하면 서버가 선택 정보를 바로 지웁니다. 그래서 확인 창에 지워질 값을 보입니다.
- 비밀번호를 바꾸면 서버가 이전 토큰을 모두 막습니다. 그래서 변경에 성공하면 로그인 화면으로 보냅니다.

## 의존성

- `../api.ts`, `../components/*`, `../lib/*`, `shell/PersonalHeader`, `shared/ui/*`, `store/auth`·`store/project`, `lib/roles`·`role_label`
- `features/projects`, `features/auth`(비밀번호 규칙)

## 남은 일

- 없음.
