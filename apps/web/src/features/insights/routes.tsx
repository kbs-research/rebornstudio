/**
 * 개요·통계 화면의 라우트.
 *
 * 두 화면의 가드가 달라 배열이 둘이다. 개요는 일반 멤버 라우트에, 통계는 통계 가드(`insights`) 묶음에 들어간다.
 * 앱 라우트 표(`app/router.tsx`)가 각 배열을 해당 가드 아래에 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { RoleGuard } from "@/shell/RoleGuard";

import { InsightsPage } from "./pages/insights";
import { OverviewPage } from "./pages/overview";

/** 프로젝트 개요. 멤버 누구나 연다. */
export const overviewRoutes: RouteObject[] = [
  { path: "p/:slug/overview", element: <OverviewPage /> },
];

/** 통계 가드(`insights`) 아래의 통계 화면. */
export const insightsRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="insights" />,
    children: [
      { path: "p/:slug/insights", element: <InsightsPage /> },
    ],
  },
];
