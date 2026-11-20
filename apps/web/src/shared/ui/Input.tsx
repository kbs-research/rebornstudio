/** 공용 텍스트 입력란. */
import { InputHTMLAttributes, forwardRef } from "react";

import { cn } from "@/shared/lib/cn";
import { FIELD_BASE, FIELD_H } from "./field";

/** 공용 텍스트 입력란을 그린다. 모양은 `FIELD_BASE`·`FIELD_H`를 따른다. */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input
      ref={ref}
      className={cn(
        FIELD_BASE, FIELD_H, "px-3",
        className,
      )}
      {...rest}
    />
  ),
);
Input.displayName = "Input";
