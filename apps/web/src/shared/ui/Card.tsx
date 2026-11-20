/** 페이지 위에 놓는 카드와 그 머리글·본문 영역. */
import { HTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

/**
 * 페이지 위에 놓는 카드를 그린다. hairline 테두리와 접촉 그림자로 면을 띄운다.
 *
 * 운영 도구에서 과한 elevation은 시선을 흩트리므로 그림자는 e1 한 겹까지만 쓴다.
 * 그림자 없이 hairline만 쓰면 앰비언트 배경이 깔린 캔버스에서 카드가 배경에 붙어 보이고,
 * 화면 전체가 스타일이 적용되지 않은 문서처럼 보인다.
 *
 * @param as 카드를 렌더할 태그. 기본값은 `div`.
 *
 *   절을 카드로 그릴 때는 `section`을 준다.
 *   카드 클래스(`rounded-card border border-separator bg-panel`)를 손으로 옮겨 적으면
 *   `shadow-e1`이 빠져 같은 "페이지 위의 카드"가 화면마다 떠 보이거나 평평해 보인다.
 *   `SectionHeader`도 같은 이유로 `as`를 받는다.
 */
export function Card({
  className,
  as: Tag = "div",
  ...rest
}: /* 타입을 `HTMLElement`로 넓힌다. `li`·`section`은 `div`와 이벤트 핸들러의 제네릭이 달라서
      `HTMLDivElement`로는 받을 수 없다. 공통 조상 타입이면 모든 태그가 같은 props 타입을 쓴다. */
HTMLAttributes<HTMLElement> & { as?: "div" | "section" | "article" | "aside" | "header" | "li" }) {
  return (
    <Tag
      className={cn(
        "rounded-card border border-separator bg-panel shadow-e1",
        className,
      )}
      {...rest}
    />
  );
}

/** 카드의 머리글 영역을 그린다. 아래쪽에 구분선을 둔다. */
export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("border-b border-separator px-4 py-3", className)} {...rest} />;
}

/** 카드의 본문 영역을 그린다. 사방에 같은 여백을 둔다. */
export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...rest} />;
}
