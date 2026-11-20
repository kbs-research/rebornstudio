/**
 * 인증 화면의 라우트. `app/router.tsx`가 셸(`ProtectedLayout`) 밖 맨 앞에 펼친다.
 *
 * 네 화면 모두 로그인 없이 열린다. 개인정보 안내는 첫 로그인 전에도 읽을 수 있어야 하므로 인증 셸이 링크하고 이 목록에 함께 둔다.
 */
import type { RouteObject } from "react-router-dom";

import { LoginPage } from "./pages/login";
import { PasswordResetPage } from "./pages/password_reset";
import { PrivacyPage } from "./pages/privacy";
import { SetupPage } from "./pages/setup";

/** 로그인·최초 설정·개인정보 안내·비밀번호 재설정 주소. */
export const authRoutes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  { path: "/setup", element: <SetupPage /> },
  { path: "/privacy", element: <PrivacyPage /> },
  { path: "/password-reset", element: <PasswordResetPage /> },
];
