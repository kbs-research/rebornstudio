/**
 * 라벨링 단계의 반려 누적 카드. 반려가 기준 횟수 이상 쌓인 미완 작업을 관리자에게 모아 보여 준다.
 *
 * 작업 흐름은 바꾸지 않는다. 반려된 작업은 계속 만든 사람에게 재작업으로 돌아가고,
 * 관리자는 이 목록에서 가이드나 클립에 문제가 있는지 살펴본다.
 * 같은 사유가 되풀이되는지 보도록 마지막 반려 사유를 행에 쓴다.
 * 기준 횟수는 서버 응답(`threshold`)을 쓴다. 대상이 없으면 카드를 그리지 않는다.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { ErrorState } from "@/shared/ui/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { useSelectedProject } from "@/store/project";

import { fetchRepeatedRejects, pipelineKeys, type RepeatedRejectRow } from "../api";

/** 서버 조회 상한. 라우터 Query(le=500)보다 작게 둔다. */
const LIMIT = 200;

/** 마지막 반려의 사유 이름과 보충 설명을 한 줄로 잇는다. 둘 다 없으면 `—`다. */
function lastReason(r: RepeatedRejectRow): string {
  const parts = [...(r.last_reject_reasons ?? []).map((x) => x.label), (r.last_reject_comment ?? "").trim()];
  return parts.filter(Boolean).join(" · ") || "—";
}

/** 표의 열. 클립, 상태, 보유자, 반려 횟수, 마지막 반려 사유 순서다. */
const COLUMNS: DataColumn<RepeatedRejectRow>[] = [
  {
    label: "클립",
    kind: "title",
    value: (r) => r.video_title ?? "",
    cell: (r) => (
      <span className="min-w-0 truncate">
        {r.video_title ?? "제목 없음"}
        {r.clip_start_seconds != null && r.clip_end_seconds != null && (
          <span className="font-mono tabular-nums text-label-2">
            {" · "}{fmtClipRange(r.clip_start_seconds, r.clip_end_seconds)}
          </span>
        )}
      </span>
    ),
  },
  { label: "상태", width: "7rem", value: (r) => r.status, cell: (r) => <StatusBadge status={r.status} /> },
  { label: "보유자", width: "8rem", value: (r) => r.holder_name ?? "", cell: (r) => r.holder_name ?? "—" },
  {
    label: "반려",
    kind: "number",
    align: "right",
    width: "5rem",
    value: (r) => r.rejection_count,
    cell: (r) => <Chip>{r.rejection_count.toLocaleString("ko-KR")}회</Chip>,
  },
  { label: "마지막 반려 사유", sortable: false, value: (r) => lastReason(r), cell: (r) => lastReason(r) },
];

/** 반려 누적 카드를 그린다. 대상이 없거나 아직 받지 못했으면 아무것도 그리지 않는다. */
export function RepeatedRejectsCard() {
  const { selectedProjectId: pid } = useSelectedProject();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: pipelineKeys.repeatedRejects(pid),
    queryFn: () => fetchRepeatedRejects(pid, LIMIT),
    enabled: !!pid,
  });
  if (q.isError) {
    return (
      <Card className="px-4 py-3">
        <ErrorState inline message="반려 누적 작업 조회 실패" onRetry={() => void q.refetch()} />
      </Card>
    );
  }
  const page = q.data;
  if (!page || page.total === 0) return null;
  const n = (v: number) => v.toLocaleString("ko-KR");
  return (
    <Card as="section" aria-labelledby="repeated-rejects-title" className="overflow-hidden">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-separator px-4 py-3">
        {/* 이름은 개요의 조치 필요 행과 같은 `반복 반려`다. 기준 횟수는 서버가 준 값을 설명에 쓴다. */}
        <h3 id="repeated-rejects-title" className="text-headline text-label">
          반복 반려 <span className="tabular-nums">{n(page.total)}</span>
          <span className="ml-0.5 text-caption font-normal text-label-3">건</span>
        </h3>
        <span className="text-footnote text-label-3">반려 {n(page.threshold)}회 이상인 미완 작업 · 가이드나 클립 확인 대상</span>
      </div>
      <DataTable<RepeatedRejectRow>
        bare
        columns={COLUMNS}
        rows={page.items}
        rowKey={(r) => r.work_item_id}
        initialSort={{ i: 3, dir: "desc" }}
        onRowClick={(r) => navigate(`/work/${r.work_item_id}/label`)}
        total={page.total}
        unit="건"
      />
    </Card>
  );
}
