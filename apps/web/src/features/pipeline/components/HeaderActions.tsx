/**
 * 작업 단계 화면 머리의 오른쪽 영역(갱신 시각·주 버튼)을 단계 패널이 채우게 한다.
 *
 * 머리는 작업 단계 화면(`pages/pipeline.tsx`)이 그리고, 그 영역에 둘 내용은 활성 패널이 정한다.
 * 패널은 `HeaderActions`로 감싸기만 하면 머리 영역으로 옮겨 그려진다.
 * 같은 패널을 머리가 없는 화면(개인 작업공간)에 넣으면 제자리에 그린다.
 */
import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/shared/lib/cn";

/** `inside`는 작업 단계 화면 안이면 true, `el`은 머리 영역 요소(첫 렌더에는 아직 없다). */
interface Slot {
  inside: boolean;
  el: HTMLElement | null;
}

/** 머리 영역 요소를 패널에 전하는 컨텍스트. */
const SlotContext = createContext<Slot>({ inside: false, el: null });

/** 작업 단계 화면이 머리 영역 요소를 내려 준다. */
export const HeaderSlotProvider = SlotContext.Provider;

/**
 * 머리 영역에 자식을 그린다. 작업 단계 화면 밖이면 `className`을 단 줄에 제자리로 그린다.
 * 작업 단계 화면 안에서 영역 요소가 아직 없으면 그리지 않는다. 한 프레임 제자리에 떴다 옮겨 가는 깜빡임을 막는다.
 */
export function HeaderActions({ children, className }: { children: ReactNode; className?: string }) {
  const slot = useContext(SlotContext);
  if (slot.inside) return slot.el ? createPortal(children, slot.el) : null;
  return <div className={cn("flex flex-wrap items-center justify-end gap-2", className)}>{children}</div>;
}
