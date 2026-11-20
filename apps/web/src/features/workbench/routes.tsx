/**
 * 라벨링 워크벤치와 완료 워크벤치의 라우트.
 *
 * 두 화면의 가드가 달라 배열이 둘이다. 라벨링 워크벤치는 일반 멤버 라우트에 두고 권한은 서버 보유 판정이 정한다.
 * 완료 워크벤치는 산출물 열람 가드(`workResult`) 묶음 안에서 완료 상세·영상 상세와 같은 가드를 쓴다.
 * 앱 라우트 표(`app/router.tsx`)가 각 배열을 해당 가드 묶음 안에 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { LabelWorkbench } from "./components/LabelWorkbench";
import { CompletedWorkbenchPage } from "./pages/completed_workbench";

/** 라벨링 워크벤치(`/work/:id/label`). 일반 멤버 라우트에 펼친다. */
export const workbenchRoutes: RouteObject[] = [
  { path: "work/:id/label", element: <LabelWorkbench /> },
];

/** 완료 워크벤치(`/p/:slug/completed`). 산출물 열람 가드 묶음 안에 펼친다. */
export const completedWorkbenchRoutes: RouteObject[] = [
  { path: "p/:slug/completed", element: <CompletedWorkbenchPage /> },
];
