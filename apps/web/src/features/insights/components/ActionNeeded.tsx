/**
 * 개요의 `조치 필요` 카드. 관리자가 아침에 먼저 알아야 할 막힌 것을 한 곳에 모은다.
 *
 * 행마다 수·분모·여는 곳을 한 줄에 둔다. 0건인 항목은 표에서 빼고 아래 한 줄 `… 없음`으로
 * 남긴다. 항목을 지우면 검사하지 않은 것과 구별되지 않는다.
 * 이 카드는 개요에만 둔다. 통계는 사람별 실적을 나란히 보는 화면이라 운영 경보와 섞지 않는다.
 *
 * 비식별화 신고와 반복 반려를 맨 위에 둔다. 둘 다 관리자가 라벨링 단계에서 처리한다.
 *
 * 행 계산(`useActionRows`)과 그리기(`ActionNeeded`)를 나눈다. 개요가 표에 실린 행 수로
 * 카드 배치를 고르므로 같은 계산 결과를 개요와 카드가 함께 읽는다.
 */
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";

import { useCapacity } from "./useCapacity";
import { blockerHint, noneLine, splitActionRows, type ActionRow } from "../lib/action_rows";
import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Skeleton } from "@/shared/ui/Skeleton";
import { type ProjectDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { fetchPipelineSummary, fetchQcHeldTotal, pipelineKeys } from "@/features/pipeline";

import { fetchBusinessKpi, insightsKeys } from "../api";

/** 점 색. 막힘의 무게를 점 하나로 표시하고 글자는 먹색으로 둔다. */
const DOT: Record<ActionRow["tone"], string> = {
  bad: "bg-bad",
  warn: "bg-warn",
};

/** 조치 필요 행 계산 결과. `loading`·`error`이면 행이 없다. */
export type ActionRowsState =
  | { status: "loading" }
  | { status: "error"; retry: () => void }
  | { status: "ready"; rows: ActionRow[]; shown: ActionRow[]; none: ActionRow[] };

/** 조치 필요 행을 계산한다. 개요가 배치를 고를 때와 카드가 그릴 때 같은 결과를 쓴다. */
export function useActionRows(project: ProjectDTO): ActionRowsState {
  const pid = project.project_id;
  const slug = project.slug;
  const cap = useCapacity();
  const summary = useQuery({
    queryKey: pipelineKeys.summary(pid),
    queryFn: () => fetchPipelineSummary(pid),
    refetchInterval: 60_000,
  });
  // 보류함의 `total`은 페이지 행 수와 다른 전체 수다. 한 건만 받아 수만 읽는다.
  const held = useQuery({
    queryKey: pipelineKeys.qcHeldTotal(pid),
    queryFn: () => fetchQcHeldTotal(pid),
  });
  // 납품 불가의 분모(승인 건수)다. 개요의 진척률 카드와 같은 쿼리 키라 요청은 한 번이다.
  const kpi = useQuery({
    queryKey: insightsKeys.businessKpi(pid),
    queryFn: () => fetchBusinessKpi(pid),
  });

  const loading =
    cap.data == null
    || summary.data == null
    || (held.isPending && !held.isError);
  if (cap.error && cap.data == null) return { status: "error", retry: () => void cap.refetch() };
  if (loading) return { status: "loading" };

  const s = summary.data;
  const stale = (k: string) => cap.data?.stale?.find((x) => x.key === k);
  const reviewWait = stale("review_wait");
  const untouched = stale("untouched");
  const reasons = cap.data?.blocker_reasons;
  const blocked = reasons == null ? null : Object.values(reasons).reduce((a, b) => a + b, 0);
  const n = (v: number) => v.toLocaleString("ko-KR");

  const repeated: ActionRow = {
    key: "repeated_rejects",
    label: "반복 반려",
    hint: "재작업 중인 작업 · 가이드나 클립 확인 대상",
    count: s?.repeated_rejects ?? null,
    unit: "건",
    to: `/p/${slug}/pipeline?stage=label`,
    toLabel: "라벨링 단계",
    tone: "bad",
  };
  const deident: ActionRow = {
    key: "deident_reports",
    label: "비식별화 신고",
    hint: "작업이 멈춘 클립 · 재개나 제외를 기다림",
    count: s?.deident_reports ?? null,
    unit: "건",
    to: `/p/${slug}/pipeline?stage=label`,
    toLabel: "라벨링 단계",
    tone: "bad",
  };
  const review: ActionRow = {
    key: "review_wait",
    label: "장기 검수 대기",
    hint: `제출 후 ${reviewWait?.threshold_days ?? 3}일 넘음 · 검수 대기 ${s ? n(s.review_submitted) : "—"}건 중`,
    count: reviewWait?.count ?? null,
    unit: "건",
    to: `/p/${slug}/pipeline?stage=review`,
    toLabel: "검수 단계",
    tone: "warn",
  };
  const qcHeld: ActionRow = {
    key: "qc_held",
    label: "캡션 품질검수 보류",
    hint: "승인 후 검사에서 멈춘 작업",
    count: held.data?.total ?? null,
    unit: "건",
    to: `/p/${slug}/qc`,
    toLabel: "보류 목록",
    tone: "warn",
  };
  const undeliverable: ActionRow = {
    key: "blocked",
    label: "납품 불가",
    hint:
      reasons == null
        ? `상한 ${n(cap.data?.blocker_scan_limit ?? 0)}건을 넘어 세지 않음`
        : blockerHint(reasons, kpi.data?.done_count ?? null),
    count: blocked,
    unit: "건",
    to: `/p/${slug}/pipeline?stage=done`,
    toLabel: "완료 목록",
    tone: "warn",
  };
  const untouchedRow: ActionRow = {
    key: "untouched",
    label: "장기 미착수",
    hint: `작업 생성 후 ${untouched?.threshold_days ?? 12}일`,
    count: untouched?.count ?? null,
    unit: "건",
    to: `/p/${slug}/pipeline?stage=label`,
    toLabel: "라벨링 단계",
    tone: "warn",
  };

  const rows = [deident, repeated, review, qcHeld, undeliverable, untouchedRow];
  return { status: "ready", rows, ...splitActionRows(rows) };
}

/** 조치 필요 카드를 그린다. 행은 `useActionRows`의 결과를 받는다. */
export function ActionNeeded({ state }: { state: ActionRowsState }) {
  if (state.status === "error") {
    return (
      <Card as="section" className="px-5 py-4">
        <ErrorState inline message="조치 필요 항목 조회 실패" onRetry={state.retry} />
      </Card>
    );
  }
  const loading = state.status === "loading";
  const shown = state.status === "ready" ? state.shown : [];
  const none = state.status === "ready" ? state.none : [];
  const total = state.status === "ready" ? state.rows.length : 0;
  const n = (v: number) => v.toLocaleString("ko-KR");

  return (
    <Card as="section" aria-labelledby="action-needed-title" className="overflow-hidden">
      <div className="flex items-baseline gap-2 border-b border-separator px-5 py-4">
        <h2 id="action-needed-title" className="text-headline text-label">조치 필요</h2>
        {/* 전부 0건이면 아래 줄이 `조치 필요 항목 없음`이라 머리의 `0개 항목`은 같은 사실을 반복한다. */}
        {!loading && shown.length > 0 && <span className="text-caption text-label-3">{shown.length}개 항목</span>}
      </div>
      {loading ? (
        <div className="px-5 py-4"><Skeleton className="h-40" /></div>
      ) : (
        <>
          {shown.length > 0 && (
            <ul className="divide-y divide-separator">
              {shown.map((r) => (
                <li key={r.key} className="flex items-center gap-4 px-5 py-3.5">
                  <span className={cn("size-2 shrink-0 rounded-capsule", DOT[r.tone])} aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-body font-semibold text-label">{r.label}</p>
                    <p className="truncate text-footnote text-label-3">{r.hint}</p>
                  </div>
                  <p className="w-20 shrink-0 text-right">
                    {r.count == null ? (
                      <span className="text-footnote text-label-3">미집계</span>
                    ) : (
                      <>
                        <span className="text-title-1 tabular-nums text-label">{n(r.count)}</span>
                        <span className="ml-0.5 text-footnote text-label-3">{r.unit}</span>
                      </>
                    )}
                  </p>
                  <span className="w-24 shrink-0 text-right">
                    <Link
                      to={r.to}
                      className="inline-flex items-center gap-0.5 rounded-mark text-footnote font-semibold text-label hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                    >
                      {r.toLabel}
                      <ChevronRight className="size-3.5" aria-hidden="true" />
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {/* 표가 없으면 머리의 아래 선과 겹쳐 선이 두 겹이 되므로 위 선은 표가 있을 때만 긋는다. */}
          {none.length > 0 && (
            <p
              className={cn(
                "flex items-center gap-2 bg-canvas-2 px-5 py-3 text-footnote text-label-2",
                shown.length > 0 && "border-t border-separator",
              )}
            >
              <Check className="size-4 shrink-0 text-ok-text" aria-hidden="true" />
              {noneLine(none, total)}
            </p>
          )}
        </>
      )}
    </Card>
  );
}
