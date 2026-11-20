/**
 * `YYYY-MM-DD` 문자열과 로컬 Date를 서로 바꾼다. 날짜 입력·필터가 함께 쓰는 유일한 구현이다.
 *
 * 날짜는 로컬 기준으로 계산한다. `toISOString().slice(0,10)`은 UTC 기준이라
 * KST 오전 9시 이전에 하루가 밀리고, 달력에서 고른 날과 서버에 보낸 날이 달라진다.
 * 그래서 로컬 `getFullYear` 계열로 만들고, 파싱도 `new Date(y, m-1, d)`(로컬 자정)로 되돌린다.
 *
 * 날짜를 만드는 규칙이 여러 곳에 있으면 한 곳만 고쳐져 달력과 필터가 다른 날을 가리킨다.
 */

/** 로컬 Date를 `YYYY-MM-DD`로 바꾼다. */
export function toYmd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** `YYYY-MM-DD`를 로컬 Date(자정)로 바꾼다. 빈 문자열이면 `null`. */
export function parseYmd(s: string): Date | null {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/**
 * 실제로 있는 날짜의 `YYYY-MM-DD`이면 true를 돌려준다.
 *
 * 주소에서 받은 값을 `new Date(...).toISOString()`에 넣기 전에 거른다. `abc`나 `2026-13-45`가
 * 그대로 들어가면 `RangeError`가 나서 화면 전체가 열리지 않는다. `2026-02-30`처럼 모양만 맞는
 * 날짜는 다음 달로 넘어가므로 되돌려 같은지 본다.
 */
export function isValidYmd(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = parseYmd(s);
  return !!d && toYmd(d) === s;
}

/**
 * 주소의 기간(시작·끝)을 쓸 수 있는 값으로 고친다. 틀린 날짜는 버리고, 시작이 끝보다 늦으면 맞바꾼다.
 *
 * Returns:
 *   고친 시작·끝. 둘 다 비었거나 `YYYY-MM-DD`다.
 */
export function cleanYmdRange(from: string, to: string): { from: string; to: string } {
  const f = isValidYmd(from) ? from : "";
  const t = isValidYmd(to) ? to : "";
  return f && t && f > t ? { from: t, to: f } : { from: f, to: t };
}
