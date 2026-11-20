/**
 * 작업 단계 기능의 라우트. `app/router.tsx`가 셸(`ProtectedLayout`) 아래 해당 가드 묶음에서 펼친다.
 *
 * 가드가 다른 묶음 셋(품질검수·작업 단계·검수)과 산출물 묶음 안의 완료 클립 상세 하나라서 배열이 넷이다.
 * 가드 목록은 `lib/roles.ts`의 `ROUTE_ACCESS`에 있다. 전역 `/review`의 리다이렉트는 도착 화면과 같은 가드 묶음에 둔다.
 * 리다이렉트의 가드가 도착 화면보다 좁으면 열 수 있는 화면으로 이동하지 못한다.
 */
import type { RouteObject } from "react-router-dom";

import { ToPipeline } from "@/app/redirects";
import { RoleGuard } from "@/shell/RoleGuard";

import { CompletedDetailPage } from "./pages/CompletedDetailPage";
import { PipelinePage } from "./pages/pipeline";
import { QcPage } from "./pages/qc";
import { ReviewPage } from "./pages/review";

/** 캡션 품질검수 화면. 서버 라우트와 같은 권한(관리자)으로 연다. */
export const qcRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="qc" />,
    children: [
      // 캡션 품질검수 화면. 게이트가 완료 목록에서 뺀 보류 건을 여기서 본다.
      { path: "p/:slug/qc", element: <QcPage /> },
    ],
  },
];

/**
 * 작업 단계 화면. 관리자와 검수자가 들어온다.
 *
 * 라벨러는 자기 큐(`내 작업`)만 보므로 주소로 직접 들어오는 것도 막는다.
 * 어느 단계를 보일지는 `visibleStages`가 정한다.
 */
export const pipelineRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="pipeline" />,
    children: [
      { path: "p/:slug/pipeline", element: <PipelinePage /> },
    ],
  },
];

/**
 * 완료 클립 하나를 읽기 전용으로 여는 상세. 산출물 가드(`workResult`) 묶음 안에 펼친다.
 *
 * 목록(완료 워크벤치)과 같은 가드를 써서 목록에서 열 수 있는 클립만 열린다.
 */
export const completedDetailRoutes: RouteObject[] = [
  { path: "p/:slug/completed/:workItemId", element: <CompletedDetailPage /> },
];

/** 검수 워크벤치와 옛 전역 검수 주소. 검수자 이상이 연다. */
export const reviewRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="review" />,
    children: [
      { path: "review", element: <ToPipeline stage="review" /> },
      { path: "review/:id", element: <ReviewPage /> },
    ],
  },
];
