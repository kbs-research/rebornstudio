/**
 * 켜고 끄는 스위치. 기능 하나를 쓸지 말지 정할 때 쓴다.
 *
 * 체크박스는 여러 항목 중에서 고를 때 쓰고, 기능을 켜고 끄는 것은 이 스위치로 한다.
 * 기능을 체크박스로 켜게 하면 체크해야 쓰는 것인지 사용자가 해석해야 한다.
 *
 * 색만으로 상태를 알리지 않도록 지금 상태를 스위치 모양과 글자(`사용 중`/`사용 안 함`)로 함께 보여 준다.
 * 글자를 빼려면 `showState={false}`를 준다.
 */
import { cn } from "@/shared/lib/cn";

interface Props {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** 스위치 이름을 가진 요소의 id. 없으면 `label`을 이름으로 쓴다. */
  labelledBy?: string;
  /** 보이는 이름이 따로 없을 때 쓰는 이름. */
  label?: string;
  disabled?: boolean;
  /** 상태 글자를 스위치 왼쪽에 보여 주려면 true. */
  showState?: boolean;
  /** 켜짐·꺼짐 글자. 기본은 `사용 중`/`사용 안 함`. */
  onText?: string;
  offText?: string;
  size?: "sm" | "md";
  id?: string;
}

/** 스위치 하나와 상태 글자를 그린다. 누르면 `onChange(!checked)`를 부른다. */
export function Switch({
  checked,
  onChange,
  labelledBy,
  label,
  disabled = false,
  showState = true,
  onText = "사용 중",
  offText = "사용 안 함",
  size = "md",
  id,
}: Props) {
  // 크기: md 40×24(손잡이 20), sm 32×20(손잡이 16). 손잡이는 트랙 폭에서 안쪽 여백과 지름을 뺀 만큼 움직인다.
  const track = size === "sm" ? "h-5 w-8" : "h-6 w-10";
  const knob = size === "sm" ? "size-4" : "size-5";
  const travel = size === "sm" ? "translate-x-3" : "translate-x-4";
  return (
    <span className="inline-flex shrink-0 items-center gap-2.5">
      {/* 글자 폭을 고정해 목록에서 스위치와 입력란의 가장자리가 줄마다 같게 한다. */}
      {showState && (
        <span
          aria-hidden="true"
          className={cn("min-w-16 text-right text-footnote font-medium", checked ? "text-ok-text" : "text-label-3")}
        >
          {checked ? onText : offText}
        </span>
      )}
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex shrink-0 items-center rounded-capsule p-0.5 transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-50",
          track,
          checked ? "bg-accent" : "bg-separator-strong",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "rounded-capsule shadow-xs transition-transform",
            knob,
            checked ? cn(travel, "bg-accent-fg") : "translate-x-0 bg-knob",
          )}
        />
      </button>
    </span>
  );
}
