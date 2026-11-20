/**
 * 셸 본문 위치에 그리는 막힘·실패 안내. 접근 권한 없음, 없는 주소, 화면 열기 실패가 쓴다.
 *
 * 왼쪽 정렬 한 열에 아이콘 칩, 머리글, 첫 문장, 안내, 사실 줄, 버튼 순서로 둔다.
 * 사실 줄은 왜 막혔는지 확인할 값(사용자의 역할·화면을 여는 역할·요청한 주소)이다. 값이 없으면 줄을 그리지 않는다.
 * 목록 안의 빈 결과는 `EmptyState`, 한 절의 조회 실패는 `ErrorState`를 쓴다.
 */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/** 사실 줄 하나. `mono`면 값을 고정폭 글꼴로 그린다(주소·ID). */
export interface StateFact {
  label: string;
  value: ReactNode;
  mono?: boolean;
}

interface Props {
  /** 머리글 위 아이콘. 20px 안팎의 lucide 아이콘을 준다. */
  icon?: ReactNode;
  /** 아이콘 색조. `bad`는 실패에, `neutral`은 막힘·없음에 쓴다. */
  tone?: "neutral" | "bad";
  title: string;
  /** 첫 문장. 무엇이 일어났는지 쓴다. */
  lead?: ReactNode;
  /** 다음 할 일 한 문장. 작게 그린다. */
  hint?: ReactNode;
  facts?: StateFact[];
  actions?: ReactNode;
  /** 놓이는 곳. `page`는 본문 전체를 대신할 때, `card`는 카드 안에 놓을 때 쓴다. */
  size?: "page" | "card";
  className?: string;
}

/** 막힘·실패 안내 한 덩어리를 그린다. `page`면 본문 가운데 폭 440px 열에 놓는다. */
export function StatePanel({
  icon,
  tone = "neutral",
  title,
  lead,
  hint,
  facts,
  actions,
  size = "page",
  className,
}: Props) {
  const page = size === "page";
  const shown = facts?.filter((f) => f.value != null && f.value !== "") ?? [];
  return (
    <div
      role={tone === "bad" ? "alert" : undefined}
      className={cn(page ? "mx-auto w-full max-w-[440px] pb-16 pt-24" : "p-7", className)}
    >
      {icon != null && (
        <div
          aria-hidden="true"
          className={cn(
            "mb-4 flex size-10 items-center justify-center rounded-card bg-canvas-2",
            tone === "bad" ? "text-bad-text" : "text-label-2",
          )}
        >
          {icon}
        </div>
      )}
      <h1 className={cn("text-label", page ? "text-title-1" : "text-headline font-bold")}>{title}</h1>
      {lead && <p className="mt-2 text-callout text-label">{lead}</p>}
      {hint && <p className="mt-1.5 text-footnote text-label-3">{hint}</p>}
      {shown.length > 0 && (
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-footnote">
          {shown.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-label-3">{f.label}</dt>
              <dd className={cn("min-w-0 break-all font-semibold text-label", f.mono && "font-mono font-medium")}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {actions && <div className="mt-6 flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
