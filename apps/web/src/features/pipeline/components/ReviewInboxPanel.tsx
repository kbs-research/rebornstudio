/**
 * 작업 단계 검수 화면의 다음 검수 카드. 화면을 연 사람의 검수 대기열이다.
 *
 * 한 사람은 검수 작업을 한 건씩 맡는다. 다음 검수를 누르면 서버가 보유한 검수를 다시 주거나
 * 제출 순으로 한 건을 배정하고, 화면은 검수 워크벤치를 연다.
 * 머리, 빈 상태, 실패 표시는 두 대기열이 함께 쓰는 `NextWorkCard`가 그린다.
 * 단계 화면 안에 들어가므로 자체 PageHeader를 두지 않는다.
 */
import { EmptyState } from "@/shared/ui/EmptyState";
import { anonId } from "@/lib/anon";
import { useSelectedProject } from "@/store/project";
import { useReviewQueue } from "@/features/me";

import { ClipRangeCell } from "./ClipRangeCell";
import { NextWorkCard, nextErrorText, poolFailed } from "./NextWorkCard";

/**
 * 검수 단계의 다음 검수 카드를 그린다.
 *
 * `embedded`는 파이프라인 단계 화면 안에 넣을 때 쓴다. 레일 캡슐이 이미 '검수'라고 보여 주므로 카드 제목을 `내 검수 작업`으로 쓴다.
 */
export function ReviewInboxPanel({ embedded = false }: { embedded?: boolean } = {}) {
  const { selectedProjectId: pid } = useSelectedProject();
  const q = useReviewQueue(pid);

  if (!pid) {
    return <EmptyState message="프로젝트 선택 필요" description="프로젝트를 고르면 대기열이 열립니다." />;
  }

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
          <span className="inline-flex flex-wrap items-center gap-2 text-footnote text-label-2">
            <ClipRangeCell projectId={current.project_id} clipId={current.clip_id} />
            {/* 라벨러는 바뀌지 않는 익명 핸들로만 보인다. */}
            <span>
              라벨러 <span className="font-mono">{current.claimed_by ? anonId(current.claimed_by, "WK") : "—"}</span>
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
