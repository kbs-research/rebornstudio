/**
 * 초(seconds) 값을 사람이 읽기 편한 문자열로 바꾼다.
 *
 * 값이 null·NaN·undefined이면 `—`를 쓴다.
 * 60초 미만은 소수점 한 자리의 `X.X초`, 60초 이상은 분과 두 자리 초의 `M:SS`로 쓴다.
 * 60초 미만의 단위는 한국어다.
 *
 * @example fmtSecs(5.25) → "5.3초"
 * @example fmtSecs(75)   → "1:15"
 * @example fmtSecs(0)    → "0.0초"
 * @example fmtSecs(null) → "—"
 */
export function fmtSecs(s: number | null | undefined): string {
  if (s == null || isNaN(s)) return "—";
  if (s < 60) return `${s.toFixed(1)}초`;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

/**
 * 초를 한국어 단위로 표기한다. 통계 화면의 속도 열이 쓰는 형식이다.
 *
 * `fmtSecs`(`1:15`·`5.3초`, 워크벤치용 축약형)와 다른 형식이다.
 * 통계는 `1.5시간`·`1분 15초`처럼 단위를 붙여 한눈에 크기를 읽게 한다.
 * 두 화면이 쓰는 형식이 달라서 한 함수로 합치지 않는다.
 *
 * 이 표기의 구현은 이 함수 하나다.
 * 같은 사람의 같은 속도가 두 곳에서 다르게 그려지지 않도록 로컬 사본을 두지 않는다.
 */
export function fmtSecKo(sec: number | null | undefined): string {
  if (sec == null) return "—";
  if (sec >= 86400) return `${(sec / 86400).toFixed(1)}일`;
  if (sec >= 3600) return `${(sec / 3600).toFixed(1)}시간`;
  /* 분 단위는 초를 먼저 반올림한 뒤 나눈다. 나머지를 따로 반올림하면 119.6초가 `1분 60초`가 된다.
     초가 0이면 `0초`를 붙이지 않는다. 2400초는 `40분`이다. */
  const whole = Math.round(sec);
  if (whole >= 60) {
    const m = Math.floor(whole / 60);
    const s = whole % 60;
    return s ? `${m}분 ${s}초` : `${m}분`;
  }
  return `${whole}초`;
}

/**
 * 걸린 시간·분량을 한국어 단위로 표기한다. 준비 소요·Job 총 길이·등록 대상 길이가 쓴다.
 *
 * `fmtSecKo`(`1.5시간`, 통계의 소수 표기)와 달리 시·분·초로 나눠 표기한다(`1시간 31분`).
 * 재생 길이(영상 안의 시계)는 `fmtTimecode`가 맡는다.
 * 이 함수는 걸린 시간을, `fmtTimecode`는 영상 안의 길이와 위치를 표기한다.
 * 한 등록 흐름 안의 시트·Job 목록·행이 모두 이 표기를 쓴다.
 *
 * @example fmtSpan(12)    → "12초"
 * @example fmtSpan(254)   → "4분 14초"
 * @example fmtSpan(5460)  → "1시간 31분"
 * @example fmtSpan(null)  → "—"
 */
export function fmtSpan(sec: number | null | undefined): string {
  if (sec == null || isNaN(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m}분 ${s % 60}초` : `${m}분`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
}

/**
 * 초를 타임코드로 바꾼다. `withHours`면 `1:21:38`, 아니면 `21:38`이다.
 * 구간 양끝이 같은 폭이어야 비교할 수 있다.
 */
function timecode(s: number, withHours: boolean): string {
  const t = Math.max(0, Math.floor(s));
  const ss = String(t % 60).padStart(2, "0");
  if (!withHours) return `${Math.floor(t / 60)}:${ss}`;
  const mm = String(Math.floor(t / 60) % 60).padStart(2, "0");
  return `${Math.floor(t / 3600)}:${mm}:${ss}`;
}

/** 초를 소수 한 자리 타임코드로 바꾼다. 초 자리까지는 `timecode`와 같고 뒤에 `.1`초를 붙인다(버림). */
function timecodeTenths(s: number, withHours: boolean): string {
  const t = Math.max(0, s);
  const tenth = Math.min(9, Math.floor((t - Math.floor(t)) * 10 + 1e-6));
  return `${timecode(t, withHours)}.${tenth}`;
}

/**
 * 클립 구간(시작–끝)을 타임코드 한 가지 형식으로 표기한다.
 *
 * 구간은 늘 `m:ss–m:ss`다. 1시간을 넘으면 양끝 모두 시간 단위를 붙인다(`0:58:20–1:21:38`).
 * 분으로만 적으면 `81:38`이 되어 영상이 길수록 읽기 어렵다.
 * 이 도구는 영상 길이와 무관하게 동작해야 한다.
 *
 * 시작과 끝이 같은 초로 적히는 짧은 구간만 소수 한 자리를 붙인다(`0:02.2–0:02.9`).
 * 그러지 않으면 `0:02–0:02`로 합쳐져 시작과 끝이 같아 보인다.
 *
 * 양끝에 `fmtSecs`를 따로 부르면 60초 경계를 걸친 구간이 `12.0초–1:15`처럼
 * 한 목록 안에서 두 형식으로 섞인다.
 *
 * @example fmtClipRange(1298, 1398) → "21:38–23:18"
 * @example fmtClipRange(1, 55)      → "0:01–0:55"
 * @example fmtClipRange(2.2, 2.9)   → "0:02.2–0:02.9"
 * @example fmtClipRange(12, 75)     → "0:12–1:15"
 * @example fmtClipRange(3500, 4898) → "0:58:20–1:21:38"
 */
export function fmtClipRange(
  start: number | null | undefined,
  end: number | null | undefined,
): string {
  if (start == null || end == null || isNaN(start) || isNaN(end)) return "—";
  const withHours = Math.max(start, end) >= 3600;
  const a = timecode(start, withHours);
  const b = timecode(end, withHours);
  if (a !== b) return `${a}–${b}`;
  return `${timecodeTenths(start, withHours)}–${timecodeTenths(end, withHours)}`;
}

/**
 * 영상 안의 한 지점을 타임코드로 표기한다. 썸네일 시각처럼 구간이 아닌 값에 쓴다.
 *
 * `fmtSecs`(길이용)와 달리 12초 지점을 `12.0초` 대신 `0:12`로 쓴다.
 * `fmtSecs`는 길이를, 이 함수는 위치를 표기한다. 구간은 `fmtClipRange`를 쓴다.
 */
export function fmtTimecode(s: number | null | undefined): string {
  if (s == null || isNaN(s)) return "—";
  return timecode(s, s >= 3600);
}

/**
 * 초를 `mm:ss.s`로 표기한다. 문장 구간처럼 0.1초가 의미 있는 값의 유일한 구현이다.
 *
 * `fmtTimecode`(`21:38`)와 자릿수가 다르다. `fmtTimecode`는 영상의 위치를 가리킨다.
 * 이 함수는 사람이 찍은 경계를 표기하므로 소수 한 자리가 판단 근거가 된다.
 * 프레임 단위까지는 표기하지 않는다.
 *
 * `fmtSecKo`와 같은 이유로 로컬 사본을 두지 않는다. 같은 값을 두 곳에서 각자 그리면 결과가 달라진다.
 *
 * @example fmtClock(1220.94) → "20:20.9"
 * @example fmtClock(3.4)     → "0:03.4"
 */
export function fmtClock(sec: number | null | undefined): string {
  const s = Math.max(0, Number(sec) || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

/**
 * 분량 값의 소수 자릿수를 정한다. 값이 작을수록 소수를 더 보여 준다.
 * `0.22`는 두 자리가 있어야 의미가 있고 `123`에서는 소수가 잡음이다.
 */
function hoursDigits(h: number): number {
  return h >= 100 ? 0 : h >= 10 ? 1 : 2;
}

/**
 * 시간을 `12.34시간`·`12.3시간`·`123시간`으로 표기한다.
 * 분량 표기의 유일한 구현이다.
 *
 * 통계 화면에서 분량을 시간으로 표시하는 곳은 모두 이 함수를 쓴다.
 * 표 열이 조금 넓어지는 것은 감수한다.
 *
 * `fmtSecKo`(`1.5시간`)와 다르다.
 * `fmtSecKo`는 초를 받아 크기에 따라 일·시간·분·초로 나눠 표기한다.
 * 이 함수는 이미 시간 단위인 수를 자릿수만 맞춰 적는다(분량은 항상 시간 단위다).
 *
 * 한국어 화면에는 영문 단위(`12.3h`) 대신 `시간`을 쓴다.
 * 같은 화면의 속도 열(`1분 15초`)과 설정도 `시간`을 쓴다.
 */
export function fmtH(h: number): string {
  return `${h.toLocaleString("ko-KR", { maximumFractionDigits: hoursDigits(h) })}시간`;
}
