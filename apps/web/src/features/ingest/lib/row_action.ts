/**
 * 준비 타임라인 실패 줄의 조치 고르기.
 *
 * 실패 줄에는 재시도나 건너뛰기 중 하나만 둔다. 서버가 건너뛰기를 허용한 영상(`skippable`, 비식별화를 끈 프로젝트의 실패)은
 * 재시도 요청에 409로 응답하므로 재시도 버튼을 두지 않는다.
 * 화면은 프로젝트 설정을 보지 않고 서버가 준 값 하나로 고른다.
 */
/** 실패 줄에 둘 조치. 둘 다 없으면 `null`이다. */
export type FailedRowAction = "retry" | "skip" | null;

/**
 * 실패 줄에 둘 조치를 고른다.
 *
 * @param entry 그 단계 줄. 이전 버전 서버의 응답에는 `skippable`이 없을 수 있다.
 * @param can.retry 그 단계에 재시도 경로가 있고 재시도를 부를 수 있으면 true
 * @param can.skip 건너뛰기를 부를 수 있으면 true(관리자)
 */
export function failedRowAction(
  entry: { skippable?: boolean },
  can: { retry: boolean; skip: boolean },
): FailedRowAction {
  if (can.skip && entry.skippable) return "skip";
  return can.retry ? "retry" : null;
}

/**
 * 재시도 버튼을 서버 값(`retryable`)으로 정하는 단계.
 * 장면 요약 실패 줄은 영상마다 받을 수 있는 재요청이 다르고, 재요청을 받을 수 없는 실패 기록도 남는다.
 * 단계 이름만 보고 버튼을 두면 눌러도 처리되지 않는 줄이 생긴다.
 */
const SERVER_RETRY_STAGES: ReadonlySet<string> = new Set(["summarize"]);

/**
 * 실패 줄에 재시도를 제안할지 정한다.
 *
 * @param entry 그 단계 줄. `retryable`은 서버가 정한다.
 * @param hasPath 그 단계에 화면의 복구 경로가 있고 재시도를 부를 수 있으면 true
 */
export function retryOffered(entry: { stage: string; retryable?: boolean }, hasPath: boolean): boolean {
  if (!hasPath) return false;
  return SERVER_RETRY_STAGES.has(entry.stage) ? !!entry.retryable : true;
}
