/** 폼 라벨. */
import { LabelHTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

/**
 * 폼 라벨을 그린다. `required`면 필수 표시를 붙인다.
 *
 * 필수 표시를 한 곳에서 그리면 모양이 같아지고, 스크린리더는 별표 대신 `필수`라고 읽는다.
 */
export function Label({
  className,
  required,
  children,
  ...rest
}: LabelHTMLAttributes<HTMLLabelElement> & { required?: boolean }) {
  return (
    <label className={cn("block text-caption font-semibold text-label-2", className)} {...rest}>
      {children}
      {required && (
        <>
          <span aria-hidden="true" className="ml-0.5 text-bad-text">*</span>
          <span className="sr-only"> 필수</span>
        </>
      )}
    </label>
  );
}
