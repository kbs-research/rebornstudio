/**
 * 홈(`/`) 화면. 사용자의 역할에 따라 첫 화면을 정한다.
 *
 * 라벨러·검수자는 프로젝트 화면 중 `개요`만 보고, 실제 작업은 `내 작업`에서 한다.
 * 그래서 작업 역할만 가진 사용자는 그 역할의 첫 화면으로 바로 보낸다.
 * 프로젝트 목록은 그대로 열 수 있고 첫 화면만 바꾼다.
 * 관리자·super_admin은 프로젝트를 고르는 일부터 하므로 목록을 그대로 보여 준다.
 * 역할별 첫 화면은 `lib/roles`의 `landingFor`가 정한다. 접근 안내(`AccessDenied`)도 같은 표를 읽는다.
 * 라벨러·검수자는 `내 작업`이다.
 * 작업 역할만 가진 사람에게 프로젝트 목록을 먼저 보여 주면 카드 하나를 지나 다시 일하는 화면으로 가야 한다.
 */
import { useEffect } from "react";
import { Navigate } from "react-router-dom";

import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { homeLanding } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { ProjectsPage, useProjects } from "@/features/projects";

/** 프로젝트 목록을 받은 뒤, 역할에 맞는 첫 화면으로 보내거나 프로젝트 목록을 그린다. */
export function HomeRoute() {
  const { user } = useAuth();
  const { selectedProjectId, setSelectedProjectId } = useSelectedProject();
  const projects = useProjects();
  const landing = user && !projects.isLoading ? homeLanding(user, projects.data, selectedProjectId) : null;
  /*
    착지할 프로젝트를 고른 상태로 둔 뒤에 이동한다. 이동한 `내 작업`은 고른 프로젝트로 큐를 정한다.
    고른 프로젝트가 없거나 목록에 없는 이전 값이면 `프로젝트 선택 필요`에서 멈춘다.
  */
  const pick = landing?.projectId;
  useEffect(() => {
    if (pick && pick !== selectedProjectId) setSelectedProjectId(pick);
  }, [pick, selectedProjectId, setSelectedProjectId]);

  // 역할을 판정하기 전에는 어느 쪽도 그리지 않는다. 목록을 먼저 그렸다가 이동하면 화면이 깜빡인다.
  // 계정도 기다린다. 목록만 먼저 오면 최고관리자를 작업 역할로 읽어 다른 화면으로 보낼 수 있다.
  if (projects.isLoading || !user) return <PageSkeleton cards={6} />;
  if (landing) {
    // 프로젝트를 고르기 전에 이동하면 이동한 화면이 고른 것 없이 먼저 그려진다.
    if (landing.projectId !== selectedProjectId) return <PageSkeleton cards={6} />;
    return <Navigate to={landing.to} replace />;
  }
  return <ProjectsPage />;
}
