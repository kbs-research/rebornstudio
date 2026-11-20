/**
 * 빈 목록과 빈 검색 결과를 안내한다. 아이콘, 메시지, 선택 액션으로 이루어진다.
 *
 * 기본형은 `py-14`(약 196px)라서 페이지 한가운데에 놓는 크기다.
 * 카드 안·팝오버·표 셀에는 그만한 공간이 없어서, 압축형 `dense`가 없으면 그런 곳은 문장을 손으로 그리게 된다.
 * `ErrorState`의 `inline`과 짝이다. 줄이는 대상이 여백·칩·글자 크기 셋이라 이름을 `dense`로 둔다.
 */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

interface Props {
  icon?: ReactNode;
  message: string;
  description?: string;
  action?: ReactNode;
  /**
   * 카드 안·팝오버·표 셀에 쓰는 압축형이면 true. 여백·칩·머리글 글자 크기를 한 단계씩 줄인다.
   * 페이지 전체가 비었을 때는 기본형을 쓴다. 크게 보여야 페이지가 비었다는 사실이 전달된다.
   */
  dense?: boolean;
  /** 점선 테두리 상자로 감싸면 true. 카드 밖, 목록이 놓일 곳이 통째로 비었을 때 쓴다. */
  framed?: boolean;
}

/**
 * 빈 상태를 그린다. 머리글 하나와, 다음에 할 행동이 있을 때만 버튼 하나를 둔다.
 * 아이콘은 `icon`을 준 곳에만 그린다. 막힘·실패처럼 무엇이 일어났는지 모양으로 알려야 할 때 준다.
 */
export function EmptyState({ icon, message, description, action, dense = false, framed = false }: Props) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 text-center",
        dense ? "py-6" : "py-12",
        framed && "rounded-card border border-dashed border-separator-strong bg-panel px-7",
      )}
    >
      {icon != null && (
        <div
          className={cn(
            "mb-1 flex items-center justify-center rounded-card bg-canvas-2 text-label-2",
            dense ? "size-8" : "size-10",
          )}
        >
          {icon}
        </div>
      )}
      <p className={cn("font-semibold text-label", dense ? "text-callout" : "text-headline")}>{message}</p>
      {description && <p className="text-footnote text-label-2">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
