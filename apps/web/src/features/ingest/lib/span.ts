/**
 * 분석 요청 대상의 총 길이와 예상 비용 표기. 상태 타일과 확인 창이 같이 쓴다.
 *
 * 한 화면의 여러 곳이 같은 영상 묶음을 표시하므로 반올림 규칙을 이 파일 한 곳에 둔다.
 * 곳마다 다르게 반올림하면 같은 묶음이 "0분"과 "₩70"처럼 서로 어긋난 수로 보인다.
 */

/**
 * 초를 `5시간 12분`·`38분`·`25초` 꼴로 쓴다.
 *
 * 1분 미만은 초로 쓴다. 분으로 반올림하면 30초 미만 영상이 `0분`이 되어 비용이 붙은 대상의 길이가 없어 보인다.
 */
export function fmtHourMin(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  if (s < 60) return `${s}초`;
  const m = Math.round(secs / 60);
  const h = Math.floor(m / 60);
  return h > 0 ? `${h}시간 ${m % 60}분` : `${m}분`;
}

/**
 * 길이 합과 시간당 단가로 예상 분석 비용(원)을 낸다. 단가나 길이가 없으면 `null`이다.
 *
 * 서버의 비용 집계(`maiu.service._cost_core`)와 같은 산식이다. 초를 시간으로 바꿔 단가를 곱하고
 * 마지막에 한 번 원 단위로 반올림한다. 정확히 절반이면 짝수 쪽으로 반올림한다(파이썬 `round`와 같다).
 * 산식이 다르면 같은 영상의 금액이 이 화면과 통계 화면의 남은 비용에서 서로 다르게 뜬다.
 */
export function costKrw(secs: number, rate: number | null | undefined): number | null {
  if (rate == null || secs <= 0) return null;
  const x = (secs / 3600) * rate;
  const floor = Math.floor(x);
  const frac = x - floor;
  if (frac > 0.5) return floor + 1;
  if (frac < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}
