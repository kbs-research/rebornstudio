/**
 * 개요의 진척률과 단계별 대기 카드. 진척률은 관리자와 작업 결과를 보는 검수자가, 단계별 대기는 관리자가 본다.
 *
 * 진척률의 큰 수와 기준은 앱 공용 판정(`lib/progress.progressView`)을 쓴다.
 * 관리자 개요와 검수자 개요가 같은 이름으로 다른 수를 보여 주지 않게 한다.
 * 단계별 대기는 작업 단계 화면과 같은 원천(`pipeline/summary`)과 같은 규칙(`stageCellCount`)이다. 막대 없이 수의 크기로
 * 병목을 읽게 하고, 영상·클립(개)과 작업(건)의 단위를 섞지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { fmtDay } from "../lib/dates";
import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Skeleton } from "@/shared/ui/Skeleton";
import { type ProjectDTO } from "@/shared/api/client";
import { fetchPipelineSummary, pipelineKeys, stageCellCount } from "@/features/pipeline";
import { fmtH } from "@/shared/lib/fmtSecs";
import { PROGRESS_LABEL, progressView } from "@/lib/progress";
import { STAGE_ROLES } from "@/lib/roles";
import { STAGE_LABEL } from "@/shell/nav-config";
import { useAuth } from "@/store/auth";

import { fetchBusinessKpi, fetchCapacity, fetchWorkflowHealth, insightsKeys } from "../api";

/** 사업 KPI 조회. 개요의 카드들이 같은 쿼리 키로 나눠 쓴다. */
export function useBusinessKpi(pid: string | undefined, enabled = true) {
  return useQuery({
    queryKey: insightsKeys.businessKpi(pid),
    queryFn: () => fetchBusinessKpi(pid!),
    enabled: !!pid && enabled,
  });
}

/** 카드 머리를 그린다. 제목, 옅은 부연, 오른쪽 링크를 한 줄에 둔다. */
export function CardHead({ title, note, right, id }: { title: string; note?: ReactNode; right?: ReactNode; id?: string }) {
  return (
    <div className="flex items-baseline gap-2 px-5 pb-3 pt-4">
      <h2 id={id} className="text-headline text-label">{title}</h2>
      {note != null && <span className="text-caption text-label-3">{note}</span>}
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

/** 카드 머리 오른쪽의 이동 링크를 그린다. 목적지 이름과 꺾쇠 하나로 쓴다. */
export function MoreLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-0.5 rounded-mark text-footnote font-semibold text-label hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      {children}
      <ChevronRight className="size-3.5" aria-hidden="true" />
    </Link>
  );
}

/**
 * 세 값 막대를 그린다. 승인, 등록된 분량(또는 가공 중), 목표를 같은 축 위에 둔다.
 */
export function HoursBar({ done, held, scale }: { done: number; held: number; scale: number }) {
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / (scale || 1)) * 100))}%`;
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-capsule bg-canvas-2" aria-hidden="true">
      <div className="absolute inset-y-0 left-0 bg-separator-strong" style={{ width: pct(held) }} />
      <div className="absolute inset-y-0 left-0 bg-label" style={{ width: pct(done) }} />
    </div>
  );
}

/** 막대 범례 하나를 그린다. */
export function Legend({ tone, children }: { tone: "done" | "held" | "target"; children: ReactNode }) {
  const dot =
    tone === "done" ? "bg-label" : tone === "held" ? "bg-separator-strong" : "border border-separator-strong bg-panel";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`size-2 rounded-mark ${dot}`} aria-hidden="true" />
      {children}
    </span>
  );
}

/**
 * 진척률 카드를 그린다. 큰 수 하나, 세 값 막대, 아래 건수 기준 줄로 그린다.
 *
 * Args:
 *   compact: 검수자 개요처럼 옆 열에 작게 두면 true. 건수 줄을 뺀다.
 */
export function ProgressCard({
  project,
  compact = false,
}: {
  project: ProjectDTO;
  compact?: boolean;
}) {
  const kpi = useBusinessKpi(project.project_id);
  const d = kpi.data;
  const v = progressView(d);
  const hp = d?.hours_progress ?? null;

  return (
    <Card as="section" aria-labelledby="progress-title">
      <CardHead id="progress-title" title={PROGRESS_LABEL} />
      <div className="px-5 pb-5">
        {kpi.isError && d == null ? (
          <ErrorState inline message="진척률 조회 실패" onRetry={() => void kpi.refetch()} />
        ) : d == null ? (
          <Skeleton className="h-24" />
        ) : (
          <>
            {v.basis && <p className="text-caption font-semibold text-label-2">{v.basis.label} 기준</p>}
            <p className="mt-1 text-display tabular-nums text-label">{v.headline}</p>
            {v.note && <p className="mt-1 text-footnote text-label-3">{v.note}</p>}
            {hp != null && (
              <div className="mt-4 space-y-2">
                <HoursBar done={d.done_hours} held={d.total_hours} scale={hp.scale_hours} />
                <p className="flex flex-wrap gap-x-4 gap-y-1 text-caption tabular-nums text-label-2">
                  <Legend tone="done">승인 {fmtH(d.done_hours)}</Legend>
                  <Legend tone="held">클립 분량 {fmtH(d.total_hours)}</Legend>
                  {d.target_hours != null && <Legend tone="target">목표 {fmtH(d.target_hours)}</Legend>}
                </p>
              </div>
            )}
            {!compact && v.count && (
              <dl className="mt-4 divide-y divide-separator border-t border-separator text-footnote">
                <div className="flex justify-between py-2.5">
                  <dt className="text-label-3">건수 기준</dt>
                  <dd className="tabular-nums text-label">
                    {v.count.pct} ({v.count.done.toLocaleString("ko-KR")}/{v.count.total.toLocaleString("ko-KR")}건)
                  </dd>
                </div>
              </dl>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

/** 단계별 대기 하나를 그린다. */
interface StageCell {
  key: string;
  label: string;
  count: number;
  unit: "개" | "건";
  sub: string;
  to?: string;
}

/**
 * 단계별 대기 다섯 개를 그린다. 같은 딥링크를 수와 함께 가로로 놓는다.
 *
 * 이름과 수는 작업 단계 스텝퍼와 같다. 이름은 `STAGE_LABEL`, 수는 `stageCellCount`가 정한다.
 * 이 카드에서 따로 더하면 같은 이름이 두 화면에서 다른 수로 표시된다.
 * 완료는 품질검수 보류를 뺀 완료 클립 수(`completed_total`)에 든다.
 *
 * 각 항목은 그 단계를 여는 역할에게만 링크다(`STAGE_ROLES`).
 * 설명 문장을 붙이지 않는다. 이동만 하는 항목에 문장을 붙이면 수가 묻힌다.
 */
export function StageStrip({ project, deliverable }: { project: ProjectDTO; deliverable?: number | null }) {
  const pid = project.project_id;
  const user = useAuth((s) => s.user);
  const role = user?.global_role === "super_admin" ? "project_admin" : project.my_role;
  const summary = useQuery({
    queryKey: pipelineKeys.summary(pid),
    queryFn: () => fetchPipelineSummary(pid),
    refetchInterval: 60_000,
  });
  const wh = useQuery({
    queryKey: insightsKeys.workflowHealth(pid),
    queryFn: () => fetchWorkflowHealth(pid),
  });
  const stale = useQuery({
    queryKey: insightsKeys.capacity(pid),
    queryFn: () => fetchCapacity(pid),
  });
  const s = summary.data;
  const n = (v: number) => v.toLocaleString("ko-KR");
  const can = (stage: keyof typeof STAGE_ROLES) =>
    role != null && (STAGE_ROLES[stage] as readonly string[]).includes(role);
  const link = (stage: keyof typeof STAGE_ROLES) =>
    can(stage) ? `/p/${project.slug}/pipeline?stage=${stage}` : undefined;
  const reviewStale = stale.data?.stale?.find((x) => x.key === "review_wait");
  const parts = (xs: [string, number][]) =>
    xs.filter(([, v]) => v > 0).map(([k, v]) => `${k} ${n(v)}개`).join(" · ") || "없음";

  /*
    단계 이름과 수는 작업 단계 화면과 같다. 이름은 `STAGE_LABEL`, 수는 `stageCellCount` 한 곳에서 가져온다.
    같은 이름에 다른 수를 쓰면 두 화면을 오간 사람이 어느 쪽이 맞는지 판단할 수 없다.
    영상 등록은 사람이 분석을 요청할 영상이다.
  */
  const cells: StageCell[] = s
    ? [
        {
          key: "ingest",
          label: STAGE_LABEL.ingest,
          count: stageCellCount(s, "ingest") ?? 0,
          unit: "개",
          sub: parts([["분석 전", s.awaiting_analysis]]),
          to: link("ingest"),
        },
        {
          key: "label",
          label: STAGE_LABEL.label,
          count: stageCellCount(s, "label") ?? 0,
          unit: "건",
          sub: wh.data ? `보유 중 ${n(wh.data.queue_depth.claimed ?? 0)}건` : "",
          to: link("label"),
        },
        {
          key: "review",
          label: STAGE_LABEL.review,
          count: stageCellCount(s, "review") ?? 0,
          unit: "건",
          sub: reviewStale ? `${reviewStale.threshold_days}일 넘은 대기 ${n(reviewStale.count)}건` : "",
          to: link("review"),
        },
        {
          key: "done",
          label: STAGE_LABEL.done,
          count: stageCellCount(s, "done") ?? 0,
          // 완료 칸은 클립을 센다. 작업 단계 스텝퍼의 `완료 클립`과 같은 단위(`개`)를 쓴다.
          unit: "개",
          sub: deliverable != null ? `납품 가능 ${n(deliverable)}개` : "",
          to: link("done"),
        },
      ]
    : [];

  return (
    <Card as="section" aria-labelledby="stage-strip-title">
      <CardHead id="stage-strip-title" title="단계별 대기" note="지금 기준" />
      {summary.isError && s == null ? (
        <div className="px-5 pb-4">
          <ErrorState inline message="단계별 대기 조회 실패" onRetry={() => void summary.refetch()} />
        </div>
      ) : s == null ? (
        <div className="px-5 pb-4"><Skeleton className="h-16" /></div>
      ) : (
        <ul className="grid grid-cols-2 border-t border-separator md:grid-cols-4">
          {cells.map((c) => {
            const body = (
              <>
                <span className="flex items-center justify-between text-caption font-semibold text-label-2">
                  {c.label}
                  {c.to && <ChevronRight className="size-3.5 text-label-3" aria-hidden="true" />}
                </span>
                <span className="mt-1.5 block">
                  <span className="text-title-1 tabular-nums text-label">{n(c.count)}</span>
                  <span className="ml-0.5 text-footnote text-label-3">{c.unit}</span>
                </span>
                <span className="mt-1 block truncate text-caption tabular-nums text-label-3">{c.sub || " "}</span>
              </>
            );
            return (
              <li key={c.key} className="border-separator md:border-l md:first:border-l-0">
                {c.to ? (
                  <Link
                    to={c.to}
                    className="block h-full px-5 py-4 transition-colors hover:bg-canvas-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
                  >
                    {body}
                  </Link>
                ) : (
                  <div className="px-5 py-4">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** 계약 목표·마감 한 줄을 만든다. 개요의 머리 부제로 쓴다. 값이 없으면 `undefined`다. */
export function contractFacts(project: ProjectDTO): string | undefined {
  const parts = [
    project.target_hours != null ? `계약 목표 ${fmtH(project.target_hours)}` : null,
    project.contract_deadline ? `마감 ${fmtDay(project.contract_deadline)}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : undefined;
}
