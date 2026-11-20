/**
 * 요청할 때만 나오는 보조 설명(Tooltip)과, 그 관례를 적용하는 `DefinedTerm`.
 *
 * 화면의 수는 분모·기간·좋은 방향을 밝혀야 한다.
 * 이 앱의 화면은 매일 같은 화면에 들어오는 관리자가 주로 쓴다.
 * 그 설명을 모두 항상 보이는 문구로 쓰면 관리자에게는 읽지 않는 글이 쌓인다.
 *
 * 그래서 정의는 점선 밑줄 뒤에 두고, 값은 그대로 둔 채 뜻이 궁금한 사람만 열어 보게 한다.
 * 이 관례가 지켜지면 사용자는 세 번쯤 보고 나서 점선이면 설명이 있다는 것을 배운다.
 * 그래서 점선은 설명이 있을 때만 쓴다. 점선인데 설명이 없으면 관례가 무너진다.
 *
 * 접근성: hover 전용 툴팁은 키보드 사용자가 열 수 없으므로 트리거를 `tabIndex=0`으로 두고 포커스로도 연다.
 * `aria-describedby`로 연결하므로 스크린리더는 값과 정의를 함께 읽는다. Esc로 닫는다.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

interface TooltipProps {
  /** 툴팁에 보여 줄 내용. 한 문장을 권장한다. */
  content: ReactNode;
  /** 트리거가 될 요소. */
  children: ReactNode;
  /** 패널 위치. "top"이면 트리거 위쪽에 띄운다(아래 여백이 없을 때). 기본값은 "bottom". */
  placement?: "top" | "bottom";
  className?: string;
}

/** hover나 포커스로 열리는 보조 설명 패널을 그린다. 트리거를 감싸서 쓴다. */
export function Tooltip({ content, children, placement = "bottom", className }: TooltipProps) {
  const [open, setOpen] = useState(false);
  // 패널이 화면 오른쪽 밖으로 나가면 트리거의 오른쪽 끝에 맞춘다.
  const [alignEnd, setAlignEnd] = useState(false);
  const panel = useRef<HTMLSpanElement>(null);
  const id = useId();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  /*
    열린 뒤 패널이 화면 오른쪽 끝을 넘는지 재고, 넘으면 오른쪽 정렬로 뒤집는다.
    표의 오른쪽 열 머리에서 열면 왼쪽 정렬 패널이 화면 밖으로 나가 첫 몇 글자만 보인다.
  */
  useLayoutEffect(() => {
    if (!open) {
      setAlignEnd(false);
      return;
    }
    const r = panel.current?.getBoundingClientRect();
    if (r && r.right > window.innerWidth - 8) setAlignEnd(true);
  }, [open]);

  // 스치듯 지나가는 포인터에 반응하지 않도록 열기는 조금 늦춘다.
  // 닫기는 바로 한다. 패널이 남아 있으면 다음 요소를 가린다.
  const openLater = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), 120);
  };
  const closeNow = () => {
    clearTimeout(timer.current);
    setOpen(false);
  };

  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={openLater}
      onMouseLeave={closeNow}
      onFocus={() => setOpen(true)}
      onBlur={closeNow}
      tabIndex={0}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open && (
        <span
          ref={panel}
          id={id}
          role="tooltip"
          /* 툴팁은 흑연색 면이라 종이 화면 위에서 본문과 구분된다. */
          data-surface="graphite"
          className={cn(
            // `whitespace-normal`: 표 머리(`th`)의 `nowrap`을 물려받으면 18rem 안에서 줄바꿈하지 않으므로 되돌린다.
            "pointer-events-none absolute z-50 w-max max-w-[18rem] animate-pop-in whitespace-normal",
            alignEnd ? "right-0" : "left-0",
            "rounded-control border border-separator bg-canvas-2 px-2.5 py-1.5 shadow-pop",
            "text-caption font-normal leading-relaxed text-label",
            placement === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}

/**
 * 점선 밑줄과 정의 툴팁을 붙인 용어를 그린다. 이 앱에서 설명이 있다는 표시는 이 컴포넌트로만 한다.
 *
 * 지표 라벨·표 머리글처럼 사용자가 이 수가 무엇을 세는지 궁금해할 곳에 쓴다.
 * 링크(실선 밑줄)와 모양이 달라서 사용자는 밑줄 모양만으로 이동인지 설명인지 구분한다.
 */
export function DefinedTerm({
  children,
  define,
  placement,
  className,
}: {
  children: ReactNode;
  /** 정의. 무엇을 세는지, 어느 기간인지, 어느 쪽이 좋은지 쓴다. */
  define: ReactNode;
  placement?: "top" | "bottom";
  className?: string;
}) {
  return (
    <Tooltip content={define} placement={placement}>
      <span
        className={cn(
          "cursor-help underline decoration-dotted decoration-from-font underline-offset-[3px]",
          "decoration-label-4",
          className,
        )}
      >
        {children}
      </span>
    </Tooltip>
  );
}
