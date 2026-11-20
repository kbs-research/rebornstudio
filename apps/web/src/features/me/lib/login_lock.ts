/**
 * 내 계정 로그인 기록의 로그인 잠금 줄 표기.
 *
 * 서버가 이메일 단위 잠금을 건 실패에서 남기는 `auth.login_locked`의 내용(`via`·`failures`·`lock_seconds`)을
 * 보조 문구로 바꾼다. 잠금 기준은 서버가 정하므로 화면은 실린 값만 옮겨 적는다.
 */

/** 로그인 잠금을 건 경로(서버 `auth.service._record_email_lock`의 `via`)의 화면 이름. */
const LOCK_VIA: Record<string, string> = {
  login: "로그인",
  password_change: "비밀번호 변경",
};

/** 로그인 잠금 기록의 내용 중 보조 문구에 쓰는 값. */
export interface LockPayload {
  via?: string | null;
  failures?: number | null;
  lock_seconds?: number | null;
}

/**
 * 로그인 잠금 줄의 보조 문구를 만든다. 예: `비밀번호 변경 실패 50회 · 15분 잠금`.
 * 서버가 실은 값만 쓴다. 값이 없으면 그 부분을 빼고, 모두 없으면 보조 문구를 그리지 않는다.
 * 사전에 없는 경로는 경로 이름 없이 횟수만 쓴다. 코드를 그대로 보여 주지 않는다.
 */
export function lockDetail(p: LockPayload): string | undefined {
  const via = p.via ? LOCK_VIA[p.via] : undefined;
  const fails = typeof p.failures === "number" ? `실패 ${p.failures}회` : undefined;
  const failPart = via && fails ? `${via} ${fails}` : fails;
  const lock = typeof p.lock_seconds === "number" ? `${Math.round(p.lock_seconds / 60)}분 잠금` : undefined;
  return [failPart, lock].filter(Boolean).join(" · ") || undefined;
}
