/**
 * CSS 미디어쿼리 결과를 JS에서도 읽는다.
 *
 * 폭에 따라 클래스만 바뀌면 되는 경우는 Tailwind 접두사(`lg:`)로 충분하다.
 * 이 훅은 폭에 따라 상태가 바뀌어야 할 때 쓴다. 예를 들어 좁은 화면에서 사이드바를 접는 동작은 클래스만으로 할 수 없다.
 *
 * 창을 줄이거나 기기를 돌리면 값이 바뀌므로 변경을 구독한다.
 * 마운트 때 한 번만 읽으면 그 뒤로는 이전 값을 계속 쓴다.
 */
import { useEffect, useState } from "react";

/** 미디어쿼리가 지금 맞으면 true를 돌려준다. 결과가 바뀌면 다시 렌더한다. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    // SSR·구형 환경처럼 `matchMedia`가 없으면 false로 시작한다.
    () => window.matchMedia?.(query).matches ?? false,
  );

  useEffect(() => {
    const mql = window.matchMedia?.(query);
    if (!mql) return;
    setMatches(mql.matches);
    const on = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", on);
    return () => mql.removeEventListener("change", on);
  }, [query]);

  return matches;
}
