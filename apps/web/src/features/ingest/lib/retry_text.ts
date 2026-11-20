/**
 * 분석 재요청과 요약 재요청의 문구와 결과 토스트.
 *
 * 두 재요청은 같은 서버 경로(`maiu/retry`)를 부르고 처리 방식은 서버가 영상마다 정한다.
 * 장면 요약을 받지 못한 영상(서버 `prep_blocked_stage`가 `summary`)은 요약만 다시 요청하고,
 * 그 밖의 실패는 분석을 다시 요청한다. 화면은 서버가 정한 위치로 문구만 고른다.
 * 영상 상세와 영상 등록 화면이 같은 문구를 쓰도록 이 파일에 둔다. 렌더 없이 테스트한다.
 */
import type { PrepBlockedStage } from "./vocab";

/** 요약 재요청 버튼과 확인 창 제목. */
export const SUMMARY_RETRY_LABEL = "요약 재요청";

/** 요약 재요청 확인 창의 두 줄. `lead`는 굵게 쓰는 할 일, `sub`는 바뀌지 않는 대상. */
export const SUMMARY_CONFIRM = {
  lead: "장면 요약만 다시 요청합니다.",
  sub: "클립과 진행 중인 작업은 그대로 둡니다.",
} as const;

/**
 * 재요청 요청 자체가 실패했을 때 토스트 제목을 고른다. 버튼 이름 뒤에 `실패`를 붙인다.
 *
 * @param summaryOnly 요약 재요청 버튼으로 보낸 요청이면 true
 */
export function retryFailTitle(summaryOnly: boolean): string {
  return `${summaryOnly ? SUMMARY_RETRY_LABEL : "분석 재요청"} 실패`;
}

/** 막힌 위치가 요약이면 true. 그 영상의 재요청은 서버가 요약만 다시 요청한다. */
export function isSummaryBlocked(v: { prep_blocked_stage?: PrepBlockedStage | null }): boolean {
  return v.prep_blocked_stage === "summary";
}

/** 재요청 결과 중 토스트가 읽는 수. */
export interface RetryCounts {
  requeued: number;
  restarted: number;
  resummarized?: number;
  resummarize_failed?: number;
}

/** 토스트 한 건. `useToast`의 `(type, message, detail?)` 인자에 그대로 넘긴다. */
export interface RetryToast {
  tone: "success" | "warning" | "error";
  title: string;
  detail?: string;
}

/** 요약 요청을 분석 서비스가 받지 않았을 때의 안내. 사유는 서버가 준비 진행의 요약 줄에 남긴다. */
export const RESUMMARIZE_FAILED_HINT = "사유는 영상 상세의 준비 진행에 있습니다.";

/**
 * 재요청 결과를 토스트 한 건으로 바꾼다. 영상 수라서 단위는 `개`다.
 *
 * 요약 요청이 실패한 영상이 있으면 오류로 먼저 알린다. 그 영상은 막힘으로 남는다.
 * 처리한 영상이 없으면 경고로 알린다. 서버는 실패 상태가 아닌 영상을 건너뛴다.
 */
export function retryToast(r: RetryCounts, requested: number): RetryToast {
  const resummarized = r.resummarized ?? 0;
  const failed = r.resummarize_failed ?? 0;
  if (failed > 0) {
    return { tone: "error", title: `영상 ${failed}개 요약 재요청 실패`, detail: RESUMMARIZE_FAILED_HINT };
  }
  const parts = [
    resummarized > 0 ? `요약 재요청 ${resummarized}개` : "",
    r.requeued + r.restarted > 0 ? `분석 재요청 ${r.requeued + r.restarted}개` : "",
  ].filter(Boolean);
  if (parts.length === 0) {
    return {
      tone: "warning",
      title: "다시 요청할 실패 영상이 없습니다",
      detail: `${requested}개 모두 재요청할 상태가 아닙니다.`,
    };
  }
  // 다음에 확인할 곳이 달라 등록부터 다시 하는 경우를 따로 알린다.
  const detail = r.restarted > 0
    ? `${r.restarted}개는 분석 등록부터 다시 시작합니다.`
    : r.requeued > 0
      ? "등록된 파일을 그대로 씁니다."
      : SUMMARY_CONFIRM.sub;
  return { tone: "success", title: parts.join(" · "), detail };
}
