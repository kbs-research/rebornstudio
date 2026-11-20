/**
 * 재생기 오류 화면의 문구와 `다시 시도` 여부를 고른다. `components/VideoPlayer.tsx`가 쓴다.
 *
 * 사유는 오류 사전(`shared/api/error_text`)이 정한다. 다시 시도할 가치가 있는지는 전역 쿼리 재시도·`ErrorState`와 같은
 * `isRetryable`이 정한다. 409 `invalid_source_uri`처럼 다시 해도 같은 실패에는 `다시 시도`를 두지 않는다.
 *
 * 원본을 받을 수 없는 사람(라벨러)이 받은 `invalid_source_uri`는 뜻이 다르다. 비식별화가 필요한 영상은 원본을 주지 않으므로
 * (`_may_play_original`) 가린 재생본이 아직 없다는 뜻이고, 준비가 끝나면 같은 요청이 성공한다.
 */
import { errorText, isRetryable } from "@/shared/api/error_text";

/** 사전에 없는 실패의 문구. 원인을 확인하지 못한 실패라서 HTTP 코드를 함께 표시한다. */
export const UNKNOWN_REASON = "네트워크 또는 저장소 연결에 문제가 있습니다.";

/**
 * 재생기 화면에서 사전 문구 대신 쓰는 원인. 사전 문구가 제목과 같은 말을 되풀이하는 코드에 쓴다.
 * `invalid_source_uri`는 재생본이 없고 원본도 가리킬 수 없을 때다(`services/media/resolver.playback_source`).
 */
const PLAYER_CAUSE: Record<string, string> = {
  invalid_source_uri: "재생본이 없고 원본 파일도 연결되어 있지 않습니다.",
  video_not_found: "삭제되었거나 주소가 잘못된 영상입니다.",
};

/** 문장 끝에 마침표를 붙인다. 사전의 문구는 제목에도 쓰여 마침표가 없다. */
function sentence(s: string): string {
  return /[.!?]$/.test(s) ? s : `${s}.`;
}

/**
 * 재생기 오류 화면을 고른다.
 *
 * 제목은 명사구다. 다시 해도 같은 실패(403·404·409)에는 `다시 시도`를 주지 않는다.
 * `opts.originalBlocked`는 원본을 받을 수 없는 역할이면 참이다. 그때의 `invalid_source_uri`는 재생본 준비 중으로 그린다.
 * HTTP 코드는 원인을 확인하지 못한 실패에만 붙인다. 원인을 아는 실패에 코드를 붙이면 고장처럼 읽힌다.
 *
 * Returns:
 *   `title`은 제목, `reason`은 원인 문장, `hint`는 할 일, `retry`는 다시 시도 버튼을 그리면 참,
 *   `code`는 함께 표시할 HTTP 코드(원인을 확인하지 못한 실패에서만)다.
 */
export function playerErrorView(
  err: { status?: number; detail?: unknown } | null | undefined,
  opts: { originalBlocked?: boolean } = {},
): {
  title: string;
  reason: string;
  hint?: string;
  retry: boolean;
  code?: number;
} {
  const status = err?.status;
  const detail = typeof err?.detail === "string" ? err.detail : undefined;
  // 원본을 받을 수 없는 사람에게 재생본이 없으면 준비 중이다. 준비가 끝나면 풀리므로 다시 시도를 준다.
  if (opts.originalBlocked && detail === "invalid_source_uri") {
    return {
      title: "재생본 준비 중",
      reason: "재생본을 준비하는 중입니다.",
      hint: "준비가 끝나면 재생할 수 있습니다. 잠시 후 다시 시도해주세요.",
      retry: true,
    };
  }
  const { message, hint } = errorText(err?.detail, { message: UNKNOWN_REASON });
  const known = message !== UNKNOWN_REASON;
  const retry = isRetryable(status);
  const title = retry
    ? "영상 조회 실패"
    : status === 403
      ? "볼 수 없는 영상"
      : status === 404
        ? "영상 없음"
        : "재생할 영상 없음";
  return {
    title,
    reason: (detail && PLAYER_CAUSE[detail]) || sentence(message),
    hint: hint ? sentence(hint) : undefined,
    retry,
    code: !known && status != null ? status : undefined,
  };
}
