/**
 * 멤버 화면의 라우트. 앱 라우트 표(`app/router.tsx`)가 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { RoleGuard } from "@/shell/RoleGuard";

import { WorkforcePage } from "./pages/people";

/** 관리 가드(`people`) 아래의 멤버 화면. */
export const peopleRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="people" />,
    children: [
      { path: "p/:slug/people", element: <WorkforcePage /> },
    ],
  },
];
