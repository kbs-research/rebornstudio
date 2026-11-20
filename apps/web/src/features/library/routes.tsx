/**
 * 콘텐츠(영상·클립 카탈로그)의 라우트.
 *
 * 세 화면의 가드가 달라 배열이 셋이다. 클립 보기는 일반 멤버 라우트에, 영상 상세는 산출물을 보는 역할의 가드
 * (`workResult`) 묶음에, 콘텐츠 목록은 콘텐츠 가드(`content`) 묶음에 들어간다.
 * 앱 라우터(`app/router.tsx`)가 각 배열을 해당 묶음 안에 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { RoleGuard } from "@/shell/RoleGuard";

import { ContentPage } from "./pages/content";
import { VideoClipsPage } from "./pages/video_clips";
import { VideoDetailPage } from "./pages/video_detail";

/** 클립 보기. 판정 기능이 없어 라벨러를 포함한 프로젝트 멤버 누구나 연다. */
export const videoClipsRoutes: RouteObject[] = [
  { path: "p/:slug/videos/:videoId/clips", element: <VideoClipsPage /> },
];

/** 영상 상세. 산출물을 보는 역할의 가드(`workResult`) 안, 완료 목록·상세 뒤에 펼친다. */
export const videoDetailRoutes: RouteObject[] = [
  { path: "p/:slug/videos/:videoId", element: <VideoDetailPage /> },
];

/** 콘텐츠 목록(영상·클립 탭). 관리자만 연다. */
export const contentRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="content" />,
    children: [
      { path: "p/:slug/content", element: <ContentPage /> },
    ],
  },
];
