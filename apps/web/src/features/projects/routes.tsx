/**
 * 프로젝트 목록·생성·설정 화면의 라우트.
 *
 * 세 화면의 가드가 달라 배열이 셋이다. 목록은 일반 멤버 라우트에, 생성은 생성 가드(`projectNew`)에,
 * 설정은 설정 가드(`settings`) 묶음 안에 들어간다. 앱 라우트 표(`app/router.tsx`)가 각 배열을 해당 가드 아래에 펼친다.
 */
import type { RouteObject } from "react-router-dom";

import { RoleGuard } from "@/shell/RoleGuard";

import { ProjectNewPage } from "./pages/project_new";
import { ProjectSettingsPage } from "./pages/project_settings";
import { ProjectsPage } from "./pages/projects";

/** 프로젝트 목록. 홈이 작업 화면으로 보내더라도 프로젝트를 고를 수 있게 주소를 남긴다. */
export const projectRoutes: RouteObject[] = [
  { path: "projects", element: <ProjectsPage /> },
];

/** 프로젝트 생성과 그 가드(super_admin). */
export const projectNewRoutes: RouteObject[] = [
  {
    element: <RoleGuard route="projectNew" />,
    children: [{ path: "projects/new", element: <ProjectNewPage /> }],
  },
];

/** 설정 가드(`settings`) 묶음 안에 펼치는 프로젝트 설정. */
export const projectSettingsRoutes: RouteObject[] = [
  { path: "p/:slug/settings", element: <ProjectSettingsPage /> },
];
