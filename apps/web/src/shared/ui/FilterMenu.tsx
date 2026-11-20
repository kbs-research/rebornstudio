/**
 * 캡슐 칩과 팝오버로 값 하나를 고르는 필터.
 * `searchable`을 켜면 옵션이 많을 때(예: 작업자 100명 이상) 입력해서 좁힌다.
 * 고르기 전에는 중립 칩에 'label'만, 고른 뒤에는 'label: 값 ✕'를 보여 준다.
 *
 * 감사 로그 같은 운영 필터에서 네이티브 select 대신 쓴다. 필터마다 같은 팝오버 칩 모양을 쓴다.
 */
import { Check, ChevronDown, Search, X } from "lucide-react";
import { useRef, useState } from "react";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { EmptyState } from "@/shared/ui/EmptyState";
import { cn } from "@/shared/lib/cn";

export interface FilterOption {
  value: string;
  label: string;
  hint?: string;
  /** 옵션 왼쪽 색 점의 클래스(예: "bg-bad"). 심각도처럼 색으로 훑어봐야 하는 필터에 쓴다. 선택 항목이다. */
  dot?: string;
}

/** 필터 칩과 옵션 팝오버를 그린다. 빈 값("")은 전체를 뜻한다. */
export function FilterMenu({
  label,
  value,
  options,
  onChange,
  searchable = false,
  align = "left",
  width = "w-56",
}: {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  searchable?: boolean;
  align?: "left" | "right";
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useOutsideClose(open, ref, () => setOpen(false));

  const active = !!value;
  /*
    `value`는 반드시 `options`에 있는 값이어야 한다. 없으면 내부 키가 칩에 그대로 표시된다("단계: awaiting").
    두 컨트롤이 같은 URL 파라미터를 서로 다른 값 이름으로 쓰면 이 문제가 생긴다.
  */
  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;
  const filtered =
    searchable && q.trim()
      ? options.filter((o) => o.label.toLowerCase().includes(q.trim().toLowerCase()))
      : options;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => { setOpen((o) => !o); setQ(""); }}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "inline-flex h-8 max-w-full items-center gap-1 rounded-capsule border pl-3 text-footnote font-medium transition-colors",
          /*
            상태 표현을 입력란과 맞춘다. hover에서는 테두리만 진해지고, 면 색은 켜진 상태에서만 바뀐다.
            꺼진 칩의 hover가 면을 칠하면 켜진 칩과 구분하기 어렵다.
          */
          active
            ? "border-accent/40 bg-accent-muted pr-1.5 text-accent"
            : "border-separator bg-canvas-2 pr-2.5 text-label-2 hover:border-separator-strong",
        )}
      >
        <span className="max-w-[12rem] truncate">{active ? `${label}: ${selectedLabel}` : label}</span>
        {active ? (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); onChange(""); setOpen(false); }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                onChange("");
                setOpen(false);
              }
            }}
            className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-capsule text-accent transition-colors hover:bg-fill"
            aria-label={`${label} 해제`}
          >
            <X size={12} />
          </span>
        ) : (
          <ChevronDown
            size={14}
            className={cn("shrink-0 text-label-3 transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        )}
      </button>
      {open && (
        <div
          role="listbox"
          className={cn(
            "absolute top-full z-30 mt-1.5 animate-pop-in overflow-hidden rounded-card border border-separator bg-panel shadow-pop",
            width,
            align === "right" ? "origin-top-right right-0" : "origin-top-left left-0",
          )}
        >
          {searchable && (
            <div className="flex items-center gap-1.5 border-b border-separator px-2.5 py-2">
              <Search size={14} className="shrink-0 text-label-3" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="검색…"
                aria-label={`${label} 검색`}
                className="w-full border-0 bg-transparent text-footnote text-label outline-none placeholder:text-placeholder"
              />
            </div>
          )}
          <div className="max-h-64 overflow-auto p-1">
            <Opt label={`${label} 전체`} selected={!value} onClick={() => { onChange(""); setOpen(false); }} />
            {filtered.map((o) => (
              <Opt
                key={o.value}
                label={o.label}
                hint={o.hint}
                dot={o.dot}
                selected={value === o.value}
                onClick={() => { onChange(o.value); setOpen(false); }}
              />
            ))}
            {/*
              드롭다운의 결과 없음 표시는 `shell/ProjectSwitcher`와 같은 `EmptyState dense`로 그린다.
              검색으로 좁혀서 0건이 된 경우와 호출부가 옵션을 하나도 받지 못한 경우를 나눈다(`filtered`는 검색어가 없으면 `options` 그대로다).
              옵션이 없는 경우에 "검색어와 일치하는"이라고 쓰면 입력하지도 않은 검색을 원인으로 가리키게 된다.
            */}
            {filtered.length === 0 &&
              (searchable && q.trim() ? (
                <EmptyState dense message="결과 없음" description="검색어와 일치하는 값이 없습니다." />
              ) : (
                <EmptyState dense message="고를 수 있는 값 없음" />
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** 팝오버의 옵션 한 줄을 그린다. 고른 옵션에는 체크 표시를 붙인다. */
function Opt({
  label,
  hint,
  dot,
  selected,
  onClick,
}: {
  label: string;
  hint?: string;
  dot?: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-footnote transition-colors hover:bg-fill",
        selected ? "font-medium text-accent" : "text-label-2",
      )}
    >
      <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate">
        {dot && <span className={cn("h-2 w-2 shrink-0 rounded-capsule", dot)} aria-hidden="true" />}
        {label}
        {hint && <span className="ml-1.5 text-caption text-label-3">{hint}</span>}
      </span>
      {selected && <Check size={14} className="shrink-0 text-accent" aria-hidden="true" />}
    </button>
  );
}
