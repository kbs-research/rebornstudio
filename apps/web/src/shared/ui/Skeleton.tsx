/**
 * 로딩 중에 보여 주는 골격 컴포넌트(Skeleton, SkeletonText).
 *
 * Skeleton: animate-pulse 직사각형 블록이다. 크기와 너비는 className으로 정한다.
 * SkeletonText: Skeleton 막대 N개를 세로로 쌓아 텍스트 로딩을 표현한다.
 */
import { cn } from "@/shared/lib/cn";

/** Skeleton 블록 하나를 그린다. className으로 h-*, w-* 등을 지정한다. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-control bg-canvas-2", className)}
      aria-hidden="true"
    />
  );
}

/** 텍스트 스켈레톤의 줄마다 돌아가며 쓰는 너비. 너비를 바꿔 실제 글처럼 보이게 한다. */
const LINE_WIDTHS = ["w-full", "w-5/6", "w-4/6", "w-full", "w-3/4"];

/** N줄짜리 텍스트 스켈레톤을 그린다. */
export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn("h-4", LINE_WIDTHS[i % LINE_WIDTHS.length])}
        />
      ))}
    </div>
  );
}
