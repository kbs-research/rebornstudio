/** 접근성을 갖춘 탭 바와 탭 패널. */
import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/** 탭 1개의 정의. key는 고유해야 하고, label은 렌더할 노드다(아이콘과 텍스트를 함께 넣을 수 있다). */
export interface TabItem {
  key: string;
  label: ReactNode;
  /** hover 툴팁. */
  title?: string;
}

/** 탭 라벨 옆 카운트 배지를 그린다. 모든 화면이 함께 쓴다(사람, 콘텐츠 뷰, 파이프라인 단계 등). */
export function TabCount({ count }: { count: number }) {
  return (
    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-capsule bg-fill px-1 text-caption font-bold text-label-2">
      {count}
    </span>
  );
}

interface TabsProps {
  tabs: TabItem[];
  value: string;
  onChange: (key: string) => void;
  /**
   * 탭과 패널을 잇는 id 접두어. 주면 탭마다 `id`와 `aria-controls`를 달고,
   * 같은 접두어로 그린 `TabPanel`이 `aria-labelledby`로 그 탭을 가리킨다.
   * 주지 않으면 연결하지 않고, 패널은 쓰는 쪽이 따로 그린다.
   */
  idPrefix?: string;
  /** 탭 모양. "pill"이 기본이고, "underline"은 표면 탭(액센트 색 밑줄)이다. */
  variant?: "pill" | "underline";
  className?: string;
  /** 테마 변형(어두운 워크벤치 등)에서 쓰는 클래스 덮어쓰기. */
  tabClassName?: string;
  activeClassName?: string;
  inactiveClassName?: string;
}

/**
 * 접근성을 갖춘 탭 바를 그린다(role=tablist/tab, aria-selected, roving tabindex, ←/→ 키 이동).
 *
 * 패널 내용은 쓰는 쪽이 value로 조건부 렌더한다. 이 컴포넌트는 탭 버튼과 접근성·키보드만 맡는다.
 * variant="underline"은 표면 탭(밑줄과 액센트)이고 기본은 pill이다. *ClassName으로 추가로 덮어쓴다.
 */
export function Tabs({
  tabs,
  value,
  onChange,
  idPrefix,
  variant = "pill",
  className,
  tabClassName,
  activeClassName,
  inactiveClassName,
}: TabsProps) {
  const idx = tabs.findIndex((t) => t.key === value);
  const underline = variant === "underline";

  const baseTab = underline
    ? "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap -mb-px border-b-2 border-transparent pb-2.5 pt-1 text-callout transition-[color,border-color] duration-short focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
    : "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-control px-3 py-1.25 text-footnote font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
  // 밑줄 탭: 고른 탭은 먹색 글자·굵게·2px 먹색 밑줄, 나머지는 ink-3.
  const activeDefault = underline ? "border-accent font-semibold text-label" : "bg-panel font-semibold text-label shadow-xs";
  const inactiveDefault = underline ? "text-label-3 hover:text-label-2" : "text-label-2 hover:text-label";

  /*
    탭 이름은 한 낱말이라 `flex`만 두면 폭이 모자랄 때 `anywhere`가 낱말 중간을 끊는다(`알림`이 `알`/`림`으로). 그래서 라벨은 한 줄로 두고 넘치면 가로로 스크롤한다.
    스크롤은 바깥 래퍼가 맡는다. 탭 바에 `overflow-x`를 주면 `overflow-y`도 `auto`가 되어 활성 탭의 `-mb-px` 1px 때문에 세로 스크롤바가 생긴다.
    `overflow-y-hidden`은 밑줄을 1px로 자르고 `pb-px`는 구분선과 밑줄의 겹침을 깨므로, 겹침은 탭 바에 남기고 래퍼는 스크롤만 한다.
    `min-w-full`은 탭이 짧아도 구분선이 전체 폭을 가로지르게 한다(`w-max`만 주면 마지막 탭에서 선이 끊긴다).
  */
  return (
    <div className={cn("overflow-x-auto", className)}>
    <div
      role="tablist"
      aria-orientation="horizontal"
      className={cn(
        "flex w-max min-w-full items-center",
        underline ? "gap-5.5 border-b border-separator" : "gap-0.5",
      )}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const delta = e.key === "ArrowRight" ? 1 : tabs.length - 1;
        onChange(tabs[(idx + delta) % tabs.length].key);
      }}
    >
      {tabs.map((t) => {
        const active = t.key === value;
        return (
          <button
            key={t.key}
            role="tab"
            type="button"
            id={idPrefix ? tabId(idPrefix, t.key) : undefined}
            aria-controls={idPrefix ? panelId(idPrefix, t.key) : undefined}
            aria-selected={active}
            title={t.title}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.key)}
            className={cn(
              baseTab,
              tabClassName,
              active ? (activeClassName ?? activeDefault) : (inactiveClassName ?? inactiveDefault),
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
    </div>
  );
}

/** 탭 버튼의 id를 만든다. `Tabs`와 `TabPanel`이 같은 규칙을 쓴다. */
function tabId(prefix: string, key: string): string {
  return `${prefix}-tab-${key}`;
}

/** 탭 패널의 id를 만든다. */
function panelId(prefix: string, key: string): string {
  return `${prefix}-panel-${key}`;
}

/**
 * 탭 하나의 내용 영역을 그린다(role="tabpanel").
 * `Tabs`에 준 `idPrefix`와 같은 값을 주면 스크린리더가 탭과 이 영역을 잇는다.
 * 이어야 탭을 고른 뒤 스크린리더가 지금 읽는 영역이 어느 탭의 내용인지 알린다.
 */
export function TabPanel({
  idPrefix,
  tabKey,
  children,
  className,
}: {
  idPrefix: string;
  tabKey: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      id={panelId(idPrefix, tabKey)}
      aria-labelledby={tabId(idPrefix, tabKey)}
      className={className}
    >
      {children}
    </div>
  );
}
