# features/workbench

## 목적

워크벤치 기능을 담당합니다. 라벨링 전체 화면 워크벤치(`/work/:id/label`), 완료 워크벤치(`/p/:slug/completed`), 라벨링 워크벤치의 로직 훅(`useLabelWorkbench`)이 여기 있습니다.
두 워크벤치(라벨링·검수)와 클립 보기·완료 보기가 함께 쓰는 머리 줄·확인 창·가이드·단축키·문장 부품, 캡션 문장·토큰 규칙, 서버 호출도 이 폴더에 둡니다.
검수 워크벤치 화면 자체는 작업 단계 기능(`features/pipeline`)에 있습니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `api.ts` | 서버 호출과 키 팩토리 `workbenchKeys`. 작업 한 건·라벨 이력·검수 이력·클립 조회, 임시저장·제출·비식별화 미흡 신고, 완료 목록·완료 요약 |
| `routes.tsx` | 라우트 두 묶음. 라벨링 워크벤치(`workbenchRoutes`)는 일반 멤버 라우트에 두고, 권한은 서버의 보유 판정이 정합니다. 완료 워크벤치(`completedWorkbenchRoutes`)는 산출물 열람 가드(`workResult`) 묶음에 있습니다 |
| `store.ts` | `useWorkbenchSurface`(마운트된 동안 문서 루트에 `data-surface="workbench"`를 붙임) |
| `index.ts` | 밖으로 내보내는 것: 공용 부품(머리 줄·확인 창·가이드 서랍·단축키 창·문장 판정·키캡·칩 크기 기준·라벨 표시 부품·캡션 본문), 캡션 규칙(`sentencesOf`·`finalText`·`liveSentences`·`toSentences`·`joinSentences`), 캡션 토큰 수(`estimateTokens`, 검수 화면의 수정본 기준 검사), 재생 단축키 처리기(`useVideoShortcuts`·`VIDEO_SHORTCUTS`), 면 범위 훅(`useWorkbenchSurface`). 두 화면은 내보내지 않습니다 |
| [`pages/`](./pages/README.md) | 완료 워크벤치 화면 |
| [`components/`](./components/README.md) | 라벨링 워크벤치(뷰와 로직 훅)와 워크벤치 공용 부품 |
| [`lib/`](./lib/README.md) | 캡션 문장·토큰 규칙, 단축키 표, 첫 내용 고르기, 돌아갈 화면, 재작업·보유 판정 |

## 설계 이유

- 워크벤치는 앱 테마와 관계없이 흑연색 화면입니다. 루트에 `data-surface="workbench"`를 붙이고, 워크벤치 루트 밖에 그려지는 요소까지 흑연색으로 그리도록 `useWorkbenchSurface`가 문서 루트에도 같은 속성을 붙입니다. 테마 전환 버튼은 없습니다.
- 오른쪽 레일의 폭은 두 워크벤치와 완료 보기 모두 416px입니다. 폭이 다르면 라벨링에서 검수로 넘어갈 때 오른쪽 패널이 움직입니다. 루트 글자 크기가 14px이라서 rem으로 적으면 의도한 폭과 달라지므로 px로 적습니다.
- 재생 위치는 원본 `<video>`의 절대 시간으로 읽고, 이동은 클립 상대 API로 합니다.
- 서버도 제출 조건을 검사합니다. 두 구현이 서로 다르면 화면에서는 통과했는데 서버가 거절하게 됩니다. 런타임이 둘이라 함수를 합칠 수 없으므로, 양쪽 테스트가 같은 판정 표 파일을 읽습니다(아래 표).
- 라벨러가 채우는 입력란(캡션 하나)과 읽기만 하는 필드(영상 단위 메타 네 가지: 방송사·프로그램명·장르·방영일)를 나눕니다. 영상 메타는 관리자가 영상 상세에서 입력하고, 클립이 그 값을 물려받습니다.
- AI 장면 요약이 라벨러의 캡션을 이끌지 않게 합니다. 분석 초안은 빈 캡션에만 채웁니다. 따로 보는 AI 참고 탭은 두지 않습니다.
- 단축키는 재생 키, 임시저장·제출, 문장 편집 확정·취소, 창 닫기(Esc)만 둡니다. 건 이동·가이드 열기는 버튼으로 합니다. 키가 많으면 단축키 창이 길어지고, 같은 키가 화면마다 다른 뜻이 되기 쉽습니다.
- 쿼리 키는 `workbenchKeys`로만 만듭니다. 보유 무효화는 `features/me`, 클립 조회는 `features/library`, 작업 단계 요약 무효화는 `features/pipeline`의 호출과 키를 씁니다.
- `index.ts`는 두 화면을 내보내지 않습니다. 화면은 라우터만 쓰고(`routes.tsx`), 다른 기능 폴더가 부품을 가져올 때 화면까지 딸려 오지 않게 합니다.

### 서버와 같은 판정 표

| 판정 표 | 화면 테스트 | 서버 짝 |
|---|---|---|
| `packages/reborn-contracts/caption-gate.json` | `lib/tokens.test.ts` | `apps/api-gateway/api_gateway/services/caption.py` · `apps/api-gateway/tests/contract/test_caption_gate.py` |
| `packages/reborn-contracts/caption-shapes.json` | `lib/caption.test.ts` | `apps/api-gateway/api_gateway/services/caption.py` · `apps/api-gateway/tests/contract/test_caption_shapes.py` |

화면 테스트는 판정 표를 테스트 파일 위치 기준의 상대 경로로 읽습니다. 폴더를 옮기면 그 경로, 판정 표의 `web` 항목, 서버 쪽 주석을 함께 고칩니다.

## 의존성

- 부르는 것: `shared/ui/*`, `shared/api/*`, `shared/lib/*`(`diff`·`josa` 포함), `shared/hooks/useUnsavedGuard`·`useOutsideClose`·`useDocumentTitle`, `shell/AccessDenied`, `components/StatusBadge`,
  `hooks/useSlugProject`, `lib/video_meta`·`blocker`·`roles`·`anon`, `store/auth`·`store/project`,
  `features/projects`, `features/me`(`invalidateHolding`), `features/library`(플레이어·클립 조회), `features/pipeline`(`CompletedClipView`·필터 축·`pipelineKeys`)
- 부르는 쪽: `app/router.tsx`, `features/pipeline`(검수 워크벤치, 완료 보기, 경계 편집기, 검수 규칙), `features/library`(클립 보기), `features/me`(캡션 미리보기), `components/ReadOnlyLabel`

## 남은 일

- `pipelineKeys`(`workItem`·`workLabels`·`workReviews`·`completedSummary`)가 작업 한 건·라벨·검수 키와 완료 요약 키를 같은 값으로 따로 만듭니다. 완료 요약 조회 함수도 두 개입니다. 한 팩토리로 모을지 정해야 합니다.
