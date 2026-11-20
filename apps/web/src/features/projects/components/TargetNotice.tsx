/**
 * 분석 연동 카드(저장소 대상·분석 대상) 안의 안내 두 줄. 결과를 굵게, 이유나 다음 할 일을 작게 쓴다.
 *
 * 두 카드가 같은 위치에 같은 모양으로 잠긴 이유와 저장 전에 막힌 것을 보여 준다.
 */
import type { ReactNode } from "react";
import { Info, TriangleAlert } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/** 안내 두 줄을 그린다. `warn`이면 막힌 것, `neutral`이면 잠긴 이유다. */
export function TargetNotice({
  tone, title, children,
}: { tone: "neutral" | "warn"; title: string; children: ReactNode }) {
  const warn = tone === "warn";
  const Icon = warn ? TriangleAlert : Info;
  return (
    <div
      role="note"
      className={cn(
        "mt-3 flex gap-2.5 rounded-control px-4 py-3",
        warn ? "bg-warn-muted text-warn-text" : "border border-separator bg-canvas-2",
      )}
    >
      <Icon size={16} aria-hidden="true" className={cn("mt-0.5 shrink-0", !warn && "text-label-2")} />
      <div className="text-footnote">
        <p className={cn("font-semibold", !warn && "text-label")}>{title}</p>
        <p className={cn("mt-0.5", !warn && "text-label-2")}>{children}</p>
      </div>
    </div>
  );
}
