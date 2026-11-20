/**
 * slug가 없는 전역 주소를 선택한 프로젝트의 화면으로 보내는 리다이렉트.
 * 전역 /review는 `/p/:slug/pipeline?stage=…`로 보낸다.
 */
import { Navigate } from "react-router-dom";

import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

/**
 * slug가 없는 전역 큐 주소를 선택한 프로젝트의 파이프라인 화면으로 보낸다.
 *
 * 선택한 프로젝트가 없으면(북마크로 `/review`에 바로 온 경우) 프로젝트 목록으로 보낸다.
 * 프로젝트 목록을 받아 오는 동안에만 빈 화면을 둔다.
 * 그 밖의 경우에 `null`을 반환하면 본문이 빈 채로 멈춘다.
 * 라우트 가드가 최고관리자를 프로젝트 없이도 통과시키므로 최고관리자에게 특히 자주 생긴다.
 */
export function ToPipeline({ stage }: { stage: string }) {
  const { selectedProjectId } = useSelectedProject();
  const { data, isLoading } = useProjects();
  const slug = data?.find((p) => p.project_id === selectedProjectId)?.slug;
  if (!slug) return isLoading ? null : <Navigate to="/projects" replace />;
  return <Navigate to={`/p/${slug}/pipeline?stage=${stage}`} replace />;
}
