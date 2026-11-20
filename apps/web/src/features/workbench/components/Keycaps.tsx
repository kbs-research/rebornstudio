import { cn } from "@/shared/lib/cn";

/**
 * 키캡 묶음을 그린다. 워크벤치의 버튼·단축키 표·안내가 같은 모양으로 키를 그린다.
 *
 * 키 하나가 캡 하나다. `⌘/Ctrl + Enter` 같은 글자 대신 `["⌘", "↵"]`로 넘긴다.
 * 화면 낭독기에는 읽히지 않는다(`aria-hidden`). 같은 뜻이 버튼 이름이나 표의 설명에 이미 있다.
 *
 * @param tone `on-amber`는 호박색 버튼 안에서 쓴다. 호박색 위의 글자색(`text-on-amber`)을 따른다.
 */
export function Keycaps({ keys, tone = "default", className }: {
  keys: string[];
  tone?: "default" | "on-amber";
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-0.5", className)} aria-hidden="true">
      {keys.map((k, i) => (
        <kbd
          key={i}
          className={cn(
            "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-mark px-1 font-mono text-micro font-semibold leading-none",
            tone === "on-amber"
              ? "bg-on-amber/15 text-on-amber"
              : "border border-separator-strong bg-canvas-2 text-label-2",
          )}
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

