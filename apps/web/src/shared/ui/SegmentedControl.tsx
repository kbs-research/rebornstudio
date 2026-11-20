/** 하나만 고르는 세그먼트 토글. */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/** 세그먼트 옵션. value는 문자열 union의 멤버다. */
export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
}

interface Props<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
  /** 그룹 이름. 스크린리더가 이 이름으로 무엇을 고르는 토글인지 읽는다. */
  ariaLabel?: string;
  className?: string;
}

/**
 * 하나만 고르는 토글 그룹을 그린다. role=radiogroup/radio, ←/→ 키 이동, roving tabindex를 쓴다.
 *
 * `label`이 ReactNode라서 아이콘을 함께 넣을 수 있다.
 * 같은 모양을 따로 구현하면 키보드 이동을 잃기 쉬우므로 이 컴포넌트를 쓴다.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  ariaLabel,
  className,
}: Props<T>) {
  const idx = options.findIndex((o) => o.value === value);
  const pad = size === "sm" ? "px-2.5 py-1 text-caption" : "px-3 py-1.25 text-footnote";
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-0.5 rounded-control bg-canvas-2 p-0.75",
        className,
      )}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const delta = e.key === "ArrowRight" ? 1 : options.length - 1;
        onChange(options[(idx + delta) % options.length].value);
      }}
    >
      {options.map((o, i) => {
        const active = o.value === value;
        // 고른 것이 없으면(값이 선택지 밖) 첫 선택지를 탭 정지로 둔다. 탭 정지가 없으면 그룹에 Tab으로 들어올 수 없다.
        const tabStop = active || (idx < 0 && i === 0);
        return (
          <button
            key={o.value}
            role="radio"
            type="button"
            aria-checked={active}
            tabIndex={tabStop ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={cn(
              "rounded-control transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              pad,
              active ? "bg-panel font-semibold text-label shadow-xs" : "text-label-2 hover:text-label",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
