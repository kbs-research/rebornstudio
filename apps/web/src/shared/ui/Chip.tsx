/**
 * 캡슐 모양 글자 칩. 칩 모양(여백·글자 크기)을 정하는 단일 출처다.
 *
 * 칩 하나는 역할 하나만 맡고, 역할마다 모양이 하나다.
 * - `soft`: 옅은 면과 색 글자. 태그·표시(`재작업`, `읽기 전용`)에 쓴다.
 * - `dot`: 흰 면·선·7px 점·먹색 글자. 상태(`승인`, `반려`)에 쓴다. `StatusBadge`가 이 모양이다.
 * - `count`: 가라앉은 면·고정폭 숫자. 수(`대기 147건`, `보유 1`)에 쓴다. 점이 없어 상태와 구분된다.
 *
 * 세 모양은 높이 22px·좌우 여백 8px·12px 글자로 같아서, 한 줄에 섞여도 윗선과 아랫선이 맞는다.
 *
 * `StatusBadge`는 도메인 상태 enum을 색조와 이름으로 바꾸고, `Chip`은 임의 텍스트를 받는다.
 * 둘은 하는 일이 달라 합치지 않고, `StatusBadge`가 `Chip`을 써서 모양을 한 곳에서 정한다.
 * 안에 버튼이 들어가는 캡슐(필터 초기화·제거 가능 칩)은 `Chip` 밖에서 따로 그린다.
 */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/**
 * 칩의 색조. 색이 의미를 나타낸다. `neutral`은 단순한 라벨이고 나머지는 상태를 나타낸다.
 * `amber`는 지금 봐야 할 표시(재작업 등)에만 쓴다.
 */
export type ChipTone = "neutral" | "quiet" | "ok" | "warn" | "bad" | "note" | "accent" | "amber";

/** 옅은 면 칩(`variant="soft"`)의 면·글자 색. */
const TONE: Record<ChipTone, string> = {
  neutral: "bg-canvas-2 text-label-2",
  // 약한 라벨. `미결정`처럼 아직 아무 일도 일어나지 않은 상태라서 일부러 흐리게 그린다.
  quiet: "bg-canvas-2 text-label-3",
  ok: "bg-ok-muted text-ok-text",
  warn: "bg-warn-muted text-warn-text",
  bad: "bg-bad-muted text-bad-text",
  note: "bg-note-muted text-note-text",
  accent: "bg-accent-muted text-accent",
  amber: "bg-amber-soft text-amber-text",
};

/** 점 칩(`variant="dot"`)의 점 색. 글자는 모두 먹색이고 상태는 점과 글자로 함께 표시한다. */
const DOT: Record<ChipTone, string> = {
  neutral: "bg-label-3",
  quiet: "bg-label-4",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  note: "bg-note",
  accent: "bg-accent",
  amber: "bg-amber",
};

/**
 * 칩의 공통 모양 클래스. 높이·여백·글자 크기는 이 상수 한 곳에서 정한다.
 * 테두리 1px를 모든 모양에 둔다. 옅은 면 칩은 투명 테두리라 선이 있는 점 칩과 높이가 같다.
 */
export const CHIP_BASE =
  "inline-flex h-5.5 shrink-0 items-center gap-1 whitespace-nowrap rounded-capsule border border-transparent px-2 text-caption font-semibold";

/** 점 칩의 모양 클래스. 흰 면에 선 하나를 두르고 앞에 7px 점을 둔다. */
const DOT_BASE = cn(CHIP_BASE, "gap-1.5 border-separator bg-panel font-medium text-label");

/** 수 칩의 모양 클래스. 가라앉은 면에 고정폭 숫자를 쓴다. 사람 단위 수에 상태 색을 쓰지 않으므로 색조를 받지 않는다. */
const COUNT_BASE = cn(CHIP_BASE, "bg-canvas-2 font-medium tabular-nums text-label-2");

interface Props {
  children: ReactNode;
  tone?: ChipTone;
  /** 모양. `soft`(기본)는 태그, `dot`은 상태, `count`는 수를 표시하는 칩이다. `count`는 `tone`을 무시한다. */
  variant?: "soft" | "dot" | "count";
  /** 테두리를 두르면 true. 배경색만으로는 구분이 약한 곳(예: 시스템 헬스 pill 줄)에 쓴다. */
  outlined?: boolean;
  title?: string;
  className?: string;
}

/** 임의 텍스트를 담는 캡슐 칩을 그린다. 색은 `tone`, 모양은 `variant`로 고른다. */
export function Chip({ children, tone = "neutral", variant = "soft", outlined, title, className }: Props) {
  if (variant === "count") {
    return (
      <span title={title} className={cn(COUNT_BASE, className)}>
        {children}
      </span>
    );
  }
  if (variant === "dot") {
    return (
      <span title={title} className={cn(DOT_BASE, className)}>
        <span aria-hidden="true" className={cn("size-1.75 shrink-0 rounded-capsule", DOT[tone])} />
        {children}
      </span>
    );
  }
  return (
    <span
      title={title}
      className={cn(CHIP_BASE, TONE[tone], outlined && "border border-separator", className)}
    >
      {children}
    </span>
  );
}
