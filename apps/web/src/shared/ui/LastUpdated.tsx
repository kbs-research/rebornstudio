/**
 * 마지막 갱신 시각과 새로고침 버튼. 예: "14:22 갱신 ⟳".
 *
 * 자동 갱신 화면에서 시각이 있어야 누를지 판단할 수 있다. 오래됐으면 누르고, 방금 갱신됐으면 그대로 둔다.
 * 시각은 분까지만 쓴다. 30초마다 바뀌는 초 자리는 읽을 필요 없이 움직이기만 해서 시선을 끈다.
 * 버튼은 아이콘만 두고 이름은 `aria-label`로 단다.
 */
import { RefreshCw } from "lucide-react";

import { cn } from "@/shared/lib/cn";

/**
 * 갱신 시각을 HH:MM 한 줄로 만든다. 시각이 없으면 `—`를 돌려준다.
 *
 * 부르는 쪽이 `Math.min(a || Infinity, b || Infinity)` 같은 대체값을 넘길 수 있다.
 * truthy 검사(`at ? … : "—"`)는 `Infinity`를 그대로 통과시켜 `new Date(Infinity)`가 `Invalid Date`를 화면에 표시한다.
 *
 * 그래서 이 함수가 유한한 수인지 확인한다. 이 컴포넌트는 시각이 없으면 `—`(측정하지 않았다는 뜻)를 보여 주고,
 * 호출부가 어떤 대체값을 쓰든 없는 시각을 글자로 표시하지 않는다.
 * `tsc`와 빌드는 이 문제를 잡아내지 못하므로 컴포넌트 밖 함수로 분리해 테스트로 확인한다.
 *
 * ko-KR 로케일은 "15시 47분"처럼 길어져 폭을 줄이려던 목적과 맞지 않으므로 24시간제 HH:MM으로 고정한다.
 */
export function formatUpdatedAt(at?: number): string {
  if (!Number.isFinite(at) || (at as number) <= 0) return "—";
  return new Date(at as number).toLocaleTimeString("en-GB", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

interface Props {
  /** 마지막으로 데이터를 받은 시각. react-query의 `dataUpdatedAt`(ms)을 그대로 넘긴다. */
  at?: number;
  onRefresh: () => void;
  refreshing?: boolean;
  className?: string;
}

/** 갱신 시각 글자와 새로고침 버튼을 한 줄로 그린다. 좁은 폭에서는 시각 글자를 숨긴다. */
export function LastUpdated({ at, onRefresh, refreshing, className }: Props) {
  const time = formatUpdatedAt(at);
  return (
    <span className={cn("inline-flex items-center gap-1 text-caption text-label-2", className)}>
      {/* 흰 면·캔버스 위 12px 글자라 `text-label-2`다. `text-label-4`는 2.6:1로 읽기 어렵다. */}
      <span className="hidden tabular-nums sm:inline">{time} 갱신</span>
      <button
        type="button"
        onClick={onRefresh}
        disabled={refreshing}
        aria-label="새로고침"
        /* 시각은 옆 글자가 이미 보여 주므로 툴팁에는 버튼이 하는 일만 쓴다.
           결과와 동작을 한 문자열에 이어 쓰지 않는다.
           좁은 폭(`sm` 미만)에서 옆 글자가 숨으면 시각은 aria 설명으로 전달한다. */
        title="새로고침"
        aria-description={`${time} 갱신`}
        // 누르는 면은 24px로 둔다. 14px 아이콘에 여백 4px이면 22px라 최소 크기(WCAG 2.5.8)보다 작다.
        className="inline-flex size-6 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label-2 disabled:opacity-40"
      >
        <RefreshCw size={14} className={cn(refreshing && "animate-spin")} aria-hidden="true" />
      </button>
    </span>
  );
}
