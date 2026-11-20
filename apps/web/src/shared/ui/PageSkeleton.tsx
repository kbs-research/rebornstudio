/**
 * 로딩 중에 그 화면의 모양을 미리 그려 두는 골격(skeleton) 컴포넌트.
 *
 * `<p>불러오는 중…</p>` 한 줄을 그리면 로딩이 끝날 때 그 한 줄이 지표 띠·표·카드로 한꺼번에 바뀌어
 * 레이아웃이 크게 흔들린다. 화면이 흔들리면 사용자는 방금 보던 곳을 놓친다.
 *
 * 스켈레톤 블록은 모두 `aria-hidden`이므로 스크린리더 안내를 위해 바깥에 `role="status"`를 둔다.
 *
 * 실제 화면과 모양이 맞는 조합이 있을 때만 쓴다. 다른 골격을 그리면 골격과 실제 화면 순서로 화면이 두 번 흔들린다.
 * 맞는 조합이 없으면 그 화면에서 `Skeleton`을 직접 조립한다.
 */
import { Skeleton } from "@/shared/ui/Skeleton";
import { cn } from "@/shared/lib/cn";

interface Props {
  /** 상단 지표 띠(StatLine)의 골격을 그리려면 true. 모니터링 화면 대부분이 지표 띠로 시작한다. */
  stats?: boolean;
  /** 카드 패널 수. 2면 본문·사이드 비대칭 그리드로 그린다(실제 화면들이 쓰는 배치). */
  panels?: number;
  /** 도구 줄과 표의 골격을 그리려면 true. `DataTable`의 자체 스켈레톤은 표가 렌더된 뒤에야 나온다. */
  table?: boolean;
  /** 카드 그리드의 카드 수(프로젝트 목록처럼 크기가 같은 카드). */
  cards?: number;
  /**
   * 페이지 제목(`PageHeader`)의 골격을 그리려면 true. 조기 반환 때문에 제목까지 사라지는 화면에서만 켠다.
   *
   * 기본값은 꺼짐이다. `PageHeader`를 조기 반환 바깥에 둔 화면은 로딩 중에도 제목이 보이므로,
   * 그런 화면에서 이 값을 켜면 제목이 두 개가 된다.
   */
  header?: boolean;
  className?: string;
}

/** 지표 띠 한 줄의 골격을 그린다. 값 위치에만 블록을 두고, 간격은 StatLine과 같게 둔다. */
function StatsBar() {
  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
      {/* 클래스는 문자열 리터럴로 쓴다. `w-${n}`처럼 조합하면 Tailwind가 클래스를 찾지 못해
          폭 클래스가 생성되지 않고 스켈레톤이 폭 0으로 사라진다. */}
      {["w-10", "w-8", "w-12", "w-9"].map((w, i) => (
        <span key={i} className="inline-flex items-baseline gap-1.5">
          <Skeleton className={cn("h-5", w)} />
          <Skeleton className="h-3 w-12" />
        </span>
      ))}
    </div>
  );
}

/** 카드 한 장의 골격을 그린다. 헤더 hairline과 본문 몇 줄로 그린다. 그림자는 카드와 같은 e1이다. */
function PanelBox({ lines = 4 }: { lines?: number }) {
  return (
    <div className="overflow-hidden rounded-card border border-separator shadow-e1">
      <div className="border-b border-separator px-5 py-3">
        <Skeleton className="h-4 w-28" />
      </div>
      <div className="space-y-3 px-5 py-4">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3.5", i % 3 === 2 ? "w-3/5" : i % 2 ? "w-4/5" : "w-full")} />
        ))}
      </div>
    </div>
  );
}

/** 넘긴 조합대로 페이지 골격을 그린다. 바깥에 `role="status"`를 둔다. */
export function PageSkeleton({ stats, panels = 0, table, cards = 0, header, className }: Props) {
  return (
    <div
      className={cn("space-y-6", className)}
      role="status"
      aria-label="불러오는 중"
    >
      {/* 제목과 보조 설명 두 줄. 치수는 `PageHeader`의 실제 값을 따른다.
          제목은 `text-title-2`(20px, 행간 1.3으로 약 26px), 부제는 `text-footnote`(13px)다. */}
      {header && (
        <div className="mb-6 border-b border-separator pb-4">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="mt-1.5 h-3.5 w-56" />
        </div>
      )}

      {stats && <StatsBar />}

      {table && (
        <div className="overflow-hidden rounded-card border border-separator shadow-e1">
          <div className="flex items-center justify-between border-b border-separator px-5 py-3">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-7 w-24" />
          </div>
          <div className="divide-y divide-separator">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton className="h-7 w-7 rounded-capsule" />
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="ml-auto h-3.5 w-14" />
                <Skeleton className="h-3.5 w-14" />
              </div>
            ))}
          </div>
        </div>
      )}

      {panels > 0 && (
        <div className={cn("grid gap-4", panels === 2 && "lg:grid-cols-[1.6fr_1fr]")}>
          {Array.from({ length: panels }).map((_, i) => (
            <PanelBox key={i} lines={i === 0 ? 5 : 3} />
          ))}
        </div>
      )}

      {cards > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: cards }).map((_, i) => (
            <PanelBox key={i} lines={2} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 전체 화면 워크벤치용 골격을 그린다. 헤더 바, 왼쪽 스테이지, 오른쪽 레일로 이루어진다.
 *
 * 워크벤치는 `fixed inset-0`이다. 일반 페이지 위치에 `<p>`를 그리면 로딩 뒤에
 * 한 줄짜리 화면이 화면 전체를 덮는 화면으로 바뀌므로, 골격도 같은 오버레이로 그린다.
 */
export function WorkbenchSkeleton() {
  return (
    <div
      data-surface="workbench"
      className="fixed inset-0 z-workbench flex flex-col bg-canvas"
      role="status"
      aria-label="작업 불러오는 중"
    >
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-separator px-4">
        <Skeleton className="h-5 w-12 rounded-capsule" />
        <Skeleton className="h-3.5 w-20" />
        <Skeleton className="ml-auto h-7 w-24" />
        <Skeleton className="h-7 w-28" />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-3 p-4">
          {/* 스테이지는 어두운 바탕(ws-stage)이라 밝은 스켈레톤 대신 바탕만 둔다. */}
          <div className="min-h-0 flex-1 rounded-card bg-ws-stage" />
          <Skeleton className="h-10 w-full" />
        </div>
        <div className="hidden w-[380px] shrink-0 flex-col gap-3 border-l border-separator p-4 lg:flex">
          <Skeleton className="h-8 w-40" />
          {["w-full", "w-5/6", "w-full", "w-3/4", "w-4/6"].map((w, i) => (
            <Skeleton key={i} className={cn("h-3.5", w)} />
          ))}
        </div>
      </div>
    </div>
  );
}
