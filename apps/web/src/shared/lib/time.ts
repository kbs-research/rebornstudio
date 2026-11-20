/**
 * 화면에 쓰는 날짜·시각 표기의 단일 출처. 모든 화면이 이 파일의 함수로 날짜와 시각을 쓴다.
 *
 * 쓰는 곳에 따라 함수를 고른다.
 * - 언제 일어났나(기록·보유 작업): `whenLabel`. 7일 안은 `3시간 전`, 그 뒤는 `9월 18일`이다.
 * - 얼마나 지났나(큐의 경과): `relativeTime`. `12일 전`처럼 날짜로 바꾸지 않는다.
 * - 날짜를 서로 견주는 목록·상세: `fmtDateTime`. `9월 23일 18:42`이다.
 * - 날짜만: `fmtDate`. `9월 23일`이다.
 * - 날짜로 묶은 목록: 묶음 머리는 `dayGroupLabel`(`오늘`·`어제`·`9월 24일`), 행은 `fmtTime`(`14:02`)이다.
 * - 정확한 시각이 필요한 툴팁: `fmtFullDateTime`. `2026년 9월 23일 18:42:45`이다.
 * - 감사 로그·서버 로그: `features/audit`의 `fmtEventTime`(`2026-09-23 18:42:45`)이다. 줄을 맞춰 대조하는 장부라 예외다.
 * - 입력란 값: `YYYY-MM-DD`(`shared/lib/ymd`)다.
 *
 * 시각은 24시간제로 쓰고 `오전`·`오후`를 쓰지 않는다. 올해 날짜는 연도를 빼고, 다른 해면 붙인다.
 * 시간대는 보는 사람의 브라우저 시간대다. 집계 구간의 시간대 이름은 서버가 준다.
 *
 * 주의: 화면에서 `toLocaleString("ko-KR")`로 날짜를 직접 쓰지 않는다.
 * 기본 꼴(`2026. 9. 23. 오후 6:42:45`)이 이 규칙과 달라 화면마다 표기가 달라진다.
 */

/** 두 자리로 채운다. */
const pad2 = (n: number) => String(n).padStart(2, "0");

/**
 * ISO 시각이나 `YYYY-MM-DD` 날짜를 `Date`로 바꾼다.
 *
 * 날짜만 오면 로컬 자정으로 읽는다. `new Date("2026-09-01")`은 UTC 자정이라
 * 서쪽 시간대에서는 하루 앞 날짜가 된다.
 */
function toDate(v: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(v);
}

/** 로컬 기준으로 같은 날이면 true를 돌려준다. */
function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/**
 * ISO 시각을 상대 시간(`방금 전`·`n분 전`·`n시간 전`·`n일 전`)으로 바꾼다.
 * 큐처럼 경과 자체가 판단 재료인 곳에서 쓴다.
 */
export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}시간 전`;
  return `${Math.floor(hrs / 24)}일 전`;
}

/** 월·일로 쓴다. 같은 해면 연도를 빼고, 다른 해면 붙인다. `9월 10일` · `2025년 9월 10일`. */
function dateWords(d: Date, now: Date): string {
  const md = `${d.getMonth() + 1}월 ${d.getDate()}일`;
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}년 ${md}`;
}

/** 24시간제 시각(`14:37`)으로 쓴다. */
function hm(d: Date): string {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/**
 * 활동 기록처럼 언제 일어났나를 쓰는 곳의 시각을 만든다. 7일 안이면 상대 시간, 그 뒤는 날짜다.
 *
 * 큐는 `12일 전`처럼 경과 일수가 판단 재료라 날짜로 바꾸지 않고, 기록은 날짜를 알아야 대조할 수 있다.
 * 그래서 `relativeTime`과 함수를 나눈다.
 * 화면마다 상대 시간 함수를 따로 두면 `방금`·`방금 전`, `어제`·`1일 전`처럼 표기가 달라진다.
 *
 * @example whenLabel(3분 전) // "3분 전"
 * @example whenLabel(10일 전, 같은 해) // "9월 18일"
 */
export function whenLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const mins = Math.floor((now.getTime() - d.getTime()) / 60_000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}시간 전`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}일 전`;
  return dateWords(d, now);
}

/**
 * 날짜와 시각을 24시간제로 쓴다. `9월 10일 14:37`(같은 해) · `2025년 9월 10일 14:37`.
 * 날짜를 서로 견줘야 하는 목록과 상세에서 쓴다.
 */
export function fmtDateTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  return `${dateWords(d, now)} ${hm(d)}`;
}

/**
 * 날짜를 쓴다. `9월 23일`(같은 해) · `2025년 12월 31일`.
 * ISO 시각도, `YYYY-MM-DD` 날짜도 받는다. 날짜만 오면 로컬 날짜 그대로 쓴다.
 */
export function fmtDate(value: string, now: Date = new Date()): string {
  return dateWords(toDate(value), now);
}

/** 시각만 쓴다(`14:02`). 날짜로 묶은 목록의 행에서 쓴다. 날짜는 묶음 머리(`dayGroupLabel`)가 표시한다. */
export function fmtTime(iso: string): string {
  return hm(new Date(iso));
}

/**
 * 연도와 초까지 쓴 시각을 만든다. `2026년 9월 23일 18:42:45`.
 * 툴팁처럼 짧은 표기 뒤의 정확한 값을 보여 주는 곳에서 쓴다. 대조하려면 연도까지 있어야 한다.
 */
export function fmtFullDateTime(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 ${hm(d)}:${pad2(d.getSeconds())}`;
}

/**
 * 날짜로 묶은 목록의 묶음 이름을 만든다. `오늘` · `어제` · `9월 24일` · `2025년 12월 31일`.
 * 묶음은 보는 사람의 로컬 날짜로 자른다.
 */
export function dayGroupLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (sameDay(d, now)) return "오늘";
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(d, yesterday)) return "어제";
  return dateWords(d, now);
}
