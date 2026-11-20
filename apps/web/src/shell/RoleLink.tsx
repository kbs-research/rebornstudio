/**
 * 역할이 열 수 있는 화면일 때만 링크를 그린다.
 *
 * 목적지 라우트의 키(`route`)를 받아 `lib/roles`의 `ROUTE_ACCESS`로 판정한다.
 * 라우터 가드(`RoleGuard`)와 같은 표를 읽으므로 이 링크로 연 화면은 가드를 통과한다.
 * 열 수 없으면 아무것도 그리지 않는다. 이름은 남겨야 하는 곳은 `fallback`에 글자를 준다.
 * 열 수 없는 링크가 잠깐 보이지 않도록 계정과 프로젝트 목록이 오기 전에는 그리지 않는다.
 * 역할을 보지 않고 링크를 그리면 누른 사람이 접근 권한 없음 화면에 닿는다.
 */
import type { ReactNode } from "react";
import { Link, type LinkProps, useParams } from "react-router-dom";

import type { ProjectDTO } from "@/shared/api/client";
import { canOpenRoute, ROUTE_ACCESS, type RouteKey } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/**
 * 현재 사용자가 이 라우트를 열 수 있으면 true를 돌려준다. 버튼을 숨기거나 조회를 막을 때 쓴다.
 *
 * 프로젝트 범위 라우트는 넘긴 프로젝트, 주소의 slug, 고른 프로젝트 순서로 역할을 확인한다.
 * `RoleGuard`와 같은 순서다. 계정이나 프로젝트 목록이 아직 없으면 `false`다.
 * 조회의 `enabled`에 쓰면 서버가 403으로 거절할 요청을 보내지 않는다.
 */
export function useCanOpen(route: RouteKey, project?: ProjectDTO): boolean {
  const { user } = useAuth();
  const { slug } = useParams<{ slug?: string }>();
  const { selectedProjectId } = useSelectedProject();
  const scope = ROUTE_ACCESS[route].scope;
  const projects = useProjects({ enabled: !!user && scope === "project" && !project });
  if (!user) return false;
  const target = project
    ?? (slug
      ? projects.data?.find((p) => p.slug === slug)
      : projects.data?.find((p) => p.project_id === selectedProjectId));
  return canOpenRoute(route, user, target);
}

interface Props extends LinkProps {
  /** 목적지 라우트의 키. `lib/roles`의 `ROUTE_ACCESS` 키를 쓴다. */
  route: RouteKey;
  /** 역할을 확인할 프로젝트. 없으면 주소의 slug, 그다음 고른 프로젝트를 쓴다. */
  project?: ProjectDTO;
  /** 열 수 없을 때 대신 그릴 것. 기본은 아무것도 그리지 않는다. */
  fallback?: ReactNode;
}

/** 열 수 있는 사람에게만 링크를 그린다. 열 수 없으면 `fallback`을 그린다. */
export function RoleLink({ route, project, fallback = null, ...link }: Props) {
  const allowed = useCanOpen(route, project);
  return allowed ? <Link {...link} /> : <>{fallback}</>;
}
