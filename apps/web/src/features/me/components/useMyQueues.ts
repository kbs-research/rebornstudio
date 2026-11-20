/**
 * 두 대기열(라벨링·검수)의 조회와 다음 작업 훅. `내 작업`과 작업 단계 화면의 대기열 카드가 함께 쓴다.
 *
 * 조회 키는 워크벤치·검수 화면과 같다. 여러 화면이 같은 캐시를 읽어 한쪽에서 보유가 바뀌면 다른 쪽도 같은 수를 보여 준다.
 * 대기 수와 보유 수는 서버가 센 값을 쓴다. 다음 작업은 서버가 고른 한 건을 받아 바로 워크벤치로 연다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { pipelineKeys } from "@/features/pipeline";
import { type ApiError } from "@/shared/api/client";

import {
  fetchHeldReviews,
  fetchHeldWork,
  fetchReviewWaiting,
  fetchWorkWaiting,
  meKeys,
  nextReview,
  nextWork,
  type NextWorkDTO,
} from "../api";

/** 다음 작업으로 받은 작업을 여는 주소. 라벨링은 라벨링 워크벤치, 검수는 검수 워크벤치다. */
export function workbenchPath(kind: "label" | "review", workItemId: string): string {
  return kind === "label" ? `/work/${workItemId}/label` : `/review/${workItemId}`;
}

/**
 * 라벨링 대기열의 보유 목록, 대기 수, 다음 작업을 돌려준다.
 *
 * 보유 목록은 다음 작업이 다시 열 작업을 미리 보여 주는 데 쓴다. 순서(재작업 먼저)는 서버가 정한다.
 * 보유에는 끝나는 시각이 없어 화면이 시간을 세지 않는다.
 */
export function useLabelQueue(pid: string | null) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const waiting = useQuery({
    queryKey: meKeys.workWaiting(pid),
    queryFn: () => fetchWorkWaiting(pid),
    enabled: !!pid,
    refetchInterval: 60_000,
    // 실패는 머리의 칩이 알린다(`poolFailed`).
    meta: { handled: true },
  });
  const list = useQuery({
    queryKey: meKeys.heldWork(pid),
    queryFn: () => fetchHeldWork(pid),
    enabled: !!pid,
    refetchInterval: 30_000,
  });
  const next = useMutation<NextWorkDTO, ApiError>({
    // 실패는 카드의 배너가 알린다. 전역 토스트가 겹치지 않게 한다.
    meta: { handled: true },
    mutationFn: () => nextWork(pid),
    onSuccess: (w) => {
      void qc.invalidateQueries({ queryKey: meKeys.heldWorkAll() });
      void qc.invalidateQueries({ queryKey: meKeys.workWaitingAll() });
      void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
      navigate(workbenchPath("label", w.work_item_id));
    },
  });
  return { waiting, list, next, held: waiting.data?.held ?? null };
}

/** 검수 대기열의 보유 목록, 대기 수, 다음 검수를 돌려준다. 대기 수는 서버가 다음 검수와 같은 조건으로 센다. */
export function useReviewQueue(pid: string | null) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const waiting = useQuery({
    queryKey: meKeys.reviewWaiting(pid),
    queryFn: () => fetchReviewWaiting(pid),
    enabled: !!pid,
    refetchInterval: 60_000,
    meta: { handled: true },
  });
  const list = useQuery({
    queryKey: meKeys.heldReviews(pid),
    queryFn: () => fetchHeldReviews(pid),
    enabled: !!pid,
    refetchInterval: 30_000,
  });
  const next = useMutation<NextWorkDTO, ApiError>({
    meta: { handled: true },
    mutationFn: () => nextReview(pid),
    onSuccess: (w) => {
      void qc.invalidateQueries({ queryKey: meKeys.heldReviewsAll() });
      void qc.invalidateQueries({ queryKey: meKeys.reviewWaitingAll() });
      void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
      navigate(workbenchPath("review", w.work_item_id));
    },
  });
  return { waiting, list, next, held: waiting.data?.held ?? (list.data ? list.data.length : null) };
}
