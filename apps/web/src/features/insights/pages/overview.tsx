/**
 * 개요 화면(`/p/:slug/overview`). 프로젝트의 첫 화면이고 역할마다 다른 첫 화면을 그린다.
 *
 * 관리자에게는 `조치 필요`(반복 반려 맨 위)와 진척률, 작업 가이드(세 줄), 단계별 대기를 둔다.
 * 카드 배치는 조치 필요 행 수와 작업 가이드 유무로 고른다(`lib/action_rows`의 `overviewLayout`).
 * 카드 높이가 데이터에 따라 크게 달라서, 배치 하나로는 어느 한 열의 아래가 크게 빈다.
 * 라벨러·검수자에게는 이어서 할 작업(보유 중, 받을 수 있는 작업)과 `내 작업 열기`,
 * 펼친 작업 가이드를 둔다. 작업 결과를 볼 수 있는 검수자는 진척률을 이어서 할 작업 옆에 둔다.
 * 작업자 개요의 배치는 진척률과 작업 가이드 유무로 고른다(`lib/action_rows`의 `workerOverviewLayout`).
 *
 * 부제는 풀어 쓰는 설명 대신 이 화면에서만 알 수 있는 사실(계약 목표·마감)을 쓴다.
 * 역할 판정은 서버 가드와 같은 표(`lib/roles`)를 읽는다. 화면을 서버보다 넓게 열지 않는다.
 */
import { Plus } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { ActionNeeded, useActionRows } from "../components/ActionNeeded";
import { GuideCard, hasGuideText } from "../components/GuideCard";
import { contractFacts, ProgressCard, StageStrip, useBusinessKpi } from "../components/ManagerCards";
import { WorkCard } from "../components/WorkerCards";
import { overviewLayout, workerOverviewLayout } from "../lib/action_rows";
import { buttonClasses } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import type { ProjectDTO } from "@/shared/api/client";
import { canOpenRoute, hasProjectRole, WORK_RESULT_VIEWER_ROLES } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { ConnectionNotice, ProjectShellState, useProjects } from "@/features/projects";

/** 관리자 개요를 그린다. */
function AdminOverview({ project }: { project: ProjectDTO }) {
  const user = useAuth((s) => s.user);
  const kpi = useBusinessKpi(project.project_id);
  const canSettings = canOpenRoute("settings", user, project);
  const action = useActionRows(project);
  const layout = overviewLayout(action.status === "ready" ? action.shown.length : null, hasGuideText(project.guide_md));
  const progress = <ProgressCard project={project} />;
  const guide = (
    <GuideCard
      text={project.guide_md}
      mode="peek"
      editTo={canSettings ? `/p/${project.slug}/settings?tab=guide` : undefined}
    />
  );
  return (
    <div className="space-y-5">
      <PageHeader
        title="개요"
        subtitle={contractFacts(project)}
        actions={
          <Link to={`/p/${project.slug}/pipeline?stage=ingest`} className={buttonClasses("primary", "md")}>
            <Plus className="size-4" aria-hidden="true" />
            영상 등록
          </Link>
        }
      />
      {/* 저장소·분석 연동이 없으면 등록·분석이 막힌 사실을 첫 화면에서 먼저 알린다. */}
      <ConnectionNotice project={project} />
      {layout === "top" ? (
        <>
          <ActionNeeded state={action} />
          {/* 작업 가이드는 넓은 왼쪽 열, 진척률은 오른쪽 열에 둔다. 관리자 가이드는 세 줄만 보여서 두 열의 높이가 비슷하다. */}
          <div className="grid items-start gap-5 lg:grid-cols-[1.6fr_1fr]">
            {guide}
            {progress}
          </div>
        </>
      ) : (
        <div className="grid items-start gap-5 lg:grid-cols-[1.6fr_1fr]">
          {layout === "left" ? (
            <>
              <div className="space-y-5">
                <ActionNeeded state={action} />
                {guide}
              </div>
              {progress}
            </>
          ) : (
            <>
              <ActionNeeded state={action} />
              <div className="space-y-5">
                {progress}
                {guide}
              </div>
            </>
          )}
        </div>
      )}
      <StageStrip project={project} deliverable={kpi.data?.deliverable?.count ?? null} />
    </div>
  );
}

/** 라벨러·검수자 개요를 그린다. 이어서 할 작업을 가장 크게 둔다. */
function WorkerOverview({ project, kind }: { project: ProjectDTO; kind: "label" | "review" }) {
  const user = useAuth((s) => s.user);
  const showProgress = kind === "review" && hasProjectRole(user, project, WORK_RESULT_VIEWER_ROLES);
  const layout = workerOverviewLayout(showProgress, hasGuideText(project.guide_md));
  const work = <WorkCard project={project} kind={kind} />;
  const guide = <GuideCard text={project.guide_md} mode="read" />;
  const progress = <ProgressCard project={project} compact />;
  return (
    <div className="space-y-5">
      <PageHeader title="개요" />
      {layout === "single" ? (
        <>
          {work}
          {guide}
        </>
      ) : layout === "top" ? (
        <>
          {/* 펼친 가이드는 길이가 일정하지 않아 진척률 옆에 두지 않고 아래 전체 폭에 둔다. */}
          <div className="grid items-start gap-5 lg:grid-cols-[1.6fr_1fr]">
            {work}
            {progress}
          </div>
          {guide}
        </>
      ) : (
        <div className="grid items-start gap-5 lg:grid-cols-[1.6fr_1fr]">
          <div className="space-y-5">
            {work}
            {guide}
          </div>
          {progress}
        </div>
      )}
    </div>
  );
}

/** 역할에 따라 개요를 고른다. */
export function OverviewPage() {
  const { slug } = useParams<{ slug: string }>();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  const user = useAuth((s) => s.user);

  if (projects.isLoading) return <PageSkeleton header stats panels={2} />;
  if (!project) return <ProjectShellState isError={projects.isError} onRetry={() => void projects.refetch()} />;

  const role = project.my_role;
  if (user?.global_role === "super_admin" || role === "project_admin") return <AdminOverview project={project} />;
  if (role === "reviewer") return <WorkerOverview project={project} kind="review" />;
  return <WorkerOverview project={project} kind="label" />;
}
