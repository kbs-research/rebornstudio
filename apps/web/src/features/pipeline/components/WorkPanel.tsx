/**
 * 작업 단계 라벨링 화면의 다음 작업 카드. 화면을 연 사람의 라벨링 대기열이다.
 *
 * 한 사람은 라벨링 작업을 한 건씩 맡는다. 다음 작업을 누르면 서버가 보유한 작업을 다시 주거나
 * 대기열에서 한 건을 배정하고, 화면은 그 작업의 워크벤치를 연다.
 * 보유한 작업이 있으면 그 클립을 미리 보여 준다. 재작업이면 반려 횟수를 붙인다.
 * 머리, 빈 상태, 실패 표시는 두 대기열이 함께 쓰는 `NextWorkCard`가 그린다.
 */
import { EmptyState } from "@/shared/ui/EmptyState";
import { Chip } from "@/shared/ui/Chip";
import { useSelectedProject } from "@/store/project";
import { useLabelQueue } from "@/features/me";

import { ClipRangeCell } from "./ClipRangeCell";
import { NextWorkCard, nextErrorText, poolFailed } from "./NextWorkCard";

/**
 * 라벨링 단계의 다음 작업 카드를 그린다.
 * `embedded`가 `true`면(파이프라인 단계 화면 안에 넣을 때) 카드 제목을 `내 라벨링 작업`으로 쓴다.
 */
export function WorkPanel({ embedded = false }: { embedded?: boolean } = {}) {
  const { selectedProjectId: pid } = useSelectedProject();
  const q = useLabelQueue(pid);

  if (!pid) {
    return <EmptyState message="프로젝트 선택 필요" description="프로젝트를 고르면 대기열이 열립니다." />;
  }

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
          <span className="inline-flex flex-wrap items-center gap-2">
            <ClipRangeCell projectId={current.project_id} clipId={current.clip_id} brief />
            {current.rejection_count > 0 && (
              <Chip tone="amber">재작업 {current.rejection_count.toLocaleString("ko-KR")}회</Chip>
            )}
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
