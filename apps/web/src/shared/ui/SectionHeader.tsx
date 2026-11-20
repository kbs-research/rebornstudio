/**
 * 테이블 섹션의 공용 머리글(아이콘, 제목, 보조 설명, 카운트·액션).
 *
 * 여러 화면의 표 머리글 모양을 하나로 통일한 컴포넌트다.
 * Card 안에 두면 CardHeader를 대신한다(border-b, px-5).
 * 카드 밖 전체 폭 섹션에서는 `bare`로 제목 줄만 그린다.
 * 아이콘 색은 의미 색을 유지할 수 있도록 호출부가 정하고, 정하지 않으면 중립색 label-3을 쓴다.
 *
 * 제목은 heading 태그로 렌더한다.
 * 구조가 없으면 스크린리더와 브라우저 개요가 페이지를 한 덩어리로 읽고,
 * 개발자도 코드에서 절과 라벨을 구분하기 어렵다.
 * Tailwind preflight가 heading의 크기·굵기·여백을 이미 초기화하므로 태그를 바꿔도 화면 모양은 같다.
 */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/** 섹션 머리글 한 줄을 그린다. 제목과 설명이 없으면 카운트를 왼쪽에 둔다. */
export function SectionHeader({
  icon,
  title,
  description,
  count,
  action,
  bare = false,
  as: Heading = "h2",
  className,
}: {
  icon?: ReactNode;
  /**
   * 제목. 생략할 수 있다. 파이프라인 단계로 임베드되면 레일 캡슐이 이미 단계 이름을
   * 보여 주므로, 제목을 또 쓰면 같은 단어가 화면에 두 번 나온다.
   * 생략하면 카운트·액션만 남은 툴바가 된다.
   */
  title?: ReactNode;
  /** 제목 옆의 회색 보조 설명. em-dash로 이어 붙인다. */
  description?: ReactNode;
  /** 카운트(숫자 또는 문자). tabular-nums로 렌더한다. 제목과 설명이 없으면 왼쪽에 둔다. */
  count?: ReactNode;
  /** 오른쪽 액션(버튼 등). count와 함께 쓰면 count 뒤에 둔다. */
  action?: ReactNode;
  /** 카드 밖 전체 폭 섹션의 제목이면 true. 테두리와 패딩 없이 아래 여백만 둔다. */
  bare?: boolean;
  /**
   * 제목 태그. 기본값 `h2`는 페이지 제목(`PageHeader`의 `h1`) 바로 아래 절이라는 뜻이다.
   * 이미 제목이 있는 절 안에 다시 카드를 두는 경우에만 `h3`로 낮춘다.
   * 툴바로 쓸 때는 `title`을 넘기지 않으며, 그때 이 값은 적용되지 않는다.
   */
  as?: "h2" | "h3" | "h4";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2",
        bare ? "mb-2.5" : "border-b border-separator px-5 py-3.5",
        className,
      )}
    >
      {icon && <span className="flex shrink-0 items-center text-label-3">{icon}</span>}
      {title != null && (
        <Heading className="shrink-0 text-callout font-semibold text-label-2">{title}</Heading>
      )}
      {/*
        제목과 설명이 모두 없으면 카운트를 왼쪽에 둔다. 이 줄의 순서는 `icon · title … [빈 공간] … count · action`이다.
        툴바로 쓰면(`title` 없음) 왼쪽이 비고 칩과 버튼이 모두 오른쪽에 몰린다.
        그래서 카운트를 왼쪽에 두고 빈 공간을 그 뒤로 보내, 수량, 여백, 할 일 순서로 읽히게 한다.
        그러면 제목이 있을 때와 같은 구조가 된다.
      */}
      {count != null && title == null && !description && (
        <span className="shrink-0 text-caption tabular-nums text-label-3">{count}</span>
      )}
      {description ? (
        <span className="min-w-0 flex-1 truncate text-caption text-label-3">— {description}</span>
      ) : (
        <span className="flex-1" aria-hidden="true" />
      )}
      {count != null && (title != null || !!description) && (
        <span className="shrink-0 text-caption tabular-nums text-label-3">{count}</span>
      )}
      {action && <span className="shrink-0">{action}</span>}
    </div>
  );
}
