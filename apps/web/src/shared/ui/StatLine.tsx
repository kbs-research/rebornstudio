/**
 * KPI 요약을 카드 그리드 대신 한 줄로 보여 주는 인라인 통계.
 *
 * 카드 4장(라벨, 큰 숫자, 캡션) 균일 그리드는 전형적인 대시보드 템플릿처럼 보인다.
 * 그래서 머리글 아래의 가벼운 인라인 통계로 쓴다. 숫자를 강조하고 라벨은 보조로 둔다.
 *
 * 항목 사이는 구분점(·)이나 세로 구분선 대신 간격으로 나눈다. 바깥이 `flex-wrap`이라 줄이 접히면
 * 구분 기호가 다음 줄의 첫 글자나 줄 끝에 남고, 같은 줄에 있을 때만 그리는 것은 CSS로 할 수 없다.
 * 값은 굵고 크며 라벨은 작아서 간격만으로 묶음이 구분된다.
 *
 * 값·라벨·힌트를 각각 `nowrap`으로 묶어 줄이 묶음 사이에서만 접히게 한다.
 * 묶음이 줄어들 수 있으면 폭이 모자랄 때 묶음 안이 접혀 `약` / `₩171,294`처럼 수와 단위가 떨어진다.
 *
 * 값이 여럿이면 2단으로 쌓는다(`layout="stacked"`). 간격만으로 나누는 방식은 꼬리가 짧을 때만 통한다.
 * 한 묶음의 회색 꼬리(`남은 분석 비용` + `17.15시간 남음`)가 묶음 사이 간격(`gap-x-6`)보다
 * 훨씬 길면 여러 조각이 한 문장처럼 읽힌다.
 * 2단은 값을 위에, 이름을 바로 아래에 붙여서, 옆 묶음과 21px만 떨어져 있어도 섞이지 않게 한다.
 */
import { Skeleton } from "@/shared/ui/Skeleton";
import { DefinedTerm } from "@/shared/ui/Tooltip";
import { cn } from "@/shared/lib/cn";

/** 값의 색조. 기본은 중립이고 warn·bad·ok·accent는 각각 주의·초과·양호·강조에 쓴다(선택). */
export type StatTone = "default" | "warn" | "bad" | "ok" | "accent";
/** 지표 한 항목. `hint`는 라벨 뒤의 작은 보조 설명이다(예: "배정→제출", "길이 환산"). */
export type StatItem = {
  label: string;
  value: string | number;
  tone?: StatTone;
  hint?: string;
  /**
   * 이 수가 무엇을 세는지, 어느 기간인지, 어느 쪽이 좋은지의 정의.
   * 주면 라벨에 점선 밑줄이 붙고 hover·포커스 때 정의가 나온다.
   * 매일 보는 화면이라 항상 보이는 문구 대신 툴팁으로 둔다(`ui/Tooltip.tsx` 참고).
   */
  define?: string;
};

const TONE_CLASS: Record<StatTone, string> = {
  default: "text-label",
  warn: "text-warn-text",
  bad: "text-bad-text",
  ok: "text-ok-text",
  accent: "text-accent",
};

/**
 * 지표 항목들을 한 줄 또는 2단으로 그린다.
 *
 * @param loading 불러오는 중이면 true. 값만 스켈레톤으로 바꾸고 화면의 뼈대인 라벨은 그대로 둔다.
 *   라벨까지 감추면 로딩이 끝날 때 줄 전체가 다시 배치된다.
 *   `role="status"`를 두는 이유는 `ui/PageSkeleton.tsx`를 참고한다.
 */
export function StatLine({
  items, className, loading = false, layout = "inline",
}: {
  items: StatItem[];
  className?: string;
  loading?: boolean;
  /**
   * 값과 이름의 배치. 한 줄(`inline`) 또는 2단(`stacked`).
   * 값이 셋 이상이거나 통화·단위가 붙어 꼬리가 길면 `stacked`를 고른다(위 docstring).
   */
  layout?: "inline" | "stacked";
}) {
  const stacked = layout === "stacked";
  return (
    <div
      className={cn(
        "flex flex-wrap text-callout",
        stacked ? "gap-x-10 gap-y-4" : "items-baseline gap-x-6 gap-y-2",
        className,
      )}
      {...(loading ? { role: "status" as const, "aria-label": "지표 불러오는 중" } : {})}
    >
      {items.map((m) => (
        <span key={m.label} className={stacked ? "inline-flex flex-col" : "inline-flex items-baseline"}>
          <span className={cn("whitespace-nowrap text-headline font-semibold tabular-nums", TONE_CLASS[m.tone ?? "default"])}>
            {loading ? <Skeleton className="inline-block h-[0.9em] w-9 align-baseline" /> : m.value}
          </span>
          {/*
            2단에서는 이름과 힌트가 한 덩어리로 값 밑에 붙고, 인라인에서는 이 래퍼가 `display: contents`로 사라져 세 조각이 한 baseline에 늘어선다.
            래퍼는 항상 렌더한다. 조건부로 렌더하면 같은 자식이 트리에서 두 가지 구조가 되어,
            정의가 있는 이름(`DefinedTerm`)의 포커스가 분기마다 다르게 동작한다.
          */}
          <span className={cn("inline-flex items-baseline", stacked ? "mt-0.5" : "contents")}>
            <span className={cn("whitespace-nowrap text-footnote", stacked ? "text-label-2" : "ml-1.5 text-label-3")}>
              {m.define ? <DefinedTerm define={m.define}>{m.label}</DefinedTerm> : m.label}
            </span>
            {/* 힌트에도 고정폭 숫자를 써서 `114 / 329`가 폴링할 때마다 흔들리지 않게 한다.
                힌트는 분모처럼 읽어야 하는 값이라 장식 글자 대비인 `label-4` 대신 `label-3`으로 둔다. */}
            {m.hint && <span className="ml-1 whitespace-nowrap text-caption tabular-nums text-label-3">{m.hint}</span>}
          </span>
        </span>
      ))}
    </div>
  );
}
