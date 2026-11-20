# lib

## 목적

둘 이상의 기능 폴더나 셸이 같이 읽는 도메인 규칙을 둡니다. 역할과 라우트 권한 표, 역할·판정 값의 화면 이름, 참여 기간 판정, 운영 시간대의 날짜 같은 것입니다.
대부분 React에 의존하지 않는 순수 함수와 표입니다. 훅은 `anon.ts`의 `useViewerIsAdmin` 하나입니다.
도메인과 무관한 표기·유틸은 `shared/lib/`에, 한 기능만 쓰는 규칙은 `features/<도메인>/lib/`에 둡니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `roles.ts` | 역할 판정 함수와 라우트 권한 표 `ROUTE_ACCESS`(라우트 키별 허용 역할). 역할의 첫 화면 `landingFor`, 작업 단계 표 `STAGE_ROLES`·`visibleStages`, 막힌 화면 문구 `deniedCopy`도 여기 있습니다 |
| `role_label.ts` | 플랫폼 역할(`GLOBAL_ROLE_LABEL`)과 프로젝트 역할(`PROJECT_ROLE_LABEL`)의 화면 이름. 사전에 없는 값은 원문을 씁니다 |
| `person.ts` | 사람 이름 한 줄(`personLabel`). 이름, 이메일, `이름 없음` 순서로 고릅니다 |
| `anon.ts` | 관리자가 아닌 사람에게 다른 작업자의 이름 대신 결정적 해시 핸들(`anonId`·`who`)을 보여 줍니다. 관리자 여부는 `useViewerIsAdmin`으로 판정합니다 |
| `progress.ts` | 진척률의 이름(건수 기준·시간 기준)과 표시 분기 `progressView`. 계산은 서버(`analytics.stats`)가 합니다 |
| `vocab.ts` | 클립 상태와 검수 판정 값의 화면 이름(`RIGHTS_LABEL`·`VERDICT_LABEL`). `legal_review`는 `비식별화 신고`로 부릅니다 |
| `video_meta.ts` | 영상 단위 메타 네 가지(방송사·프로그램명·장르·방영일)의 키·이름과 방영일 입력 변환. 키는 서버 계약 `VideoMetaDTO`와 같습니다 |
| `blocker.ts` | 납품 제외 사유 한 줄(`{항목 이름}: {사유}`)을 항목 이름과 사유로 나눕니다(`splitBlocker`) |

`*.test.ts`는 옆 파일의 순수 로직 테스트입니다.

## 여기에 남은 이유

| 파일 | 읽는 곳 |
|---|---|
| `roles.ts` | 앱 조립, 셸, 통계, 콘텐츠, 개인 작업공간, 사람, 작업 단계, 워크벤치 |
| `role_label.ts` | 셸, 통계, 개인 작업공간, 사람, 플랫폼, 프로젝트 |
| `person.ts` | 통계, 사람 |
| `progress.ts` | 통계, 플랫폼, 프로젝트 |
| `vocab.ts` | 등록, 콘텐츠, 개인 작업공간, 사람, 작업 단계, `components/StatusBadge` |
| `anon.ts` | 콘텐츠, 개인 작업공간, 작업 단계, 워크벤치 |
| `video_meta.ts` | 콘텐츠, 작업 단계, 워크벤치 |
| `blocker.ts` | 작업 단계, 워크벤치 |

한 기능만 읽게 되면 그 기능의 `lib/`로 옮깁니다.

## 설계 이유

- 라우터 가드(`shell/RoleGuard`), 링크(`shell/RoleLink`), 사이드바(`shell/nav-config`), 첫 화면이 모두 `ROUTE_ACCESS`를 읽습니다.
  가드와 링크가 다른 목록을 읽으면 링크를 눌렀을 때 접근 권한 없음 화면이 나옵니다.
  목록에서 한 줄이 빠져도 타입은 그대로이므로 `roles.test.ts`에 역할별 기대값을 적어 둡니다.
- 참여 기간이 끝나도 저장된 멤버 상태는 `active`로 남습니다. 화면이 기간을 날짜로 계산하지 않으면 이미 끝난 사람이 목록에 활성으로 보입니다.
- 판정 값·역할·라벨 항목의 이름은 사전 한곳에 둡니다. 화면마다 사전이 있으면 하나만 고쳐져 같은 값이 화면마다 다른 이름으로 나옵니다.
  모르는 값은 원문을 그대로 보여 줍니다. 모르는 값을 감추면 서버가 새 값을 더했을 때 그 값이 화면에서 조용히 사라집니다.
- 진척률의 나눗셈은 서버에만 둡니다. 화면에 계산을 두면 서버와 다른 판정이 하나 더 생깁니다. 이 폴더는 어떤 기준으로 쟀는지의 이름과 표시 분기만 정합니다.

## 단위 테스트

순수 로직은 옆에 `<파일>.test.ts`를 두고 `pnpm --filter @reborn/web test`(`vitest run`)로 실행합니다. 설정은 `vite.config.ts`의 `test`에 있습니다.
대상은 틀려도 `tsc`가 잡지 못하고 화면만 틀어지는 코드입니다. 예를 들면 다음과 같습니다.

- 집계 규칙, 기간 판정, 날짜 경계
- 서버 계약과 같은 값인지 확인하는 코드. `features/ingest/lib/dispatch_body.test.ts`는 `docs/api/openapi.json`의 요청 모델과 대조합니다.
- 서버와 같은 판정 표를 읽는 코드. `features/workbench/lib/tokens.test.ts`와 `caption.test.ts`는 `packages/reborn-contracts/caption-shapes.json`을 서버 테스트와 같이 읽습니다.

실행 환경은 다음과 같이 정합니다.

- 환경은 `node`, 시간대는 `Asia/Seoul`로 고정합니다(`test.env.TZ`). 날짜가 하루 밀리는 버그는 시간대에 따라 드러나기 때문입니다.
- 화면 렌더 테스트는 두지 않습니다. `features/workbench/lib/caption.ts`처럼 부품 파일에서 순수 함수 하나만 가져오는 경우는 node 환경에서 그대로 실행됩니다.
- 테스트에서도 다른 기능의 사전은 그 기능의 `index.ts`로 가져옵니다. `index.ts`가 화면 부품을 함께 내보내므로 영상 플레이어(`@vidstack/react`)까지 불러오게 됩니다.
  이 패키지는 `.js` 파일에 JSX를 담고 있어 Node가 읽지 못합니다. 그래서 `test.server.deps.inline`에 넣어 vite 변환을 거치게 했습니다.
  같은 오류(`SyntaxError: Unexpected token '<'`)가 다른 패키지에서 나면 import 경로를 우회하지 말고 그 패키지를 이 목록에 더합니다.

## 의존성

- `anon.ts`: `store/auth`·`store/project`·`features/projects`(`useProjects`)로 보는 사람이 관리자인지 판정합니다
- 그 밖: `shared/lib/josa`·`fmtSecs`·`time`, `shared/api/client`(타입)
- 부르는 쪽: 위 "여기에 남은 이유" 표

## 남은 일

- 없음.
