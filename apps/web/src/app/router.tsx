/**
 * 앱의 라우트 정의.
 * 인증 전 화면을 먼저 두고,
 * 나머지는 셸(`ProtectedLayout`) 아래에서 역할 가드(`RoleGuard`)별로 묶는다.
 */
import { createBrowserRouter, Navigate } from "react-router-dom";

import { RouteError } from "@/shell/RouteError";

import { RoleGuard } from "@/shell/RoleGuard";

import { ProtectedLayout } from "./_layout";
import { insightsRoutes, overviewRoutes } from "@/features/insights/routes";
import {
  completedDetailRoutes, pipelineRoutes, qcRoutes, reviewRoutes,
} from "@/features/pipeline/routes";
import { completedWorkbenchRoutes, workbenchRoutes } from "@/features/workbench/routes";
import { authRoutes } from "@/features/auth/routes";
import { DesignSystemPage } from "@/routes/design_system";
import { platformOpsRoutes, platformRoutes } from "@/features/platform/routes";
import { peopleRoutes } from "@/features/people/routes";
import { projectNewRoutes, projectRoutes, projectSettingsRoutes } from "@/features/projects/routes";
import { meHomeRoutes, meRoutes } from "@/features/me/routes";
import { contentRoutes, videoClipsRoutes, videoDetailRoutes } from "@/features/library/routes";

/** 앱 전체 라우터. 인증 전 화면, 셸 아래 화면, 없는 주소 순서로 맞춘다. */
export const router = createBrowserRouter([
  ...authRoutes,
  /*
    디자인 시스템 카탈로그는 개발 빌드에서만 등록한다.
    관리자와 작업자에게는 필요 없는 화면이라 로그인 가드 대신 운영 빌드에서 뺀다.
    가드로 막으면 코드가 번들에 남아 가드 한 줄을 잘못 고칠 때 열리고, 공개 주소로 두면 부품 카탈로그·토큰 이름·문구 규칙이 외부에 보인다.
    `import.meta.env.DEV`는 빌드할 때 상수로 바뀌므로 운영 빌드에서 이 항목은 `...[]`가 되고 `DesignSystemPage`도 번들에서 빠진다.
  */
  ...(import.meta.env.DEV
    ? [{ path: "/design-system", element: <DesignSystemPage /> }]
    : []),
  {
    path: "/",
    element: <ProtectedLayout />,
    // 오류 화면은 셸 안에 띄운다. 사이드바가 남아 있어야 잘못 들어온 사람이 다른 화면으로 나갈 수 있다.
    // 최상위에 두면 셸까지 사라져 "홈으로" 링크 하나만 남는다.
    errorElement: <RouteError />,
    children: [
      // ── 일반 멤버 라우트 (BE 멤버십 가드) ──
      ...meHomeRoutes,
      // 프로젝트 목록 주소는 남긴다. 홈이 작업 화면으로 보내더라도 프로젝트를 고를 수 있어야 한다.
      ...projectRoutes,
      { path: "p/:slug", element: <Navigate to="overview" replace /> },
      /*
        클립 뷰어는 프로젝트 멤버라면 누구나 본다. 클립이 어떻게 나뉘었는지는 라벨러도 봐야 한다.
      */
      ...videoClipsRoutes,
      ...overviewRoutes,
      ...meRoutes,
      // 라벨링 화면은 하나다.
      ...workbenchRoutes,

      /*
        권한 가드는 라우트 키(`route`)만 받는다. 키마다 역할 목록은 `lib/roles.ts`의 `ROUTE_ACCESS`에 있다.
        링크를 그리는 화면(`RoleLink`)과 사이드바가 같은 표를 읽는다.
      */

      // ── super_admin 전용 (글로벌) ──
      {
        element: <RoleGuard route="platform" />,
        children: [
          ...platformRoutes,
          ...platformOpsRoutes,
        ],
      },

      // ── 프로젝트 생성: super_admin ──
      ...projectNewRoutes,

      // ── 통계: project_admin. 검수자와 라벨러는 막는다(`INSIGHTS_ROLES`, features/insights/routes.tsx) ──
      ...insightsRoutes,
      // ── 멤버: project_admin ──
      ...peopleRoutes,
      // ── 캡션 품질검수: 서버 라우트와 같은 권한(project_admin)(features/pipeline/routes.tsx) ──
      ...qcRoutes,

      // ── project_admin 전용: 프로젝트 설정 ──
      {
        element: <RoleGuard route="settings" />,
        children: [
          ...projectSettingsRoutes,
        ],
      },

      // ── 파이프라인: 작업 흐름을 보는 역할 ──
      // 관리자와 검수자가 들어온다. 검수자의 사이드바에는 메뉴가 없지만(`STAGES_MENU_HIDDEN`) 주소와 링크로는 연다.
      // 라벨러는 자기 큐(`내 작업`)만 보므로 URL로 직접 들어오는 것도 막는다.
      // review 리다이렉트의 도착지이므로 그 리다이렉트의 역할을 모두 넣는다.
      // 보여 줄 단계는 `visibleStages`가 정한다.
      ...pipelineRoutes,

      /*
        산출물을 보는 역할.
        클립 뷰어의 '영상 상세' 링크도 이 목록(`WORK_RESULT_VIEWER_ROLES`)을 읽는다.
        검수자는 사이드바 메뉴 없이 검수 화면의 `완료 클립에서 보기`와 완료 클립의 영상 링크로 들어온다.
      */
      {
        element: <RoleGuard route="workResult" />,
        children: [
          /*
            완료 워크벤치는 검색, 목록, 고른 클립을 한 화면에서 보여 주고, 상세(`/p/:slug/completed/:workItemId`)는 따로 남긴다.
            375px 폭에서는 3단 배치를 할 수 없지만 상세는 세로로 접히므로, 상세를 없애면 좁은 폭에서 쓸 화면과 북마크가 사라진다.
            목록과 상세는 같은 가드를 쓴다. 목록만 일반 멤버 라우트로 두면 라벨러가 주소로 목록을 열어
            다른 사람이 쓰고 승인된 캡션 전부를 볼 수 있다. 라벨러용 참고 예시는 프로젝트 가이드에 싣는다.
          */
          ...completedWorkbenchRoutes,
          // 완료된 클립 하나를 읽기 전용으로 연다. 목록과 같은 가드를 쓴다.
          ...completedDetailRoutes,
          /*
            영상 상세는 준비 상태와 처리 이력을 한 화면에 보여 준다.
            라벨러는 자기 큐(`내 작업`)만 보면 되므로 주소로 들어오는 것도 막는다.
            클립 뷰어(`videos/:videoId/clips`)는 일반 멤버 라우트에 둔다.
            클립 뷰어에는 판정도 저장 경로도 없고, 클립이 어떻게 나뉘었는지는 라벨러도 봐야 한다.
          */
          ...videoDetailRoutes,
        ],
      },

      // ── 검수자+ (선택된 프로젝트) ──
      ...reviewRoutes,

      // ── 콘텐츠(영상·클립 카탈로그): 관리자 전용 ──
      ...contentRoutes,

      // 어느 라우트와도 맞지 않는 주소.
      // `errorElement`는 던져진 오류만 받으므로 404는 이 항목이 직접 그린다.
      // 이 항목이 없으면 React Router의 개발자용 오류 원문이 그대로 뜬다.
      // 주의: `notFound`를 넘겨야 한다.
      // 이 캐치올은 일반 element라서 `useRouteError()`가 `undefined`다.
      // 그래서 넘기지 않으면 없는 주소에 일시 오류 안내("다시 시도해주세요")가 뜬다.
      { path: "*", element: <RouteError notFound /> },
    ],
  },
]);
