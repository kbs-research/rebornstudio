/**
 * 재생기의 소스·구간이 바뀔 때 다시 할 일을 정한다. `VideoPlayer`가 쓴다.
 *
 * 구간(클립 시작·끝)이 늦게 오면 재생기는 이미 영상 전체로 열려 있고, 그 뒤 구간 값을 바꿔도 재생기에 반영되지 않는다.
 * 그래서 구간마다 재생기를 따로 만든다(`playerKey`).
 * 재생 주소(presigned URL)는 만료 전에 새로 받는데, 주소가 바뀌면 미디어가 처음부터 다시 열린다.
 * 그래서 바뀌기 직전의 위치와 일시정지 상태로 되돌린다(`resumeSeek`).
 * 렌더 없이 테스트하려고 컴포넌트 밖에 둔다.
 */

/**
 * 재생기 인스턴스를 가르는 키를 만든다. 구간이 바뀌면 키가 바뀌어 재생기를 새로 만든다.
 *
 * Args:
 *   start: 클립 시작(초). 값이 없으면 `undefined`.
 *   end: 클립 끝(초). 값이 없으면 `undefined`.
 *
 * Returns:
 *   구간이 있으면 `clip:시작:끝`, 없으면 `full`.
 */
export function playerKey(start: number | undefined, end: number | undefined): string {
  return start != null && end != null && end > start ? `clip:${start}:${end}` : "full";
}

/** 재생 주소가 바뀌기 직전의 재생 상태. 위치는 원본 `<video>`의 절대 초다. */
export interface PlaybackSnapshot {
  abs: number;
  paused: boolean;
}

/**
 * 재생 주소가 바뀐 뒤 되돌아갈 위치를 상대 초로 구한다.
 *
 * Args:
 *   prevSrc: 바뀌기 전 주소. 첫 로드면 `undefined`.
 *   nextSrc: 새 주소.
 *   last: 바뀌기 직전의 재생 상태. 재생한 적이 없으면 `null`.
 *   clipStart: 클립 시작(초). 상대 API는 클립 시작을 0으로 센다. 구간이 없으면 0.
 *
 * Returns:
 *   같은 영상의 주소만 바뀌었고 재생한 위치가 있으면 `{ seek, resume }`, 아니면 `null`.
 *   `resume`이 참이면 다시 재생한다.
 */
export function resumeSeek(
  prevSrc: string | undefined,
  nextSrc: string | undefined,
  last: PlaybackSnapshot | null,
  clipStart: number,
): { seek: number; resume: boolean } | null {
  if (!prevSrc || !nextSrc || prevSrc === nextSrc || !last) return null;
  return { seek: Math.max(0, last.abs - clipStart), resume: !last.paused };
}

/**
 * 재생이 다시 준비되기 전에 주소를 새로 받는 횟수의 상한.
 * 새 주소로도 같은 오류가 나면 원인은 주소 만료가 아닌 파일이나 저장소 문제라서 더 받지 않고 안내한다.
 */
export const MEDIA_REFRESH_MAX = 1;

/**
 * 미디어 오류에 무엇을 할지 정한다.
 *
 * 재생 주소는 서명 시각이 지나면 403 응답을 받는다. 탭이 오래 숨겨졌거나 절전에서 깨어나면 주기 갱신보다
 * 만료가 먼저 와서, 탐색이나 다음 범위 요청이 403으로 끝나고 재생기가 멈춘다.
 * 브라우저는 이 403을 네트워크 오류(2)나 소스 오류(4)로만 전달하므로 만료와 다른 실패를 구분할 수 없다.
 * 그래서 두 코드에서는 주소를 한 번 새로 받고, 그래도 실패하면 안내한다.
 *
 * Args:
 *   code: `MediaError.code`. 1 중단 · 2 네트워크 · 3 디코딩 · 4 소스. 값이 없으면 `undefined`.
 *   refreshes: 재생이 마지막으로 준비된 뒤 주소를 새로 받은 횟수.
 *
 * Returns:
 *   `ignore`는 사용자가 멈춘 중단, `refresh`는 주소를 새로 받는다, `fail`은 안내를 띄운다.
 */
export function mediaErrorAction(code: number | undefined, refreshes: number): "ignore" | "refresh" | "fail" {
  if (code === 1) return "ignore";
  if (code === 3) return "fail";
  return refreshes < MEDIA_REFRESH_MAX ? "refresh" : "fail";
}
