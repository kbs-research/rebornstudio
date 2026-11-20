/**
 * 분석 연동 카드(저장소 대상·분석 대상)의 문구 규칙과 일시 오류 재조회 규칙. 두 카드가 같은 수를 같은 문구로 쓴다.
 *
 * 잠금 근거의 수는 서버(`services/stacks/stack_lock`)가 센다. 화면은 그 수를 고치지 않고 분모만 밝힌다.
 */

/**
 * 잠긴 이유의 제목을 만든다. 지운 영상이 있으면 그 수를 괄호로 밝힌다.
 *
 * 잠금은 지운 영상도 센다. 그 영상의 원본과 산출이 이미 고른 저장소와 분석 프로젝트에 남아 있기 때문이다.
 * 영상 목록과 작업 단계는 지운 영상을 세지 않으므로 수만 쓰면 같은 화면의 영상 수와 어긋난다.
 *
 * @param target 바꿀 수 없는 대상의 이름(`저장소 대상`·`분석 대상`). 받침으로 끝나는 말이어야 한다.
 */
export function lockTitle(videoCount: number, deletedCount: number, target: string): string {
  const n = videoCount.toLocaleString("ko-KR");
  const deleted = deletedCount > 0 ? `(삭제 ${deletedCount.toLocaleString("ko-KR")}개 포함)` : "";
  return `영상 ${n}개${deleted}가 등록되어 ${target}을 바꿀 수 없습니다`;
}

/** 일시 오류 응답 뒤 다시 조회하기까지의 간격. */
export const TRANSIENT_RETRY_MS = 15_000;

/**
 * 일시 오류가 이어질 때 스스로 다시 조회하는 횟수의 상한.
 * 요청량 제한이 길게 이어지면 같은 요청을 계속 보내 제한을 늘리므로 몇 번 뒤에는 멈추고 사람에게 넘긴다.
 */
export const TRANSIENT_RETRY_MAX = 4;

/** 한 조회의 연속 일시 오류. `at`은 마지막으로 센 응답의 수신 시각, `n`은 이어진 일시 오류 응답 수다. */
export interface TransientStreak {
  at: number;
  n: number;
}

/** 일시 오류를 아직 한 번도 받지 않은 상태. */
export const NO_STREAK: TransientStreak = { at: 0, n: 0 };

/**
 * 새 응답으로 연속 일시 오류 수를 갱신한다.
 *
 * 조회의 재조회 주기 계산과 화면 그리기가 같은 응답을 두 번 읽을 수 있으므로, 같은 응답(`at`이 같음)으로
 * 여러 번 불러도 수가 늘지 않게 한다. 일시 오류가 아닌 응답을 받으면 0으로 돌아간다.
 *
 * @param prev 지금까지의 상태.
 * @param transient 이번 응답이 일시 오류(`error`와 `error_transient`가 모두 참)이면 참.
 * @param at 이번 응답의 수신 시각(`dataUpdatedAt`). 응답이 없으면 0.
 */
export function advanceStreak(prev: TransientStreak, transient: boolean, at: number): TransientStreak {
  if (!at || at === prev.at) return prev;
  return { at, n: transient ? prev.n + 1 : 0 };
}

/**
 * 다음 자동 재조회까지의 간격을 정한다. 일시 오류가 이어지는 동안 상한까지만 다시 부른다.
 *
 * Returns:
 *   다시 부를 간격(ms). 일시 오류가 아니거나 상한을 넘었으면 `false`.
 */
export function transientRetryDelay(s: TransientStreak): number | false {
  return s.n > 0 && s.n <= TRANSIENT_RETRY_MAX ? TRANSIENT_RETRY_MS : false;
}

/**
 * 분석 프로젝트 선택지 한 줄의 이름. 이름이 있으면 `이름 · id N`, 없으면 `id N`이다.
 * 서버 진단 줄(`platform_credentials.analysis_project_label`)과 같은 모양이다.
 */
export function projectOptionLabel(name: string | null | undefined, id: string): string {
  return name && name !== id ? `${name} · id ${id}` : `id ${id}`;
}
