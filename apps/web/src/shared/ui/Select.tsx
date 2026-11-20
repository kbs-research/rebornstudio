/** 앱 스타일의 네이티브 Select. */
import { SelectHTMLAttributes, forwardRef } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/shared/lib/cn";
import { FIELD_BASE, FIELD_H } from "./field";

/**
 * 앱 스타일의 Select를 그린다. 키보드·스크린리더 접근성을 유지하려고 네이티브 `<select>`를 쓴다.
 * `appearance-none`으로 브라우저 기본 화살표를 없애고 앱의 chevron과 필드 스타일을 입힌다.
 * 닫힌 상태는 Input과 같은 모양이다.
 * 열린 옵션 목록은 네이티브 그대로다. 완전히 직접 그린 팝오버가 필요하면 FilterMenu를 쓴다.
 */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...rest }, ref) => (
    <div className="relative">
      <select
        ref={ref}
        className={cn(
          FIELD_BASE, FIELD_H, "appearance-none pl-3 pr-9",
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-label-3"
      />
    </div>
  ),
);
Select.displayName = "Select";
