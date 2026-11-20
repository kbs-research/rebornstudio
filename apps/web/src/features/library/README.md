# features/library

## 목적

콘텐츠(영상·클립 카탈로그) 기능을 담당합니다. 콘텐츠 목록(`/p/:slug/content`, 영상·클립 탭), 영상 상세(`/p/:slug/videos/:videoId`), 클립 보기(`/p/:slug/videos/:videoId/clips`)가 여기 있습니다.
이 화면들이 쓰는 영상 부품(플레이어·미리보기 창), 영상과 클립 한 건을 표기하는 규칙(준비 단계·클립 상태·구간 표기), 서버 호출도 이 폴더에 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 키 팩토리 `libraryKeys`. 영상·클립 한 건, 재생 주소, 클립 목록, 준비 타임라인 조회와 영상 메타 저장·삭제·규격 재검사·변환·썸네일·비식별화·분석 재요청, 비식별화 건너뛰기, 수동 구간 추가, 콘텐츠 영상·클립 목록과 수 |
| `routes.tsx` | 라우트 세 묶음. `app/router.tsx`가 클립 보기(`videoClipsRoutes`)는 일반 멤버 라우트에, 영상 상세(`videoDetailRoutes`)는 산출물을 보는 역할의 가드(`workResult`) 안에, 콘텐츠 목록(`contentRoutes`)은 콘텐츠 가드(`content`) 묶음으로 펼칩니다 |
| `index.ts` | 밖으로 내보내는 것: `VideoPlayer`, `libraryKeys`, `fetchVideo`·`fetchClip`, `clipRangeFormatter`, `clipSummary`·`SPEECH_SOURCE_LABEL` |
| [`pages/`](./pages/README.md) | 콘텐츠 목록·영상 상세·클립 보기 |
| [`components/`](./components/README.md) | 영상 부품, 영상 탭·클립 탭, 미리보기 창, 빈 클립 안내 |
| [`lib/`](./lib/README.md) | 영상·클립 표기 규칙과 재생기 규칙 |

## 설계 이유

- 쿼리 키는 `libraryKeys`로만 만듭니다. 영상 한 건(`["video", id]`)은 영상 상세·클립 보기·플레이어가, 영상 목록 앞부분(`["videos"]`)은 등록 화면이, 클립 한 건(`["clip", pid, clipId]`)은 워크벤치·검수·내 작업이,
  영상의 클립 목록은 영상 상세와 클립 보기가 같은 배열로 읽거나 무효화합니다. 값이 한 글자라도 다르면 한쪽에서 고쳐도 다른 화면에는 이전 값이 남습니다.
- 목록의 수 세기와 검색·필터는 서버가 합니다. 화면이 커서 페이지에 실린 행만 세거나 훑으면 페이지 밖의 영상·클립이 없는 것으로 보입니다. 서버 커서 표이므로 헤더 정렬과 내장 페이저도 끕니다.
- 판정은 서버가 하고, 이 기능은 이름만 붙입니다. 준비 단계(`_prep_stage_case`)를 화면이 다시 계산하면 목록·상세·필터의 결과가 서로 달라집니다.
- `index.ts`가 영상 플레이어를 내보내므로, 이 파일을 거치는 순수 로직 테스트도 `@vidstack/react`를 불러옵니다. 이 패키지는 `.js` 파일에 JSX를 담고 있어서
  `vite.config.ts`의 `test.server.deps.inline` 설정으로 vite 변환을 거칩니다. vitest 설정을 바꿀 때는 이 파일을 거치는 테스트가 있는지 먼저 확인합니다.

## 의존성

- 부르는 것: `@vidstack/react`, `shared/ui/*`, `shared/api/*`, `shared/lib/*`, `shell/RoleGuard`·`RoleLink`, `components/StatusBadge`, `hooks/useAnalysisStack`, `store/auth`·`store/project`,
  `lib/roles`·`anon`·`vocab`·`video_meta`, `features/projects`(`useProjects`·`ProjectShellState`),
  `features/ingest`(준비 카드와 단계 사전), `features/workbench`(머리 줄·단축키)
- 부르는 쪽: `app/router.tsx`, `features/pipeline`(검수 화면, 완료 클립 본문, 클립 구간 칸), `features/workbench`(라벨링 워크벤치), `features/me`(대기열 행)

## 남은 일

- 등록 기능의 키 팩토리(`ingestKeys.videosAll`·`prepTimeline`)가 `libraryKeys`와 같은 값을 따로 만듭니다. 한쪽 값만 고치면 두 키가 달라지므로 한 팩토리로 모을지 정해야 합니다.
- 클립 탭에는 영상 필터가 없습니다. 서버 `/projects/{id}/clips`에 `video_id` 조건이 없고, 한 영상의 클립은 클립 보기 화면이 보여 줍니다.
