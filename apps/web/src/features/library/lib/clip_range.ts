/**
 * 한 목록 안의 클립 구간을 같은 형식으로 적는다. 영상 상세 클립 목록과 클립 보기가 쓴다.
 *
 * `shared/lib/fmtSecs.fmtClipRange`는 행마다 형식을 고른다(1분 미만은 `0.0–21.6s`, 넘으면 `0:58–1:19`).
 * 한 목록에서 형식이 섞이면 세로로 비교하기 어려워서, 이 모듈은 목록의 가장 늦은 끝으로 형식을 한 번 정한다.
 * 1시간 미만이면 `m:ss.s`(0.1초까지), 1시간 이상이면 `h:mm:ss`다. 영문 단위(`s`)는 쓰지 않는다.
 * 0.1초까지 적어 짧은 클립의 시작과 끝이 `0:02–0:02`처럼 같아 보이지 않게 한다.
 */

/** 초를 `m:ss.s`로 적는다. 0.1초 단위로 반올림한 뒤 분을 올린다(59.96초는 `1:00.0`). */
function minSec(t: number): string {
  const tenths = Math.round(Math.max(0, t) * 10);
  const m = Math.floor(tenths / 600);
  const sec = (tenths - m * 600) / 10;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

/** 초를 `h:mm:ss`로 적는다. 초 아래는 버린다. */
function hourMinSec(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const mm = String(Math.floor(s / 60) % 60).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${Math.floor(s / 3600)}:${mm}:${ss}`;
}

/**
 * 목록 하나에 쓸 구간 서식 함수를 만든다.
 *
 * Args:
 *   maxEnd: 목록에서 가장 늦은 끝(초). 이 값으로 형식을 고른다.
 *
 * Returns:
 *   `(start, end) => "0:21.6–0:41.3"` 형식의 함수. 값이 없으면 `—`다.
 */
export function clipRangeFormatter(maxEnd: number): (
  start: number | null | undefined,
  end: number | null | undefined,
) => string {
  const f = maxEnd >= 3600 ? hourMinSec : minSec;
  return (start, end) => {
    if (start == null || end == null || Number.isNaN(start) || Number.isNaN(end)) return "—";
    return `${f(start)}–${f(end)}`;
  };
}
