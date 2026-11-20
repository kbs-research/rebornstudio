/**
 * 플랫폼 관리 화면의 라우트 배열. `app/router.tsx`가 플랫폼 가드(`platform`) 묶음 안에 펼친다.
 *
 * 앱 라우터가 플랫폼 묶음 안의 두 위치에 나눠 펼치므로 배열이 둘이다.
 */
import type { RouteObject } from "react-router-dom";

import { AdminGlobalPage } from "./pages/admin_global";
import { AdminUsersPage } from "./pages/admin_users";
import { ConnectionsPage } from "./pages/connections";

/** 사용자와 개요. */
export const platformRoutes: RouteObject[] = [
  { path: "admin/users", element: <AdminUsersPage /> },
  { path: "admin", element: <AdminGlobalPage /> },
];

/** 자격증명 연결. */
export const platformOpsRoutes: RouteObject[] = [
  { path: "admin/connections", element: <ConnectionsPage /> },
];
