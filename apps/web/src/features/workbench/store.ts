/**
 * 워크벤치 화면이 문서 루트에 다는 면 범위.
 */
import { useEffect } from "react";

/**
 * 워크벤치(라벨링·검수·완료 보기)가 마운트된 동안 문서 루트에 흑연색 면 범위를 단다.
 *
 * 워크벤치는 앱 테마와 관계없이 흑연색이고 테마 전환은 없다.
 * 워크벤치 루트의 `data-surface="workbench"`는 그 요소의 자손에만 적용되므로, 문서 루트의 `color-scheme`과
 * 루트 밖에 그려지는 요소는 앱 테마를 따른다. 그래서 마운트된 동안 `<html>`에도 같은 속성을 단다.
 * `Modal`·`Drawer`는 포털 없이 그려져 워크벤치 루트 안에 있고, 토스트와 라이트박스는 자기 요소의 `data-surface="graphite"`로 흑연색이 된다.
 * 앱 테마(`data-theme`)는 바꾸지 않으므로 워크벤치를 나가면 속성만 떼면 된다.
 */
export function useWorkbenchSurface() {
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.surface = "workbench";
    return () => {
      delete root.dataset.surface;
    };
  }, []);
}
