/**
 * 완료 클립 상세 화면(`/p/:slug/completed/:workItemId`).
 * 좁은 폭에서 완료 클립을 보는 화면이자 북마크 대상이다.
 *
 * 내용은 `CompletedClipView` 하나를 쓴다.
 * 이 파일은 라우트 파라미터를 읽고 돌아가는 경로와 역할에 맞는 이동 링크를 넘기는 일만 한다.
 *
 * 완료 워크벤치(검색 레일·무대·정보 레일의 3단)는 375px 폭에 둘 수 없고, 이 화면은 그 폭에서 세로로 접힌다.
 * 그래서 완료 워크벤치로 리다이렉트하지 않는다. 리다이렉트하면 좁은 폭에서 쓸 화면과 북마크가 없어진다.
 */
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { hasProjectRole } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";

import { fetchCompletedDetail, pipelineKeys } from "../api";
import { CompletedClipView } from "../components/CompletedClipView";

/**
 * 라우트 파라미터로 완료 클립 상세를 그린다. 돌아가는 경로는 완료 단계 목록이다.
 *
 * 영상 상세로 가는 버튼은 영상 메타를 고칠 수 있는 관리자에게만 준다.
 */
export function CompletedDetailPage() {
  const { slug, workItemId } = useParams<{ slug: string; workItemId: string }>();
  const { user } = useAuth();
  const { selectedProjectId: pid } = useSelectedProject();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === pid);
  // 본문과 같은 키라 캐시를 나눠 쓴다. 영상 상세 주소에 쓸 영상 id만 읽는다.
  const detail = useQuery({
    queryKey: pipelineKeys.completedDetail(pid, workItemId),
    queryFn: () => fetchCompletedDetail(pid, workItemId),
    enabled: !!pid && !!workItemId,
    // 실패는 본문(`CompletedClipView`)이 표시한다. 같은 키라 이 조회에서도 전역 토스트를 끈다.
    meta: { handled: true },
  });
  if (!workItemId) return null;
  const canFixVideo = hasProjectRole(user, project, ["project_admin"]);
  const videoId = detail.data?.row.video_id;
  return (
    <CompletedClipView
      workItemId={workItemId}
      backTo={`/p/${slug}/pipeline?stage=done`}
      videoHref={canFixVideo && videoId ? `/p/${slug}/videos/${videoId}` : null}
      qcHref={canFixVideo ? `/p/${slug}/qc` : null}
    />
  );
}
