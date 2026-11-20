/**
 * 긴 화면의 절(section) 목록을 왼쪽에 두고 지금 보고 있는 절을 표시한다.
 *
 * 탭은 내용을 숨기고 숨은 내용은 보지 않게 되므로, 모니터링 화면에서는 탭 대신 이 레일을 쓴다.
 * 사용자는 한 번 스크롤로 전체를 훑고, 레일이 현재 위치를 표시한다.
 *
 * 모양은 사이드바 하위 레일과 같다.
 *
 * 활성 절은 위치를 직접 계산해 정한다. 기준선(뷰포트 상단 25%)을 지난 절 중 마지막 절이다.
 * 페이지 끝에서는 짧은 마지막 절이 상단 밴드 높이까지 올라올 수 없어서,
 * `IntersectionObserver`로 밴드에 들어온 절을 고르면 마지막 절을 가리키지 못한다.
 * 기준선 방식은 절 높이와 관계없고 바닥에 닿으면 마지막 절로 고정하며,
 * 콜백 순서와 관계없이 같은 위치에서 항상 같은 결과를 낸다.
 */
import { useEffect, useRef, useState } from "react";

import { cn } from "@/shared/lib/cn";

export interface RailSection {
  id: string;
  label: string;
  /** 옆에 붙는 수. 없으면 그리지 않는다. 분모가 없는 수를 만들어 내지 않는다. */
  count?: number | string;
}

/** 절 이동 레일을 그린다. 스크롤 위치에 따라 활성 절을 갱신한다. */
export function SectionRail({
  sections,
  className,
}: {
  sections: RailSection[];
  className?: string;
}) {
  const [active, setActive] = useState(sections[0]?.id);
  const ids = sections.map((s) => s.id).join(",");

  useEffect(() => {
    const list = ids.split(",").filter(Boolean);
    if (!list.length) return;

    /** 기준선을 지난 마지막 절을 고른다. 짧은 절은 기준선까지 올라오지 못하므로 바닥에서는 마지막 절을 고른다. */
    const compute = () => {
      const line = window.innerHeight * 0.25;
      const atBottom =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      if (atBottom) return list[list.length - 1];
      let cur = list[0];
      for (const id of list) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) cur = id;
      }
      return cur;
    };

    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        setActive(compute());
      });
    };

    setActive(compute());
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [ids]);

  const reduced = useRef(
    typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );

  return (
    <nav aria-label="절 이동" className={cn("flex flex-col gap-0.5", className)}>
      {sections.map((s) => {
        const on = s.id === active;
        return (
          <a
            key={s.id}
            href={`#${s.id}`}
            aria-current={on ? "true" : undefined}
            onClick={(e) => {
              const el = document.getElementById(s.id);
              if (!el) return;
              e.preventDefault();
              el.scrollIntoView({ behavior: reduced.current ? "auto" : "smooth", block: "start" });
              // 절 링크를 공유할 수 있도록 주소창에도 남긴다.
              history.replaceState(null, "", `#${s.id}`);
              setActive(s.id);
            }}
            className={cn(
              "flex items-center gap-2 border-l-2 py-1.5 pl-3 pr-2 text-footnote transition-colors",
              on
                ? "border-accent font-semibold text-accent"
                : "border-transparent text-label-3 hover:text-label-2",
            )}
          >
            <span className="truncate">{s.label}</span>
            {s.count != null && (
              <span className="ml-auto shrink-0 tabular-nums text-caption text-label-3">{s.count}</span>
            )}
          </a>
        );
      })}
    </nav>
  );
}
