/**
 * 단위가 붙는 숫자 입력란. 단위를 입력란 안 오른쪽에 둔다(`120 시간`).
 * 입력란의 모양은 `FIELD_BASE`와 같다.
 *
 * 단위를 이름 옆 괄호(`목표 분량 (시간)`)에 적으면 이름이 길어지고 값과 단위가 떨어져 읽힌다.
 */
import type { InputHTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

interface Props extends Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size"> {
  /** 값 뒤에 붙는 단위(`시간`, `건`, `%`). */
  unit: string;
  /** 값이 규칙에 맞지 않으면 true. 테두리를 위험색으로 바꾸고 `aria-invalid`를 붙인다. */
  invalid?: boolean;
  /** 입력란 폭(숫자 자릿수에 맞춘다). */
  width?: "sm" | "md";
}

/** 숫자 입력란과 단위를 한 상자에 그린다. 나머지 속성은 `<input>`으로 넘긴다. */
export function UnitInput({ unit, invalid = false, width = "sm", className, ...rest }: Props) {
  return (
    <span
      className={cn(
        "inline-flex h-9.5 shrink-0 items-center rounded-field border bg-panel transition-colors",
        "focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25",
        invalid ? "border-bad" : "border-separator-strong hover:border-label-4",
        className,
      )}
    >
      <input
        type="number"
        aria-invalid={invalid || undefined}
        className={cn(
          "h-full min-w-0 bg-transparent pl-3 pr-1.5 text-right text-callout tabular-nums text-label outline-none",
          "placeholder:text-placeholder",
          // 브라우저의 증감 화살표는 좁은 입력란에서 값을 가리므로 숨긴다. 위아래 방향키로는 그대로 바꿀 수 있다.
          "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
          width === "sm" ? "w-16" : "w-24",
        )}
        {...rest}
      />
      <span className="pr-3 text-footnote text-label-3">{unit}</span>
    </span>
  );
}
