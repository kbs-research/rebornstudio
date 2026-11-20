/**
 * 지금 보는 프로젝트가 활성이 아니면 본문 위에 그 사실을 알리는 줄. 셸(`app/_layout`)이 모든 화면 위에 둔다.
 *
 * 서버는 일시정지·끝난 프로젝트의 새 작업 배정을 409로 막는다(`classify.reject_if_not_claimable`).
 * 상태를 프로젝트 목록 칩에만 쓰면 개요·작업 단계·내 작업은 평소와 같아 보이고,
 * 사용자는 다음 작업을 누른 뒤에야 막힌 것을 알게 된다. 그래서 프로젝트를 보는 모든 화면에 같은 줄을 둔다.
 *
 * 프로젝트는 주소의 `/p/:slug`이고, 없으면(`/my/*`) 프로젝트 전환기에서 고른 프로젝트다.
 */
import { CirclePause } from "lucide-react";
import { useLocation } from "react-router-dom";

import { PROJECT_STATUS_LABEL, useProjects } from "@/features/projects";
import { slugFromPath } from "@/hooks/useSlugProject";
import { useSelectedProject } from "@/store/project";

/** 상태별 안내. 결과(굵게)와 그 결과가 무엇을 막는지(작게)를 나눈다. */
const HINT: Record<string, string> = {
  paused: "새 작업 배정이 멈춥니다. 보유 중인 작업은 이어서 제출할 수 있습니다.",
  completed: "새 작업을 받을 수 없습니다.",
};

/** 활성이 아닌 프로젝트를 보고 있으면 상태 줄을 그린다. 활성이면 아무것도 그리지 않는다. */
export function ProjectStatusNotice() {
  const { pathname } = useLocation();
  const slug = slugFromPath(pathname);
  const { selectedProjectId } = useSelectedProject();
  const projects = useProjects();
  const p = slug
    ? projects.data?.find((x) => x.slug === slug)
    : pathname.startsWith("/my/")
      ? projects.data?.find((x) => x.project_id === selectedProjectId)
      : undefined;
  if (!p || p.status === "active") return null;
  return (
    <div role="status" className="mb-5 flex items-start gap-2.5 rounded-card bg-warn-muted px-4 py-3">
      <CirclePause size={16} className="mt-0.5 shrink-0 text-warn-text" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-footnote font-semibold text-warn-text">
          {PROJECT_STATUS_LABEL[p.status]} 프로젝트 · {p.name}
        </p>
        <p className="mt-0.5 text-caption text-label-2">{HINT[p.status]}</p>
      </div>
    </div>
  );
}
