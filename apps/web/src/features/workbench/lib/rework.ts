/**
 * 재작업 판정. 이 작업이 지금 재작업 중인지 정한다.
 *
 * 렌더 없이 테스트하려고 컴포넌트 밖에 둔다.
 */

/** 워크벤치가 재작업 배너·재작업 diff를 보여 줄지 정할 때 쓰는 최소 입력. */
interface ReworkInput {
  /** 이 작업의 가장 최근 검수 판정. 없으면 아직 검수를 받지 않았다는 뜻이다. */
  latestVerdict?: string | null;
  /** 서버가 세는 누적 반려 횟수. 다른 사람이 큐에서 받으면 0으로 되돌아간다. */
  rejectionCount?: number | null;
}

/**
 * 마지막 판정이 `reject`이고 누적 반려 횟수가 1 이상이면 true.
 *
 * `reviews`는 지워지지 않는 이력이다. 큐로 돌아온 재작업을 다른 사람이 받으면
 * 서버가 `rejection_count`를 0으로 되돌린다(`work.claim`). 그러면 마지막 판정은 `reject`인데
 * 새 담당자는 반려받지 않은 상태가 된다. 마지막 판정만 보면 이전 담당자의 반려를 새 담당자의 반려로 표시한다.
 * 서버도 반려 횟수를 기준으로 쓴다(`org/project.py`의 회수 제외, `analytics/dashboard/queries.py`의 재작업 집계).
 *
 * @param x 마지막 판정과 누적 반려 횟수. 둘 중 하나라도 없으면 false.
 */
export function isRework(x: ReworkInput): boolean {
  return x.latestVerdict === "reject" && (x.rejectionCount ?? 0) > 0;
}
