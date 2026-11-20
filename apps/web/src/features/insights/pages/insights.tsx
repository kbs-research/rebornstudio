/**
 * 통계 화면(`/p/:slug/insights`). 기간 합계 칸과 사람별 실적 표를 둔다.
 *
 * 고용된 라벨러를 운영하는 관리자가 프로젝트 전체의 처리량·승인율·반려율·수정률을 보고, 여러 사람을 같은 기준으로
 * 나란히 보고 정렬하는 화면이다. 진척·대기처럼 지금 할 일을 정하는 수는 개요에 있고, 보유 중·최근 활동은 멤버 화면에 있다.
 * 기간 선택은 페이지 머리에 하나만 둔다. 합계 칸과 사람별 표가 같은 기간(`?period=`)을 읽어서 위아래 수가 같은 기간을 가리킨다.
 * 사람별 실적 API가 관리자 전용이라 이 화면도 관리자만 연다(`INSIGHTS_ROLES`).
 */
import { useParams } from "react-router-dom";

import { PageHeader } from "@/shared/ui/PageHeader";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { PeriodSelect, usePeriod } from "@/shared/ui/PeriodSelect";
import { useProjects } from "@/features/projects";

import { LabelerPerformancePanel } from "../components/LabelerPerformancePanel";
import { PeriodSummary } from "../components/PeriodSummary";

/** 기간 합계 칸과 사람별 실적 표를 그린다. */
export function InsightsPage() {
  const { slug } = useParams<{ slug: string }>();
  const [period, setPeriod] = usePeriod();
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);

  /*
    목록을 불러오지 못한 경우, 불러오는 중인 경우, 없는 프로젝트인 경우를 구분한다.
    한 조건으로 처리하면 목록 조회가 실패했을 때 화면이 불러오는 중에 멈춰 서버 장애가 기다리면 될 일처럼 보인다.
  */
  if (projects.isError && projects.data == null) {
    return (
      <ErrorState
        message="통계 조회 실패"
        description="잠시 후 다시 시도해주세요."
        onRetry={() => void projects.refetch()}
      />
    );
  }
  if (projects.isLoading || projects.data == null) return <PageSkeleton table />;
  if (!project) {
    return <EmptyState message="프로젝트 없음" description="주소를 다시 확인해주세요." />;
  }

  return (
    <div className="space-y-6">
      {/* 이 기간이 합계 칸과 사람별 표의 모든 수에 적용된다. */}
      <PageHeader title="통계" actions={<PeriodSelect value={period} onChange={setPeriod} />} />
      <PeriodSummary pid={project.project_id} />
      <LabelerPerformancePanel />
    </div>
  );
}
