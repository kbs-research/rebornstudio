/** 저장하지 않은 편집을 브라우저 이탈에서 지키는 훅. */
import { useEffect, useRef } from "react";

/**
 * 저장하지 않은 편집이 있으면 탭 닫기·새로고침 때 경고한다.
 *
 * 워크벤치 자동저장은 debounce(라벨링 30초·검수 2초)라서, 그 사이 창을 닫거나 새로고침하면 마지막 편집이 사라진다.
 *
 * `hasUnsaved`는 이벤트가 일어난 시점에 읽으므로 리렌더가 필요 없다.
 * 렌더마다 바뀌는 인라인 게터를 넘겨도 리스너가 한 번만 등록되도록 ref로 최신 게터를 가리킨다.
 *
 * 이 훅은 브라우저를 떠나는 경우만 다룬다. 앱 내부 이동(라우터 링크)은 경고 대신
 * 워크벤치가 이동 직전에 flush(즉시 저장)한다.
 */
export function useUnsavedGuard(hasUnsaved: () => boolean) {
  const ref = useRef(hasUnsaved);
  ref.current = hasUnsaved;
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!ref.current()) return;
      e.preventDefault();
      e.returnValue = ""; // 표준 방식. 브라우저가 자체 확인 문구를 띄운다.
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);
}
