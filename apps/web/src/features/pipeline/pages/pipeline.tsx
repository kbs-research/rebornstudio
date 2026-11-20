/**
 * 작업 단계 화면(`/p/:slug/pipeline?stage=…`).
 * 단계 하나를 그리고, 전체 단계를 스텝퍼로 보여 준다.
 *
 * 단계는 작업 단위로 나눈다.
 * 영상 기준 작업(등록·분석)은 `영상 등록` 워크스페이스 하나로 묶는다.
 * 클립 기준 작업(라벨링·검수)과 완료만 별도 단계로 둔다.
 *
 * 각 단계는 작업자의 `내 작업`과 같은 작업 목록 컴포넌트를 쓴다.
 * 관리자는 수행 권한이 있는 역할의 일을 같은 표와 기능으로 직접 처리한다.
 */
import { Fragment, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ChevronRight } from "lucide-react";

import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Tabs } from "@/shared/ui/Tabs";
import type { PipelineSummary } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { STAGE_LABEL } from "@/shell/nav-config";
import { landingStage, type PipelineStage, visibleStages } from "@/lib/roles";

import { IngestPage } from "@/features/ingest";
import { useProjects } from "@/features/projects";

import { fetchPipelineSummary, pipelineKeys } from "../api";
import { DonePanel } from "../components/DonePanel";
import { HeaderSlotProvider } from "../components/HeaderActions";
import { DeidentReportsCard } from "../components/DeidentReportsCard";
import { RepeatedRejectsCard } from "../components/RepeatedRejectsCard";
import { ReviewInboxPanel } from "../components/ReviewInboxPanel";
import { WorkPanel } from "../components/WorkPanel";
import { stageCellCount, stageInProgress } from "../lib/pipeline_counts";

type Stage = PipelineStage;

/**
 * 단계별 표시 정보. `unit`은 스텝퍼 셀에서 수 옆에 붙는 이름이고, `badge`는 그 셀의 접근 이름이다.
 *
 * 단계마다 세는 대상이 다르다. 등록은 영상, 라벨링·검수는 작업, 완료는 클립을 센다.
 * 그래서 수 옆에 항상 단위를 붙인다. 화면의 모든 수는 분모를 밝힌다.
 * 수만 두면 클립 51개가 영상 한 편에 몰릴 때 배지 `51` 옆 패널이 `대기 영상 1`을 보여 모순으로 읽힌다.
 *
 * 완료 셀은 대기 수 대신 쌓인 완료 클립 수를 쓴다(`completed_total`, 완료 목록과 같은 정의).
 */
const STAGE_META: Record<Stage, { label: string; unit?: string; badge?: (n: string) => string }> = {
  ingest: { label: STAGE_LABEL.ingest, unit: "대기 영상", badge: (n) => `대기 영상 ${n}개` },
  label: { label: STAGE_LABEL.label, unit: "대기 작업", badge: (n) => `대기 작업 ${n}건` },
  review: { label: STAGE_LABEL.review, unit: "대기 작업", badge: (n) => `대기 작업 ${n}건` },
  done: { label: STAGE_LABEL.done, unit: "완료 클립", badge: (n) => `완료 클립 ${n}개` },
};

/** 활성 단계에 맞는 패널을 그린다. */
function StageContent({ stage }: { stage: Stage }) {
  if (stage === "ingest") return <IngestPage embedded />;
  if (stage === "label") {
    return (
      <div className="space-y-4">
        <WorkPanel embedded />
        <DeidentReportsCard />
        <RepeatedRejectsCard />
      </div>
    );
  }
  if (stage === "review") return <ReviewInboxPanel embedded />;
  return <DonePanel />;
}

/** 역할에 맞는 단계를 고르고, 스텝퍼와 활성 단계의 패널을 그린다. */
export function PipelinePage() {
  const { user } = useAuth();
  const { selectedProjectId } = useSelectedProject();
  const [params, setParams] = useSearchParams();

  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === selectedProjectId);
  const isSuper = user?.global_role === "super_admin";
  const isManager = isSuper || project?.my_role === "project_admin";
  // 보이는 단계와 처음 여는 단계는 `lib/roles`의 `visibleStages`·`landingStage`가 정한다.
  // 판정은 한 곳에만 둔다. 역할별 값은 `lib/roles.test.ts`가 고정한다.
  // 라벨러는 라우터 가드가 이 페이지를 막으므로 표에 없다.
  const stages = visibleStages(project?.my_role ?? null, isSuper);

  // 단계별 대기 건수. 영상 기준 단계(변환·분석)까지 포함한 단일 출처다.
  // 관리자만 부른다.
  const summary = useQuery({
    queryKey: pipelineKeys.summary(project?.project_id),
    queryFn: () => fetchPipelineSummary(project!.project_id),
    enabled: !!project && isManager,
    refetchInterval: 60_000,
  });

  // 단계는 사이드바 링크(?stage=)가 정한다. 값이 없으면 역할별 첫 단계를 연다.
  const raw = params.get("stage") ?? "";
  const requested = raw as Stage;
  const active: Stage | undefined = stages.includes(requested)
    ? requested
    : landingStage(project?.my_role ?? null, stages);
  // 역할에 없는 단계로 들어오면(예: 검수자가 `?stage=label`을 열었을 때) 주소를 실제로 연 단계로 바꾼다.
  // 바꾸지 않으면 패널은 검수인데 브레드크럼·주소는 라벨링을 가리킨다.
  // 잘못 연 주소를 고치는 것이라 replace로 바꾸고 이력에 남기지 않는다.
  useEffect(() => {
    if (stages.length > 0 && active && raw && requested !== active) {
      setParams({ stage: active }, { replace: true });
    }
  }, [stages.length, active, raw, requested, setParams]);

  const meta = active ? STAGE_META[active] : null;
  /** 머리 오른쪽 영역 요소. 활성 패널이 `HeaderActions`로 갱신 시각과 주 버튼을 여기에 그린다. */
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  /*
    수를 받은 역할(관리자)만 셀 스텝퍼를 그린다. 수 없는 셀은 이름만 남아 탭과 같아진다.
    수를 받지 못하는 역할(`pipeline/summary`가 403)은 단계가 둘 이상이면 밑줄 탭으로 이동하고,
    하나뿐이면 제목이 곧 단계 이름이다.
  */
  const counted = isManager && stages.length > 1;
  /*
    단계 전환은 사이드바의 단계 링크처럼 이력에 한 항목을 쌓는다.
    replace로 바꾸면 지금 단계의 보기(`?view=triage` 등)가 이력에서 지워져, 뒤로 가기가 그 단계의 기본 보기로 돌아온다.
  */
  const pickStage = (s: Stage) => setParams({ stage: s });
  const title = stages.length === 1 && meta ? meta.label : "작업 단계";

  return (
    <HeaderSlotProvider value={{ inside: true, el: slot }}>
    <div className="space-y-5">
      <PageHeader title={title} actions={<div ref={setSlot} className="flex items-center gap-2" />} />

      {counted ? (
        <Stepper
          stages={stages}
          active={active}
          summary={summary.data}
          onPick={pickStage}
        />
      ) : stages.length > 1 && active ? (
        <Tabs
          variant="underline"
          tabs={stages.map((s) => ({ key: s, label: STAGE_META[s].label }))}
          value={active}
          onChange={(s) => pickStage(s as Stage)}
        />
      ) : null}

      {stages.length === 0 ? (
        <EmptyState message="작업 단계 접근 권한 없음" />
      ) : (
        active && <StageContent stage={active} />
      )}
    </div>
    </HeaderSlotProvider>
  );
}

/**
 * 공정 스텝퍼를 그린다. 셀마다 단계 이름, 대기 수, 단위를 함께 적는다.
 *
 * 활성 셀만 흰 면과 호박색 밑줄로 둔다. 순서는 셀 사이의 꺾쇠가 보여 준다.
 * 단계 번호는 그리지 않는다. 역할마다 보이는 단계가 달라 같은 단계가 사람마다 다른 번호가 된다.
 * 좁은 폭에서는 띠 안에서 가로로 스크롤한다. 셀이 `shrink-0`이라 줄지 않는다.
 */
function Stepper({
  stages, active, summary, onPick,
}: {
  stages: Stage[];
  active: Stage | undefined;
  summary: PipelineSummary | undefined;
  onPick: (s: Stage) => void;
}) {
  return (
    <nav
      aria-label="작업 단계"
      className="flex items-stretch overflow-x-auto rounded-card border border-separator bg-canvas-2 p-1"
    >
      {stages.map((s, i) => {
        const on = s === active;
        const count = stageCellCount(summary, s);
        const m = STAGE_META[s];
        const n = count?.toLocaleString("ko-KR");
        // 대기 수와 겹치지 않는 진행 중인 수. 반입 화면의 `준비 중`과 같은 낱말·같은 수다.
        // 규격 검증·변환·비식별화·재생본 확정이 모두 이 수에 들어가므로 `변환 중`이라 부르지 않는다.
        const busy = stageInProgress(summary, s)?.toLocaleString("ko-KR");
        return (
          <Fragment key={s}>
            {i > 0 && (
              <span className="flex shrink-0 items-center px-0.5 text-label-4" aria-hidden="true">
                <ChevronRight size={14} />
              </span>
            )}
            <button
              type="button"
              onClick={() => onPick(s)}
              aria-current={on ? "page" : undefined}
              aria-label={
                n != null && m.badge
                  ? [m.label, m.badge(n), busy != null ? `준비 중 ${busy}개` : null].filter(Boolean).join(", ")
                  : undefined
              }
              className={cn(
                "relative flex min-w-32 shrink-0 flex-col items-start gap-0.5 rounded-control px-3.5 pb-3 pt-2.5 text-left transition-colors duration-short",
                on ? "bg-panel shadow-e1" : "hover:bg-panel/60",
              )}
            >
              <span className={cn("text-footnote", on ? "font-semibold text-label" : "font-medium text-label-2")}>
                {m.label}
              </span>
              {/* 수를 아직 받지 못했으면 0을 그리지 않고 공간만 둔다. */}
              {n != null && m.unit ? (
                <span className="flex items-baseline gap-1.5">
                  <span className={cn("text-headline font-bold tabular-nums", on ? "text-label" : "text-label-2")}>{n}</span>
                  <span className="text-caption text-label-3">{m.unit}</span>
                </span>
              ) : (
                <span className="h-5.5" aria-hidden="true" />
              )}
              {busy != null && (
                <span className="text-caption tabular-nums text-label-3">준비 중 {busy}</span>
              )}
              {on && <span className="absolute inset-x-3.5 bottom-0 h-0.5 rounded-t-mark bg-amber" aria-hidden="true" />}
            </button>
          </Fragment>
        );
      })}
    </nav>
  );
}
