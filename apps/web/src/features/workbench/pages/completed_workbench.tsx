/**
 * 완료 워크벤치(`/p/:slug/completed`). 완료된 클립을 연속으로 훑어보는 화면이다.
 *
 * 레일에 검색과 목록을 붙이고 고른 클립을 같은 화면에서 본다.
 * 완료본 확인은 클립마다 납품해도 되는지 연속으로 판단하는 일이라, 건마다 상세로 갔다가 돌아오면 이동이 확인보다 오래 걸린다.
 * 정렬·훑기·내보내기 진입에는 표가 적합해서 완료 단계의 표는 따로 둔다.
 *
 * 내용은 완료 상세와 같은 컴포넌트(`CompletedClipView`)를 쓴다. 복사하면 두 화면이 결국 달라진다.
 *
 * 375px 폭에서는 본문 폭이 326px이라 목록·무대·정보를 함께 둘 수 없다.
 * 그래서 좁은 폭에서는 목록과 본문 중 하나만 보이고 서로 오간다.
 */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Check, ChevronLeft, ChevronRight, Keyboard, Search, X } from "lucide-react";

import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Input } from "@/shared/ui/Input";
import { WorkbenchSkeleton } from "@/shared/ui/PageSkeleton";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import { COMPLETED_FILTERS, CompletedClipView, completedFilterOf, type CompletedFilter } from "@/features/pipeline";
import { WbShortcutsDialog } from "../components/WbShortcutsDialog";
import { WorkbenchHeader } from "../components/WorkbenchHeader";
import type { ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { splitBlocker } from "@/lib/blocker";
import { errorText } from "@/shared/api/error_text";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { hasProjectRole } from "@/lib/roles";
import { useAuth } from "@/store/auth";
import { isTyping, VIDEO_SHORTCUTS } from "../lib/useVideoShortcuts";
import { useSelectedProject } from "@/store/project";
import { useWorkbenchSurface } from "../store";
import { useProjects } from "@/features/projects";

import { fetchCompletedPage, fetchCompletedSummary, workbenchKeys } from "../api";

/**
 * 납품 불가 사유 칩에 쓸 글자를 돌려준다. 납품 가능하거나 사유가 없으면 `null`이다.
 * 칩과 둘째 줄이 같은 판단을 읽도록 함수로 둔다.
 * 둘이 각자 판단하면 칩은 "캡션", 둘째 줄은 "캡션 없음"처럼 한 행에 같은 내용이 두 번 뜬다.
 */
function blockerField(r: { deliverable: boolean; blockers: string[] }): string | null {
  if (r.deliverable || r.blockers.length === 0) return null;
  const { field, reason } = splitBlocker(r.blockers[0]);
  return field ?? reason;
}

/** 한 번에 불러오는 행 수. 목록 레일은 스크롤하므로 표(25)보다 넉넉히 잡는다. */
const PAGE = 100;
/** 검색 디바운스(ms). 글자마다 서버에 요청하지 않는다. */
const DEBOUNCE_MS = 250;
/** 넓은 레이아웃을 쓰는 최소 폭. Tailwind `lg`와 같은 값으로 두어 CSS와 동작을 맞춘다. */
const WIDE = window.matchMedia("(min-width: 1024px)");

/** 완료 클립 목록과 고른 클립의 상세를 한 화면에 그린다. */
export function CompletedWorkbenchPage() {
  const { slug } = useParams<{ slug: string }>();
  const { selectedProjectId: pid } = useSelectedProject();
  const { user } = useAuth();
  /*
    보류함(`p/:slug/qc`)은 관리자 화면이다(`router`의 가드).
    이 워크벤치는 멤버 전원이 열 수 있으므로 보류함을 열 수 있는 사람에게만 링크를 준다.
    나머지에게는 보류 수와 처리 담당자를 알려 준다. 모두에게 링크를 주면 검수자가 권한 오류 화면에서 멈춘다.
  */
  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === pid);
  const canOpenQc = hasProjectRole(user, project, ["project_admin"]);
  const [params, setParams] = useSearchParams();
  /* 목록의 필터 축. 완료 단계 표와 같은 축·같은 이름이다(`completed_filter`). */
  const rawFilter = completedFilterOf(params.get("filter"));
  const filter: CompletedFilter = COMPLETED_FILTERS.some((f) => f.value === rawFilter) ? rawFilter : "all";
  /*
    목록에 없는 축 값은 주소에서도 지운다.
    주소에 축이 남은 채 전체 목록이 보이면 걸러진 목록으로 읽힌다. 프로젝트 정보를 받은 뒤에만 지운다.
  */
  const strayFilter = !!project && params.has("filter") && params.get("filter") !== filter;
  useEffect(() => {
    if (!strayFilter) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("filter");
      return next;
    }, { replace: true });
  }, [strayFilter, setParams]);
  const setFilter = (f: CompletedFilter) => {
    const next = new URLSearchParams(params);
    if (f === "all") next.delete("filter");
    else next.set("filter", f);
    next.delete("item");
    setParams(next, { replace: true });
  };
  // 앱 테마와 관계없이 흑연색 면이다. 워크벤치 루트 밖에 그려지는 요소도 흑연색이 되도록 문서 루트에도 범위를 단다.
  useWorkbenchSurface();
  const [helpOpen, setHelpOpen] = useState(false);

  const [rawQuery, setRawQuery] = useState(params.get("q") ?? "");
  const [query, setQuery] = useState(rawQuery);
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(rawQuery), DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [rawQuery]);
  /*
    검색어가 정해지면(디바운스 뒤) 주소의 `q`도 바꾼다. 고른 클립이 그대로여도 새로고침·공유가
    지금 검색어로 열려야 한다. 행을 고를 때만 주소를 바꾸면 이전 검색어가 남는다.
  */
  const urlQuery = params.get("q") ?? "";
  useEffect(() => {
    if (urlQuery === query) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (query) next.set("q", query);
      else next.delete("q");
      return next;
    }, { replace: true });
    // 주소가 바뀔 때마다 맞추지 않고 검색어가 정해질 때만 맞춘다. 행을 고르는 동안 잠시 어긋나도 곧 같아진다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  /** 고른 클립. 주소에 두어 새로고침·공유가 같은 클립을 연다. */
  const selected = params.get("item");
  const select = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("item", id);
    else next.delete("item");
    /* 디바운스 전의 입력값(`rawQuery`)을 쓴다.
       주소에 두어 새로고침·공유 때 같은 목록이 열리게 한다.
       디바운스된 `query`를 쓰면 입력 후 250ms 안에 행을 고를 때 주소에 이전 검색어가 남아
       새로고침·공유가 다른 목록을 연다. */
    if (rawQuery) next.set("q", rawQuery);
    else next.delete("q");
    setParams(next, { replace: true });
  };

  // 목록은 offset 페이징으로 `더 보기`가 다음 페이지를 이어 불러온다. 첫 페이지에서 조용히 자르지 않는다.
  // 서버 `limit` 상한이 200이라 limit을 키워서는 전수를 받을 수 없다.
  /* 목록은 집계 없이 부른다(`summary=false`). 머리의 수는 아래 요약 조회 한 번으로 받는다.
     함께 부르면 검색어를 입력할 때마다 최대 2,000행 검증이 다시 돈다. */
  const list = useInfiniteQuery({
    queryKey: workbenchKeys.completedList(pid, query, filter),
    queryFn: ({ pageParam = 0 }) => fetchCompletedPage(pid, PAGE, pageParam, filter, query),
    initialPageParam: 0,
    // 다음 페이지 offset은 지금까지 불러온 수다. 전수(total)에 닿을 때까지 이어 부른다.
    getNextPageParam: (lastPage, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
  /* 검색어가 바뀌는 동안 앞 결과를 그대로 둔다. 새 키에 데이터가 없다고 화면 전체를 골격으로
     바꾸면 검색 입력이 사라져 포커스가 빠지고, 이어 입력한 키가 클립을 넘긴다. */
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === pid ? keepPreviousData(prev) : undefined),
    enabled: !!pid,
  });
  /** 검색어를 입력하는 중이거나 새 검색 결과를 기다리는 중이면 true. 목록 머리가 이 동안 "찾는 중…"을 쓴다. */
  const searching = rawQuery !== query || list.isPlaceholderData;

  const rows = useMemo(
    () => (list.data?.pages ?? []).flatMap((p) => p.items),
    [list.data],
  );
  // 전수 집계는 검색어와 관계없이 한 번 받는다(완료 단계 표와 같은 키라 캐시를 함께 쓴다).
  const summary = useQuery({
    queryKey: workbenchKeys.completedSummary(pid),
    queryFn: () => fetchCompletedSummary(pid),
    enabled: !!pid,
    staleTime: 30_000,
  });
  /** 납품 대상 전수. 검색어와 관계없다. `null`은 상한을 넘어 세지 않았다는 뜻이고 0과 구분한다. */
  const deliverableAll = summary.data?.deliverable_total;
  /** 품질검수 보류로 이 목록에서 빠진 수. 검색어와 관계없는 전수 기준이다. */
  const heldTotal = summary.data?.qc_held_total ?? 0;
  const shown = list.data?.pages[0]?.total ?? 0;

  /*
    넓은 폭에서만 첫 행을 자동으로 연다. 오른쪽이 비어 있으면 화면이 고장 난 것처럼 보인다.
    좁은 폭에서는 목록이 첫 화면이다. 자동 선택을 두면 `목록`으로 돌아가는 순간 다시 선택되어 목록으로 돌아갈 수 없다.
    폭은 창 크기를 바꾸거나 태블릿을 돌리면 바뀌므로 `matches`를 한 번만 읽지 않고 변경을 구독한다.
  */
  const [isWide, setIsWide] = useState(WIDE.matches);
  useEffect(() => {
    const onChange = (e: MediaQueryListEvent) => setIsWide(e.matches);
    WIDE.addEventListener("change", onChange);
    return () => WIDE.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    if (!isWide) return;
    if (!selected && rows.length > 0) select(rows[0].work_item_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, selected, isWide]);

  /*
    고른 클립이 검색 결과에 없으면 선택을 바꾼다. 넓은 폭은 첫 결과를 고르고, 결과가 없거나
    좁은 폭이면 선택을 푼다. 다음 페이지에 있을 수 있는 클립을 없다고 판정하지 않도록 결과를 다 불러온 뒤에만 판단한다.
    목록은 선택이 바뀌어도 다시 마운트되지 않아 검색 입력의 포커스가 남는다.
    선택을 그대로 두면 결과가 0건인데 앞서 고른 클립이 무대에 남아 지금 보는 대상이 헷갈린다.
  */
  useEffect(() => {
    if (!query || searching || !list.data || list.hasNextPage) return;
    if (!selected || rows.some((r) => r.work_item_id === selected)) return;
    select(isWide && rows.length > 0 ? rows[0].work_item_id : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, selected, query, searching, isWide, list.data, list.hasNextPage]);

  /* 머리 줄의 `이전 클립`·`다음 클립` 버튼으로 이동해, 매번 왼쪽 목록에서 다음 행을 찾아 누르지 않게 한다.
     분모는 검색 결과 전체(`shown`)다. 불러온 행만 세면 남은 클립이 있는데도 마지막 클립처럼 보인다. */
  const curIdx = selected
    ? rows.findIndex((r) => r.work_item_id === selected)
    : -1;
  /**
   * 불러온 목록의 끝에서 `다음`을 눌러 다음 페이지를 부르는 중이면 true.
   * 다음 페이지를 받으면 그때 넘어간다.
   */
  const pendingNext = useRef(false);
  const goRel = (delta: number) => {
    /* 고른 클립이 없으면 이동할 기준이 없다. 버튼과 같은 조건을 쓴다.
       조건이 다르면 화면이 같은 동작의 가능 여부를 두 가지로 표시한다. */
    if (curIdx < 0) return;
    const next = rows[curIdx + delta];
    if (next) {
      select(next.work_item_id);
      return;
    }
    /* 불러온 목록의 끝인데 더 있으면 다음 페이지를 불러온 뒤에 넘어간다.
       불러오기만 하고 넘어가지 않으면 첫 클릭이 아무 동작도 하지 않은 것처럼 보인다. */
    if (delta > 0 && list.hasNextPage && !list.isFetchingNextPage) {
      pendingNext.current = true;
      void list.fetchNextPage();
    }
  };
  useEffect(() => {
    if (!pendingNext.current) return;
    const next = rows[curIdx + 1];
    if (next) {
      pendingNext.current = false;
      select(next.work_item_id);
    } else if (!list.isFetchingNextPage) {
      pendingNext.current = false; // 다음 행이 없으므로 대기 상태를 해제한다
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, curIdx, list.isFetchingNextPage]);
  /**
   * 더 불러올 행이 남았으면 목록의 끝으로 보지 않는다. 그때 `다음`을 비활성으로 두면 나머지 클립으로 갈 수 없다.
   */
  const hasPrev = curIdx > 0;
  const hasNext =
    curIdx >= 0 && (curIdx + 1 < rows.length || !!list.hasNextPage);

  // `?`로 이 화면의 단축키 목록을 연다. 입력 중에는 글자로 둔다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?" || isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      e.preventDefault();
      setHelpOpen((o) => !o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* 이전·다음 버튼으로 옮기면 목록도 따라 스크롤한다. 그러지 않으면 고른 행이 화면 밖에 남는다. */
  useEffect(() => {
    if (!selected) return;
    document
      .querySelector<HTMLElement>('[data-completed-row][aria-current="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  /*
    로딩 골격도 같은 오버레이로 그린다. 이 화면의 본체가 `fixed inset-0 z-workbench`라서,
    본문 흐름의 `PageSkeleton`을 쓰면 로딩이 끝나는 순간 앱 셸 안의 카드가 전체 화면 덮기로 바뀐다.
  */
  /* 골격은 첫 진입에만 쓴다. 검색 중에는 앞 결과가 남아 있어(`placeholderData`) 이 분기로 오지 않는다. */
  if (list.isLoading) return <WorkbenchSkeleton />;
  // 서버 오류(5xx)는 오류로 표시한다. 빈 배열로 처리하면 "완료된 클립 없음"으로 잘못 표시된다.
  // 원인이 확인된 4xx는 원인을 표시하고, 다시 눌러도 결과가 같으므로 재시도 버튼을 두지 않는다.
  // 검색 중의 실패는 목록 안에 표시한다. 화면 전체를 바꾸면 검색 입력이 사라진다.
  const listError = list.isError && !list.data ? (list.error as unknown as ApiError) : null;
  if (listError && !query) {
    const t = errorText(listError?.detail, { message: "완료 목록 조회 실패" });
    /* 실패도 워크벤치 오버레이 안에서 머리와 함께 그린다. 앱 셸 안에 오류만 뜨면 돌아갈 링크가 보이지 않는다. */
    return (
      <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
        <WorkbenchHeader
          backTo={`/p/${slug}/pipeline?stage=done`}
          backLabel="완료 클립 목록"
          badge="완료 클립"
        />
        <div className="flex flex-1 items-center justify-center p-6">
          <ErrorState
            message={t.message}
            description={t.hint}
            onRetry={!listError?.status || listError.status >= 500 ? () => void list.refetch() : undefined}
          />
        </div>
      </div>
    );
  }

  /*
    목록이 빈 이유를 구분한다.
    승인된 건이 품질검수 보류로 목록에서 빠졌는데 "아직 없습니다"라고 하면 잘못 표시하게 된다.
    목록(좁은 폭)과 무대(넓은 폭)가 같은 빈 상태를 쓴다.
    검색 빈 상태의 안내는 구현 설명 대신 할 일을 쓴다.
  */
  const emptyView = (
    <EmptyState
      message={
        query
          ? "검색 결과 없음"
          : heldTotal > 0
            ? "품질검수 보류로 비어 있음"
            : "완료 클립 없음"
      }
      description={
        query
          ? "완료 클립 전체의 캡션·영상 제목·프로그램명에서 찾습니다."
          : heldTotal > 0
            ? canOpenQc
              ? `승인된 클립 ${heldTotal.toLocaleString("ko-KR")}개가 캡션 품질검수에서 보류되어 납품 대상에서 빠졌습니다. 사유는 품질검수 화면에 있습니다.`
              : `승인된 클립 ${heldTotal.toLocaleString("ko-KR")}개가 캡션 품질검수에서 보류되어 납품 대상에서 빠졌습니다. 프로젝트 관리자가 품질검수 화면에서 처리합니다.`
            : undefined
      }
    />
  );

  /*
    목록 영역(`listPane`)은 한 번만 정의하고, 위치는 레이아웃이 정한다.

    3단 레이아웃에서는 왼쪽 열에 서고, 2단 레이아웃에서는 오른쪽 레일 위쪽에 들어간다.
    두 곳에 두면 검색·빈 상태·행 모양·로빙 tabindex가 결국 서로 달라지므로 마크업을 한 곳에만 둔다.
  */
  const listPane = (
    <div className="flex min-h-0 w-full flex-1 flex-col">
      <div className="space-y-2.5 p-4">
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-label-3"
            aria-hidden="true"
          />
          <Input
            value={rawQuery}
            onChange={(e) => setRawQuery(e.target.value)}
            placeholder="캡션 · 영상 제목 · 프로그램명"
            className="pl-9"
            aria-label="완료 클립 검색"
          />
        </div>
        {/* 필터 축. 완료 단계 표와 같은 축·같은 이름이다. */}
        <SegmentedControl
          options={COMPLETED_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
          value={filter}
          onChange={setFilter}
          ariaLabel="완료 클립 거르기"
          size="sm"
          className="flex w-full [&>button]:flex-1 [&>button]:whitespace-nowrap"
        />
        {/* 검색 중에는 결과 수와 분모를 표시한다. */}
        {(query || rawQuery) && (
          <p className="px-0.5 text-footnote text-label-3" aria-live="polite">
            {searching || !list.data ? "찾는 중…" : `검색 결과 ${shown.toLocaleString("ko-KR")}개`}
          </p>
        )}
      </div>

      {listError ? (
        <div className="px-4 pb-4">
          <ErrorState inline message="검색 실패" error={listError} onRetry={() => void list.refetch()} />
        </div>
      ) : rows.length === 0 ? (
        /* 넓은 폭에서는 무대 위치에 같은 빈 상태를 크게 그린다. 여기서는 좁은 폭에서만 그린다. */
        <div className="lg:hidden">{emptyView}</div>
      ) : (
        <ol className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {rows.map((r) => {
            const cur = r.work_item_id === selected;
            const reason = blockerField(r);
            return (
              <li key={r.work_item_id}>
                <button
                  type="button"
                  data-completed-row
                  onClick={() => select(r.work_item_id)}
                  aria-current={cur ? "true" : undefined}
                  className={cn(
                    "relative w-full rounded-card px-3 text-left transition-colors",
                    "py-2.5",
                    cur ? "bg-panel" : "hover:bg-fill",
                    // 지금 클립은 왼쪽 호박색 막대로 표시한다. 호박색은 지금 볼 것에만 쓴다.
                    cur && "before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-capsule before:bg-amber",
                  )}
                >
                  <div className="flex items-center gap-2">
                    {/* 납품 여부를 목록에서 표시한다. */}
                    {r.deliverable ? (
                      <Check size={14} className="shrink-0 text-ok-text" aria-hidden="true" />
                    ) : (
                      <X size={14} className="shrink-0 text-bad-text" aria-hidden="true" />
                    )}
                    <span className="sr-only">{r.deliverable ? "납품 대상" : "납품 불가"}</span>
                    <span className="truncate text-footnote font-semibold text-label">
                      {r.video_title ?? r.program_name ?? "제목 없음"}
                    </span>
                    {/*
                      납품 불가 사유는 ✗ 옆 칩 하나로 쓴다. 같은 사실을 둘째 줄에 다시 쓰지 않는다.
                      칩 글자는 서버가 정한 화면 이름이다(`방영일`). 전체 사유는 툴팁으로 둔다.
                    */}
                    {!r.deliverable && reason && (
                      <span
                        className="max-w-[7rem] shrink-0 truncate rounded-mark bg-bad-muted px-1.5 text-micro font-semibold text-bad-text"
                        title={r.blockers.join("\n")}
                      >
                        {reason}
                        {r.blockers.length > 1 && ` 외 ${r.blockers.length - 1}`}
                      </span>
                    )}
                    {/* 구간 표기는 영상 길이와 관계없이 같은 형식이 되도록 `fmtClipRange` 하나로 쓴다. */}
                    {r.start_seconds != null && r.end_seconds != null && (
                      <span className="ml-auto shrink-0 font-mono text-micro tabular-nums text-label-3">
                        {fmtClipRange(r.start_seconds, r.end_seconds)}
                      </span>
                    )}
                  </div>
                  {/*
                    둘째 줄은 그 행의 내용을 보여 준다. 요약이 있으면 요약, 없으면 문장 수를 쓴다.
                    사유 칩이 `캡션`이면 `캡션 없음`을 다시 쓰지 않는다. 한 행에 같은 내용이 두 번 뜨기 때문이다.
                  */}
                  {r.caption_brief ? (
                    <p className="mt-1 line-clamp-1 pl-[22px] text-footnote text-label-2">
                      {r.caption_brief}
                    </p>
                  ) : r.sentence_count > 0 ? (
                    <p className="mt-1 pl-[22px] text-footnote tabular-nums text-label-3">
                      문장 {r.sentence_count.toLocaleString("ko-KR")}개
                    </p>
                  ) : reason === "캡션" ? null : (
                    <p className="mt-1 pl-[22px] text-footnote text-label-3">캡션 없음</p>
                  )}
                </button>
              </li>
            );
          })}
          {list.hasNextPage && (
            <li className="p-2">
              <button
                type="button"
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
                className="w-full rounded-control border border-separator-strong px-3 py-2 text-footnote text-label-2 transition-colors hover:bg-fill disabled:text-label-4"
              >
                {list.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
              </button>
            </li>
          )}
        </ol>
      )}
      {/* 목록 아래에 분모를 밝힌다. 불러온 수와 전체 수를 함께 쓴다. */}
      {rows.length > 0 && (
        <p className="shrink-0 border-t border-separator px-4 py-3 text-footnote tabular-nums text-label-3">
          {rows.length.toLocaleString("ko-KR")}개 표시 · {shown.toLocaleString("ko-KR")}개 중
        </p>
      )}
    </div>
  );

  /* 뒤로 가기는 들어온 화면인 작업 단계의 완료 표로 간다. */
  const back = { to: `/p/${slug}/pipeline?stage=done`, label: "작업 단계" };

  return (
    /*
      전체 화면 오버레이다. 라벨링 워크벤치와 같은 방식이다.
      주의: `<main>`에 z-index를 주지 않는다(`_layout.tsx`의 `<main>` 주의).
      main이 스태킹 컨텍스트가 되면 이 오버레이(z=60)가 그 안에 갇혀 사이드바(z=30)가 위로 올라온다.
    */
    <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
      <WorkbenchHeader
        backTo={back.to}
        backLabel={back.label}
        badge="완료 클립"
        title="완료 클립"
        /* 현재 위치. 분모는 검색·필터 결과 전체(`shown`)다. 불러온 행만 세면 끝이 아닌데 끝처럼 읽힌다. */
        subtitle={curIdx >= 0 ? `클립 ${curIdx + 1} / ${shown.toLocaleString("ko-KR")}` : undefined}
        /*
          관리자가 이 화면에서 확인하는 것은 납품할 수 있는 양이다. 그래서 납품 대상 수를 머리 줄 가운데에 둔다.
          수는 검색어와 관계없는 전수다(서버가 따로 센다). `null`은 세지 않았다는 뜻이라 그때는 쓰지 않는다.
        */
        stats={
          deliverableAll != null && summary.data
            ? [{
                key: "deliverable",
                label: "납품 대상",
                value: `${deliverableAll.toLocaleString("ko-KR")} / ${summary.data.total.toLocaleString("ko-KR")}개`,
              }]
            : []
        }
        /*
          품질검수 보류는 관리자에게만 링크로 둔다. 보류가 0이어도 품질검수로 가는 링크는 남긴다.
        */
        status={
          canOpenQc ? (
            /* 누르면 품질검수로 간다. 글자만 두면 설명처럼 읽혀서 도구 버튼과 같은 모양에 이동 화살표를 단다. */
            <Link
              to={`/p/${slug}/qc`}
              className="inline-flex h-8 items-center gap-1 rounded-control pl-2 pr-1.5 text-footnote font-medium text-label transition-colors hover:bg-fill"
            >
              {heldTotal > 0 ? `품질검수 보류 ${heldTotal.toLocaleString("ko-KR")}개` : "품질검수"}
              <ChevronRight size={14} aria-hidden="true" className="text-label-3" />
            </Link>
          ) : heldTotal > 0 ? (
            <span className="text-footnote text-label-2">품질검수 보류 {heldTotal.toLocaleString("ko-KR")}개</span>
          ) : undefined
        }
        actions={[
          {
            key: "shortcuts",
            icon: <Keyboard size={16} aria-hidden="true" />,
            label: "단축키",
            onClick: () => setHelpOpen(true),
            compact: true,
          },
          {
            key: "prev",
            icon: <ChevronLeft size={16} aria-hidden="true" />,
            label: "이전 클립",
            onClick: () => goRel(-1),
            disabled: !hasPrev,
            separatorBefore: true,
          },
        ]}
        secondary={{ label: "다음 클립", onClick: () => goRel(1), disabled: !hasNext }}
        docTitle="완료 클립"
      />

      <WbShortcutsDialog
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        title="단축키 · 완료 클립"
        columns={[[{ title: "재생", items: VIDEO_SHORTCUTS }]]}
      />

      <div className="flex min-h-0 flex-1 flex-col">
        {/* 좁은 폭에서 목록으로 돌아가는 버튼. 넓은 폭에서는 목록이 옆에 있어 필요 없다. */}
        {selected && (
          <button
            type="button"
            onClick={() => select(null)}
            className="flex shrink-0 items-center gap-1 border-b border-separator px-3 py-2 text-footnote text-label-2 transition-colors hover:text-label lg:hidden"
          >
            <ArrowLeft size={14} aria-hidden="true" /> 목록
          </button>
        )}
        {/*
          내용은 완료 상세와 같은 컴포넌트를 쓴다. 목록이 화면 안에 있으므로 `backTo`는 주지 않는다.
          `listPane`은 한 번만, 항상 같은 위치에 마운트한다. 클립이 바뀌어도 이 컴포넌트를 다시 마운트하지 않는다.
          목록이 다시 마운트되면 검색하는 동안 입력의 포커스가 빠진다.
        */}
        <CompletedClipView
          workItemId={selected}
          backTo={null}
          variant="stage"
          sidebar={listPane}
          empty={rows.length === 0 ? emptyView : <EmptyState message="클립 선택 필요" />}
          qcHref={canOpenQc ? `/p/${slug}/qc` : null}
        />
      </div>
    </div>
  );
}
