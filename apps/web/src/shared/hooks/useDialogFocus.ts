/**
 * 열린 대화상자 안에 포커스를 가둔다. `Modal`·`Drawer`가 함께 쓴다.
 *
 * `role="dialog" aria-modal="true"`는 나머지 페이지가 비활성이라는 뜻인데, DOM 포커스는 이 속성만으로 갇히지 않는다.
 * 그러면 열었을 때 포커스가 트리거 버튼(대화상자 밖)에 남아, 사용자가 대화상자의 컨트롤에 닿기 전에
 * 뒤쪽 요소를 모두 지나야 하고 가림막에 가려 보이지 않는 요소를 누를 수도 있다.
 *
 * 워크벤치는 셸에 `inert`를 걸지만 `Modal`·`Drawer`는 포털 대신 자기를 연 화면 안에 렌더된다.
 * 그래서 배경에 `inert`를 걸면 대화상자도 함께 비활성이 되고, 포털로 옮기려면 모든 호출부의 렌더 위치를 바꿔야 한다.
 * 이 훅은 대신 Tab 이동을 가로챈다.
 *
 * 열리면 안쪽 첫 컨트롤로, 컨트롤이 없으면 패널 자체(`tabIndex={-1}`)로 포커스를 옮긴다.
 * `Tab`·`Shift+Tab`은 패널 안에서만 돌고, 끝에서 누르면 반대쪽 끝으로 간다.
 * 닫히면 열기 전에 있던 곳으로 포커스를 돌려놓는다.
 * 돌려놓지 않으면 포커스가 `<body>`로 떨어져 다음 Tab이 화면 맨 위에서 다시 시작한다.
 */
import { useEffect, type RefObject } from "react";

/**
 * 누를 수 있는 요소의 선택자. 숨은 요소에 포커스를 주면 화면이 따라가지 않으므로
 * 보이지 않는 요소는 `focusables`가 한 번 더 거른다.
 */
const SELECTOR =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** 패널 안에서 보이고 누를 수 있는 요소를 문서 순서대로 모은다. */
function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(SELECTOR)].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  });
}

/**
 * 대화상자가 열려 있는 동안 포커스를 패널 안에 가두고, 닫히면 원래 위치로 돌려놓는다.
 *
 * @param open 대화상자가 열려 있으면 true. 닫히면 포커스를 되돌린다.
 * @param panelRef 포커스를 가둘 패널. `Modal`은 카드, `Drawer`는 오른쪽 패널이다.
 */
export function useDialogFocus(open: boolean, panelRef: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;

    // 열기 전 포커스 위치를 기억한다. 닫을 때 이곳으로 돌려놓는다.
    const opener = document.activeElement as HTMLElement | null;

    // 첫 포커스는 첫 컨트롤에 주고, 컨트롤이 없으면 패널에 준다.
    // 패널은 `tabIndex={-1}`이라 탭 순서에 들지 않는다.
    const first = focusables(panel)[0];
    (first ?? panel).focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const items = focusables(panel);
      if (!items.length) {
        // 누를 요소가 없으면 포커스가 밖으로 나가지 않도록 패널에 둔다.
        e.preventDefault();
        panel.focus();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // 포커스가 패널 밖에 있으면(트리거에서 눌렀거나 배경을 클릭한 경우) 안쪽 끝으로 옮긴다.
      if (!active || !panel.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? lastEl : firstEl).focus();
        return;
      }
      if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      } else if (e.shiftKey && active === firstEl) {
        e.preventDefault();
        lastEl.focus();
      }
    };

    // 캡처 단계에서 받는다. 안쪽 컴포넌트가 Tab을 먼저 처리해도 포커스 가둠이 유지된다.
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      // 사라진 요소에 포커스를 주면 오류 없이 `<body>`로 떨어진다.
      // 그래서 요소가 DOM에 붙어 있을 때만 되돌린다.
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open, panelRef]);
}
