/**
 * 여러 항목 중에서 고르는 체크박스. 기능을 켜고 끄는 것은 `Switch`로 한다.
 *
 * 네이티브 `<input type="checkbox">`를 투명하게 겹쳐 두고 그 위에 16px, 모서리 4px, 먹색 채움 모양을 그린다.
 * 입력 요소가 그대로 있어 키보드(Space)와 스크린리더 동작은 브라우저 기본을 따른다.
 * 투명한 입력에는 포커스 표시가 보이지 않으므로 겉모양 상자가 `peer-focus-visible`로 대신 그린다.
 */
import { Check, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

interface Props {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** 일부만 고른 상태이면 true. 전체 선택 체크박스가 쓰고, 참이면 가로줄을 그린다. */
  indeterminate?: boolean;
  disabled?: boolean;
  /** 보이는 이름. 없으면 `aria-label`을 이름으로 쓴다. */
  label?: ReactNode;
  "aria-label"?: string;
  id?: string;
  className?: string;
}

/** 체크박스 하나와 선택 이름을 그린다. 누르면 `onChange(!checked)`를 부른다. */
export function Checkbox({
  checked,
  onChange,
  indeterminate = false,
  disabled = false,
  label,
  "aria-label": ariaLabel,
  id,
  className,
}: Props) {
  const on = checked || indeterminate;
  const box = (
    <span className="relative inline-flex size-4 shrink-0 align-middle">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label ? undefined : ariaLabel}
        aria-checked={indeterminate ? "mixed" : checked}
        ref={(el) => {
          if (el) el.indeterminate = indeterminate;
        }}
        onChange={() => onChange(!checked)}
        className="peer absolute inset-0 m-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
      />
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none inline-flex size-4 items-center justify-center rounded-mark border-[1.5px] transition-colors",
          "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus",
          on ? "border-accent bg-accent text-accent-fg" : "border-separator-strong bg-panel",
          disabled && "opacity-50",
        )}
      >
        {indeterminate ? <Minus size={11} strokeWidth={3.5} /> : checked && <Check size={11} strokeWidth={3.5} />}
      </span>
    </span>
  );
  if (!label) return <span className={className}>{box}</span>;
  return (
    <label
      className={cn(
        "inline-flex items-center gap-2 text-callout text-label",
        disabled ? "cursor-not-allowed text-label-4" : "cursor-pointer",
        className,
      )}
    >
      {box}
      {label}
    </label>
  );
}
