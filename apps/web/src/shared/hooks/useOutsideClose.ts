/**
 * 열린 메뉴·팝오버를 바깥 클릭과 Escape로 닫는다. 이 동작의 유일한 구현이다.
 *
 * 복사본은 한 곳을 고쳐도 나머지가 따라오지 않아, 어떤 창은 Escape로 닫히고 어떤 창은 그대로 남는다.
 * 그래서 이 동작을 한 곳에 모은다.
 *
 * 사용법:
 *
 * ```ts
 * useOutsideClose(open, ref, onClose);                        // 보통 메뉴·팝오버
 * useOutsideClose(open, btnRef, onClose, { extraRef: menuRef, reposition: true });
 * ```
 *
 * `extraRef`는 포털로 띄운 메뉴를 바깥 판정에서 뺀다. 빠뜨리면 `mousedown`이 먼저 메뉴를 언마운트해서
 * 메뉴 아이템의 `click`이 처리되지 않는다.
 * `reposition`을 켜면 `position: fixed` 메뉴를 스크롤·리사이즈 때 닫는다. 두 경우 모두 고정 좌표가 어긋나므로
 * 메뉴를 따라 옮기는 대신 둘 다 닫는다.
 * `returnFocus`는 Escape로 닫을 때 포커스를 돌려줄 트리거다. 포커스가 메뉴 안에 있었으면 메뉴와 함께
 * 사라져 `<body>`로 떨어지고, 다음 Tab이 문서 처음부터 다시 시작한다.
 * 포커스는 닫는 처리 안에서 바로 옮긴다. 다른 리스너나 효과에서 옮기면 React가 그 사이에 메뉴를 지우고
 * 효과를 정리해서 옮기는 코드가 실행되지 않는다.
 */
import { type RefObject, useEffect } from "react";

interface Options {
  /** 포털로 띄운 메뉴. 바깥 클릭 판정에서 뺀다. */
  extraRef?: RefObject<HTMLElement | null>;
  /** 스크롤·리사이즈 때 닫으려면 true(`position: fixed` 메뉴용). */
  reposition?: boolean;
  /** Escape로 닫을 때 포커스를 돌려줄 트리거. */
  returnFocus?: RefObject<HTMLElement | null>;
}

/** `contains`만 쓰는 노드. 테스트가 DOM 없이 판정을 부를 수 있게 좁힌다. */
interface NodeLike {
  contains(other: NodeLike | null): boolean;
}

/**
 * 닫힌 뒤 포커스가 갈 곳을 잃으면 true. 포커스가 없거나 `<body>`이거나 닫히는 메뉴 안에 있는 경우다.
 * 메뉴 밖의 다른 요소에 있는 포커스는 사용자가 옮긴 것이라 건드리지 않는다.
 */
export function focusWouldBeLost(
  active: NodeLike | null,
  body: NodeLike | null,
  containers: (NodeLike | null | undefined)[],
): boolean {
  if (!active || active === body) return true;
  return containers.some((c) => !!c && c.contains(active));
}

/** 열려 있는 동안 바깥 클릭·Escape를 받아 닫는다. `open`이 false면 리스너를 걸지 않는다. */
export function useOutsideClose(
  open: boolean,
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  { extraRef, reposition = false, returnFocus }: Options = {},
): void {
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || extraRef?.current?.contains(t)) return;
      onClose();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const lost = focusWouldBeLost(document.activeElement, document.body, [ref.current, extraRef?.current]);
      onClose();
      if (lost) returnFocus?.current?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    if (reposition) {
      // 스크롤은 버블링하지 않아서 버블 단계로는 안쪽 스크롤 컨테이너의 스크롤을 받지 못한다.
      // 그래서 캡처 단계에서 받는다.
      window.addEventListener("scroll", onClose, true);
      window.addEventListener("resize", onClose);
    }
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
      if (reposition) {
        window.removeEventListener("scroll", onClose, true);
        window.removeEventListener("resize", onClose);
      }
    };
  }, [open, ref, extraRef, onClose, reposition, returnFocus]);
}
