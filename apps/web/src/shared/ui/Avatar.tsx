/** 이름의 첫 글자를 담은 원형 칩. 면 색은 `className`으로 덮어쓸 수 있다. */
import { cn } from "@/shared/lib/cn";

interface Props {
  name?: string | null;
  size?: number;
  className?: string;
}

/** 원형 이니셜 칩을 그린다. */
export function Avatar({ name, size = 30, className }: Props) {
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-capsule bg-canvas-2 font-semibold text-label",
        className,
      )}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
    >
      {initial}
    </span>
  );
}
