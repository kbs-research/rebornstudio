/**
 * 개인 작업공간(홈·`내 작업`·`내 활동`·`내 계정`)의 라우트.
 *
 * 가드를 두지 않는다. 네 화면은 모든 멤버가 열고, 역할에 맞지 않으면 각 화면이 알맞은 곳으로 보낸다.
 * 홈은 일반 멤버 라우트의 맨 앞에, 나머지 셋은 개요 뒤에 있어 배열이 둘이다. 앱 라우트 표(`app/router.tsx`)가 각 위치에 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { AccountPage } from "./pages/account";
import { HomeRoute } from "./pages/home";
import { MyWorkPage } from "./pages/inbox";
import { MyActivityPage } from "./pages/my_activity";

/** 홈(`/`). 역할에 맞는 첫 화면으로 보내거나 프로젝트 목록을 그린다. */
export const meHomeRoutes: RouteObject[] = [{ index: true, element: <HomeRoute /> }];

/** 개인 탭 둘(`내 작업`·`내 활동`)과 `내 계정`. */
export const meRoutes: RouteObject[] = [
  { path: "my/work", element: <MyWorkPage /> },
  { path: "my/activity", element: <MyActivityPage /> },
  { path: "account", element: <AccountPage /> },
];
