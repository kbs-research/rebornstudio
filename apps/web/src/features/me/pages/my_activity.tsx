/**
 * `내 활동`(`/my/activity`) 화면. 본인이 한 일의 기록을 보여 준다.
 *
 * 본문은 `components/MyActivity.tsx`의 `ActivityBody`다.
 * 라벨러·관리자는 라벨링 기록 표를 보고, 검수자는 본인 판정 목록을 본다.
 */
import { PersonalHeader } from "@/shell/PersonalHeader";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { requestProjectSwitcher, useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

import { ActivityBody } from "../components/MyActivity";

/** 선택한 프로젝트에서 현재 사용자의 역할에 맞는 활동 기록을 그린다. */
export function MyActivityPage() {
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === selectedProjectId);
  const role = project?.my_role ?? null;

  return (
    <div className="space-y-4">
      <PersonalHeader />
      {/* 프로젝트 목록을 받아야 역할을 알 수 있다. 역할을 확인하기 전에 그리면 검수자에게
          라벨러 기록 요청이 나간다. 받는 동안은 골격만 그리고 요청을 보내지 않는다. */}
      {projects.isError ? (
        <ErrorState message="프로젝트 조회 실패" onRetry={() => void projects.refetch()} />
      ) : !selectedProjectId ? (
        <EmptyState
          message="프로젝트 선택 필요"
          description="프로젝트를 고르면 그 프로젝트의 내 활동이 보입니다."
          action={<Button variant="secondary" onClick={requestProjectSwitcher}>프로젝트 고르기</Button>}
        />
      ) : projects.isLoading ? (
        <PageSkeleton table />
      ) : !project ? (
        <EmptyState
          message="프로젝트 선택 필요"
          description="고른 프로젝트가 참여 목록에 없습니다."
          action={<Button variant="secondary" onClick={requestProjectSwitcher}>프로젝트 고르기</Button>}
        />
      ) : (
        <ActivityBody projectId={project.project_id} role={role} />
      )}
    </div>
  );
}
