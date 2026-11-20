/**
 * `내 작업`(`/my/work`) 본문. 역할마다 다음 작업 카드 하나를 둔다.
 *
 * 한 사람은 역할마다 한 건씩 맡는다. 다음 작업을 누르면 서버가 보유한 작업을 먼저 다시 주고,
 * 없으면 대기열에서 한 건을 배정한다. 화면은 받은 작업의 워크벤치를 바로 연다.
 * 보유한 작업이 있으면 카드가 그 작업(영상 제목·구간·캡션 미리보기)을 미리 보여 준다.
 * 재작업이면 반려 횟수와 마지막 반려 사유를 붙인다. 순서(재작업 먼저)는 서버 `work_service.held_order`가 정한다.
 *
 * 관리자는 두 대기열을 세그먼트로 고른다. 보유가 있는 탭에 보유 수를 붙인다.
 * 고를 수 있는 목록, 반납, 남은 시간은 두지 않는다. 보유는 제출·판정·신고로 끝난다.
 */
import { useState } from "react";

import { Chip } from "@/shared/ui/Chip";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import { NextWorkCard, nextErrorText, poolFailed } from "@/features/pipeline";
import { anonId } from "@/lib/anon";
import { whenLabel } from "@/shared/lib/time";
import { useSelectedProject } from "@/store/project";

import { ClipLine, RejectReasons } from "./QueueParts";
import { useLabelQueue, useReviewQueue } from "./useMyQueues";

/**
 * 라벨러의 `내 작업`을 그린다. `embedded`면(관리자 탭) 카드 제목을 `내 라벨링 작업`으로 쓴다.
 */
export function LabelerQueue({ embedded = false }: { embedded?: boolean }) {
  const { selectedProjectId: pid } = useSelectedProject();
  const q = useLabelQueue(pid);
  if (!pid) return null;

  const current = q.list.data?.[0];
  return (
    <NextWorkCard
      kind="label"
      embedded={embedded}
      pool={q.waiting.data?.count}
      poolError={poolFailed(q.waiting)}
      held={q.held}
      current={
        current ? (
          <span className="block min-w-0 space-y-1.5">
            <ClipLine
              projectId={current.project_id}
              clipId={current.clip_id}
              work={current}
              size="lg"
              extra={current.rejection_count > 0 ? <Chip tone="amber">재작업 {current.rejection_count}회</Chip> : undefined}
            />
            {current.rejection_count > 0 && <RejectReasons reasons={current.last_reject_reasons} />}
          </span>
        ) : null
      }
      loading={q.list.isLoading}
      listError={q.list.error}
      updatedAt={q.list.dataUpdatedAt}
      onRefresh={() => {
        void q.list.refetch();
        void q.waiting.refetch();
      }}
      refreshing={q.list.isRefetching}
      onNext={() => q.next.mutate()}
      nexting={q.next.isPending}
      nextError={q.next.isError ? nextErrorText(q.next.error?.detail) : null}
    />
  );
}

/**
 * 검수자의 `내 작업`을 그린다. 검수자가 일하러 들어오는 곳은 이 화면 하나다.
 *
 * 판정 전에 알아야 할 것은 이전 반려 횟수와 제출 시각이다. 라벨러는 익명 ID로만 보이고
 * 제출자 실적은 두지 않는다. 판정에 영향을 주는 정보를 판정 화면 앞에 두지 않는다.
 */
export function ReviewerQueue({ embedded = false }: { embedded?: boolean }) {
  const { selectedProjectId: pid } = useSelectedProject();
  const q = useReviewQueue(pid);
  if (!pid) return null;

  const current = q.list.data?.[0];
  return (
    <NextWorkCard
      kind="review"
      embedded={embedded}
      pool={q.waiting.data?.count}
      poolError={poolFailed(q.waiting)}
      held={q.held}
      current={
        current ? (
          <span className="block min-w-0 space-y-1.5">
            <ClipLine
              projectId={current.project_id}
              clipId={current.clip_id}
              work={current}
              size="lg"
              extra={current.rejection_count > 0 ? <Chip>이전 반려 {current.rejection_count}회</Chip> : undefined}
            />
            <span className="flex flex-wrap items-center gap-x-3 text-footnote text-label-2">
              <span>
                라벨러 <span className="font-mono text-caption">{current.claimed_by ? anonId(current.claimed_by, "WK") : "—"}</span>
              </span>
              {current.submitted_at && <span>제출 {whenLabel(current.submitted_at)}</span>}
            </span>
          </span>
        ) : null
      }
      loading={q.list.isLoading}
      listError={q.list.error}
      updatedAt={q.list.dataUpdatedAt}
      onRefresh={() => {
        void q.list.refetch();
        void q.waiting.refetch();
      }}
      refreshing={q.list.isRefetching}
      onNext={() => q.next.mutate()}
      nexting={q.next.isPending}
      nextError={q.next.isError ? nextErrorText(q.next.error?.detail) : null}
    />
  );
}

type AdminTab = "label" | "review";

/**
 * 관리자의 `내 작업`을 그린다. 관리자는 두 대기열을 모두 맡을 수 있다(서버 `_REVIEWER_ROLES`).
 *
 * 보유가 있는 탭에만 `보유 중 N`을 붙인다. 어느 대기열에 맡은 작업이 남았는지 탭을 눌러 보지 않고 알 수 있다.
 * 수를 받기 전이거나 0이면 이름만 쓴다. 조회 전에 0을 쓰면 실제 0건으로 읽힌다.
 */
export function AdminQueue() {
  const { selectedProjectId: pid } = useSelectedProject();
  const [tab, setTab] = useState<AdminTab>("label");
  const label = useLabelQueue(pid);
  const review = useReviewQueue(pid);
  const withCount = (name: string, n: number | null) => (
    <span className="inline-flex items-center gap-1.5">
      {name}
      {n != null && n > 0 && <span className="text-caption tabular-nums text-label-2">보유 중 {n.toLocaleString("ko-KR")}</span>}
    </span>
  );
  return (
    <div className="space-y-4">
      <SegmentedControl<AdminTab>
        ariaLabel="대기열"
        value={tab}
        onChange={setTab}
        options={[
          { value: "label", label: withCount("라벨링", label.held) },
          { value: "review", label: withCount("검수", review.held) },
        ]}
      />
      {tab === "label" && <LabelerQueue embedded />}
      {tab === "review" && <ReviewerQueue embedded />}
    </div>
  );
}
