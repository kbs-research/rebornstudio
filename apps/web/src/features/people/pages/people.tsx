/**
 * 멤버 화면(`/p/:slug/people`).
 *
 * 머리(제목·사실 한 줄·멤버 초대)만 이 파일이 그리고 본문은 `components/MembersPanel`이 그린다.
 * 표는 읽기 전용이고 편집은 사람 서랍의 설정 탭 한 곳에서 한다.
 *
 * 도구는 작업자에게 등급을 매기지 않는다. 작업자별 수치는 통계 화면에서 보고 사람이 판단한다.
 */
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { ProjectShellState, useProjects } from "@/features/projects";

import { listMembers, peopleKeys } from "../api";
import { memberFacts, MembersPanel } from "../components/MembersPanel";

/** 멤버 화면의 머리와 본문을 그린다. */
export function WorkforcePage() {
  const { slug } = useParams<{ slug: string }>();
  const [inviteOpen, setInviteOpen] = useState(false);
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  // 머리 사실 줄의 수다. 본문 표와 같은 쿼리 키라 요청은 한 번이다.
  const members = useQuery({
    queryKey: peopleKeys.members(project?.project_id),
    queryFn: () => listMembers(project!.project_id),
    enabled: !!project,
  });

  if (projects.isLoading) return <PageSkeleton table />;
  if (!project) return <ProjectShellState isError={projects.isError} onRetry={() => void projects.refetch()} />;

  return (
    <div className="space-y-5">
      {/* 제목은 `멤버`다. 이 프로젝트에 속한 사람이고, 플랫폼의 `사용자` 페이지는 계정을 다룬다.
          부제는 풀어 쓰는 설명 대신 이 화면에서만 알 수 있는 사실(상태별 수)이다. */}
      <PageHeader
        title="멤버"
        subtitle={members.data ? memberFacts(members.data) : undefined}
        actions={
          <Button onClick={() => setInviteOpen(true)}>
            <Plus className="size-4" aria-hidden="true" />
            멤버 초대
          </Button>
        }
      />
      <MembersPanel
        pid={project.project_id}
        inviteOpen={inviteOpen}
        onInviteOpenChange={setInviteOpen}
      />
    </div>
  );
}
