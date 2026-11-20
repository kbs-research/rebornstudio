/**
 * 라벨링 단계의 비식별화 신고 카드. 라벨러나 검수자가 비식별화 미흡을 신고해 작업이 멈춘 클립을 모은다.
 *
 * 판정은 관리자가 한다. 행마다 신고 사유·위치·신고자와 장면 프레임을 두고 `재개`와 `제외`를 둔다.
 * 재개하면 멈춘 작업이 대기열로 돌아가고, 제외하면 클립이 납품에서 빠지고 미완 작업이 닫힌다.
 * 제외는 이 화면에서 되돌릴 수 없어 사유를 받는 확인 창을 거친다. 대상이 없으면 카드를 그리지 않는다.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, TriangleAlert } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { useToast } from "@/shared/ui/Toast";
import { errorText } from "@/shared/api/error_text";
import { type ApiError } from "@/shared/api/client";
import { fmtClipRange, fmtTimecode } from "@/shared/lib/fmtSecs";
import { fmtDateTime } from "@/shared/lib/time";
import { useSelectedProject } from "@/store/project";
import { meKeys } from "@/features/me";

import { fetchDeidentReports, pipelineKeys, resolveDeidentReport, type DeidentReportRow } from "../api";

/**
 * 신고 행의 장면 프레임. 주소가 없거나 불러오지 못하면 영상 아이콘을 둔다.
 *
 * 분석 직후에는 분석 서비스가 준 썸네일 주소(만료됨)가 오고, 그 호스트는 CSP의 저장소 출처 밖일 수 있다.
 * 막히거나 만료된 이미지를 깨진 그림으로 두지 않는다. 빈 면만 두면 불러오는 중인 것처럼 보여 아이콘을 둔다.
 */
function SceneFrame({ src }: { src?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed)
    return (
      <img
        src={src}
        alt=""
        onError={() => setFailed(true)}
        className="aspect-video w-32 shrink-0 rounded-control bg-canvas-2 object-cover"
      />
    );
  return (
    <div className="grid aspect-video w-32 shrink-0 place-items-center rounded-control bg-canvas-2" aria-hidden="true">
      <Film size={16} className="text-label-4" />
    </div>
  );
}

/** 서버 조회 상한. 라우터 Query(le=500)보다 작게 둔다. */
const LIMIT = 200;

/** 행 하나의 대상 이름. 영상 제목과 클립 구간이다. */
function targetLabel(r: DeidentReportRow): string {
  return `${r.video_title ?? "제목 없음"} · ${fmtClipRange(r.start_seconds, r.end_seconds)}`;
}

/** 비식별화 신고 카드를 그린다. 대상이 없거나 아직 받지 못했으면 아무것도 그리지 않는다. */
export function DeidentReportsCard() {
  const { selectedProjectId: pid } = useSelectedProject();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [excluding, setExcluding] = useState<DeidentReportRow | null>(null);
  const [note, setNote] = useState("");
  const q = useQuery({
    queryKey: pipelineKeys.deidentReports(pid),
    queryFn: () => fetchDeidentReports(pid, LIMIT),
    enabled: !!pid,
  });
  const resolve = useMutation<
    Awaited<ReturnType<typeof resolveDeidentReport>>, ApiError,
    { row: DeidentReportRow; action: "resume" | "exclude"; note?: string }
  >({
    meta: { handled: true },
    mutationFn: ({ row, action, note: n }) => resolveDeidentReport(row.clip_id, { action, note: n }),
    onSuccess: (r, { action }) => {
      setExcluding(null);
      setNote("");
      void qc.invalidateQueries({ queryKey: pipelineKeys.deidentReports(pid) });
      void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
      void qc.invalidateQueries({ queryKey: pipelineKeys.workAll() });
      // 재개한 작업은 라벨링이나 검수 대기열로 돌아가므로 두 대기 수도 다시 받는다.
      void qc.invalidateQueries({ queryKey: meKeys.workWaitingAll() });
      void qc.invalidateQueries({ queryKey: meKeys.reviewWaitingAll() });
      if (action === "resume") {
        toast("success", "작업을 재개했습니다", `대기열로 돌아간 작업 ${r.resumed_work_items}건`);
      } else {
        toast("success", "클립을 제외했습니다", `닫힌 작업 ${r.closed_work_items}건`);
      }
    },
    onError: (e) => {
      const t = errorText(e.detail, { message: "신고 처리 실패", hint: "목록을 새로고침해주세요." });
      toast("error", t.message, t.hint);
      void qc.invalidateQueries({ queryKey: pipelineKeys.deidentReports(pid) });
    },
  });

  if (q.isError) {
    return (
      <Card className="px-4 py-3">
        <ErrorState inline message="비식별화 신고 조회 실패" onRetry={() => void q.refetch()} />
      </Card>
    );
  }
  const page = q.data;
  if (!page || page.total === 0) return null;
  const n = (v: number) => v.toLocaleString("ko-KR");
  return (
    <Card as="section" aria-labelledby="deident-reports-title" className="overflow-hidden">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-separator px-4 py-3">
        <h3 id="deident-reports-title" className="text-headline text-label">
          비식별화 신고 <span className="tabular-nums">{n(page.total)}</span>
          <span className="ml-0.5 text-caption font-normal text-label-3">건</span>
        </h3>
        <span className="text-footnote text-label-3">작업이 멈춘 클립 · 관리자 확인 대상</span>
        {page.items.length < page.total && (
          <span className="ml-auto text-caption text-label-3">
            전체 {n(page.total)}건 중 {n(page.items.length)}건
          </span>
        )}
      </div>
      <ul className="divide-y divide-separator">
        {page.items.map((r) => (
          <li key={r.clip_id} className="flex items-start gap-4 px-4 py-3">
            {/* 주소가 바뀌면 실패 표시를 지우고 새 주소를 다시 불러온다. */}
            <SceneFrame key={r.scene_thumbnail_url ?? ""} src={r.scene_thumbnail_url} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-callout font-medium text-label" title={targetLabel(r)}>{targetLabel(r)}</p>
              {r.report ? (
                <>
                  <p className="mt-1 text-footnote text-label">{r.report.reason || "사유 없음"}</p>
                  <p className="mt-0.5 text-caption tabular-nums text-label-3">
                    {[
                      r.report.timestamp_ms != null ? `위치 ${fmtTimecode(r.report.timestamp_ms / 1000)}` : null,
                      r.report.reporter_name,
                      fmtDateTime(r.report.reported_at),
                    ].filter(Boolean).join(" · ")}
                  </p>
                </>
              ) : (
                <p className="mt-1 text-footnote text-label-3">신고 기록 없음</p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={resolve.isPending}
                onClick={() => resolve.mutate({ row: r, action: "resume" })}
              >
                재개
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={resolve.isPending}
                onClick={() => { setExcluding(r); setNote(""); }}
              >
                제외
              </Button>
            </div>
          </li>
        ))}
      </ul>

      <Modal
        open={excluding != null}
        onClose={() => setExcluding(null)}
        title="클립 제외"
        icon={<TriangleAlert size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setExcluding(null)}>취소</Button>
            <Button
              variant="danger"
              size="sm"
              disabled={resolve.isPending || note.trim() === "" || excluding == null}
              onClick={() => excluding && resolve.mutate({ row: excluding, action: "exclude", note: note.trim() })}
            >
              {resolve.isPending ? "처리 중…" : "제외"}
            </Button>
          </>
        }
      >
        {excluding && (
          <div className="space-y-4">
            <div>
              <p className="font-semibold text-label">{targetLabel(excluding)}</p>
              <p className="mt-1 text-footnote text-label-3">
                납품에서 빠지고 이 클립의 미완 작업이 닫힙니다. 승인된 작업은 남습니다.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="deident-exclude-note">사유</Label>
              <Input
                id="deident-exclude-note"
                type="text"
                value={note}
                maxLength={1000}
                onChange={(e) => setNote(e.target.value)}
                placeholder="예: 얼굴 가림 불가"
              />
            </div>
            <p className="text-footnote text-label-2">이 화면에서 되돌릴 수 없습니다.</p>
          </div>
        )}
      </Modal>
    </Card>
  );
}
