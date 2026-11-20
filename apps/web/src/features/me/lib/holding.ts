/**
 * 보유가 바뀐 뒤 `내 작업`의 보유 목록과 보유 수를 함께 다시 받는다.
 *
 * `내 작업`은 보유 목록과 보유 수(대기 수 응답)를 다른 조회로 읽는다. 판정·제출·신고 뒤
 * 목록만 무효화하면 목록은 비었는데 머리의 "보유 중 N건"은 캐시 유지 시간(30초) 동안 그대로 남는다.
 * 그래서 보유를 바꾸는 뮤테이션은 이 함수 하나로 두 키를 무효화한다.
 */
import type { QueryKey } from "@tanstack/react-query";

import { meKeys } from "../api";

/** 보유가 바뀐 대기열. 라벨링 작업(`work`)과 검수 작업(`review`)이다. */
export type HoldingKind = "work" | "review";

/** 그 대기열의 보유 목록 키와 보유 수 키(모든 프로젝트)를 돌려준다. */
export function holdingKeys(kind: HoldingKind) {
  return kind === "work"
    ? [meKeys.heldWorkAll(), meKeys.workWaitingAll()]
    : [meKeys.heldReviewsAll(), meKeys.reviewWaitingAll()];
}

/** 무효화를 받는 쪽. `QueryClient`를 그대로 넘길 수 있고 테스트는 기록용 가짜를 넘긴다. */
interface Invalidator {
  invalidateQueries: (filters: { queryKey: QueryKey }) => unknown;
}

/** 그 대기열의 보유 목록과 보유 수를 무효화한다. */
export function invalidateHolding(qc: Invalidator, kind: HoldingKind): void {
  for (const queryKey of holdingKeys(kind)) void qc.invalidateQueries({ queryKey });
}
