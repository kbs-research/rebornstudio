/**
 * 콘텐츠 화면의 클립 탭.
 *
 * 프로젝트 전체 클립(장면 단위)을 썸네일 격자나 표로 전환해 본다.
 * 클립을 누르면 미리보기 창(`ClipPreviewModal`)이 열려 구간 영상과 분석 결과(장면 요약, 키워드, 화자, 음성 인식)를 보여 준다.
 * 썸네일 보기는 카드를 영상별로 묶고, 묶음 머리에 영상 제목과 영상 상세 링크를 둔다.
 * 영상 탭과 모양을 다르게 두어 두 탭을 구분하게 한다.
 *
 * 검색과 순서는 서버가 정한다(`q`). 등록 최신순으로 영상별로 묶고, 영상 안에서는 시작 시각 순이다.
 * 불러온 페이지만 검색하면 페이지 밖의 클립이 검색 결과에서 빠진다.
 */
import { useCallback, useEffect, useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronRight, LayoutGrid, Search, Table2, X } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { ErrorState } from "@/shared/ui/ErrorState";
import { EmptyState } from "@/shared/ui/EmptyState";
import { SectionHeader } from "@/shared/ui/SectionHeader";
import { Thumb } from "@/shared/ui/Thumb";
import { Chip } from "@/shared/ui/Chip";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { ClipDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { useSelectedProject } from "@/store/project";
import { fetchProjectClipCount, fetchProjectClips, libraryKeys } from "../api";
import { ClipSummaryText } from "./ClipSummaryText";
import { clipRangeFormatter } from "../lib/clip_range";
import { clipSummary } from "../lib/clip_summary";
import { WORK_LABEL } from "../lib/clip_status";

import { ClipPreviewModal } from "./ClipPreviewModal";

/** 페이지당 클립 수. 엔드포인트 상한(200) 이내이고, 갤러리 첫 화면에 알맞은 크기다. */
const PAGE_SIZE = 60;

/** 검색어를 서버 조회에 싣기 전에 기다리는 시간(ms). 글자마다 조회하지 않는다. */
const SEARCH_DEBOUNCE_MS = 300;

/** 다음 페이지 커서. 직전 페이지 마지막 클립의 네 값이다. 서버가 네 값을 모두 받아야 행을 건너뛰지 않는다. */
type ClipCursor = { before: string; before_video_id: string; before_start: string; before_id: string };

/**
 * 클립 격자의 로딩 골격을 그린다. 로딩이 끝나면 이 위치가 썸네일 카드 격자로 바뀐다.
 *
 * 열 수·간격·카드 높이를 실제 격자와 같게 두어 로딩 전후에 아래 내용이 밀리지 않게 한다.
 * 블록은 모두 `aria-hidden`이라 바깥에 `role="status"`를 두어 화면 낭독기가 로딩 상태를 읽게 한다(`ui/PageSkeleton`과 같은 방식).
 */
function ClipGridSkeleton() {
  return (
    <div
      className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
      role="status"
      aria-label="클립 불러오는 중"
    >
      {/* 폭 클래스는 리터럴로 쓴다. 문자열을 조합하면 Tailwind가 클래스를 만들지 않아 폭이 0이 된다. */}
      {["w-full", "w-4/5", "w-full", "w-3/5", "w-5/6", "w-4/5", "w-full", "w-2/3", "w-5/6", "w-full"].map(
        (w, i) => (
          <div key={i}>
            {/* 비율과 라운드는 `ui/Thumb`과 같다. */}
            <Skeleton className="aspect-video w-full rounded-control" />
            <div className="mt-1.5 flex items-start gap-1.5">
              {/* 폭 변화는 안쪽 블록에 준다. 바깥에 `flex-1`과 함께 걸면 flex가 우선해서 `w-*`가 무시된다. */}
              <div className="min-w-0 flex-1">
                <Skeleton className={cn("h-4", w)} />
              </div>
            </div>
            <Skeleton className="mt-1 h-3 w-3/5" />
          </div>
        ),
      )}
    </div>
  );
}

/**
 * 격자 카드 아래 글을 그린다. 장면 요약을 두 줄까지 보여 준다.
 *
 * 요약이 없으면 음성 인식 문장을 출처 라벨과 함께 쓴다(`ClipSummaryText`).
 * 5열에서 한 줄은 스무 자 남짓이라 한 줄로 자르면 문장이 중간에 끊긴다.
 * 영상 제목은 묶음 머리에 한 번만 쓴다. 한 영상의 클립이 연달아 놓여 카드마다 쓰면 같은 제목이 반복된다.
 */
function ClipCardCaption({ clip: c }: { clip: ClipDTO }) {
  return (
    <p className="mt-2 line-clamp-2 min-h-[2lh] text-footnote font-medium leading-snug text-label">
      <ClipSummaryText clip={c} />
    </p>
  );
}

/** 불러온 순서를 지키며 같은 영상의 연속된 클립을 한 묶음으로 모은다. 서버가 영상별로 묶어 준다. */
function groupByVideo(rows: ClipDTO[]): { videoId: string; title: string; clips: ClipDTO[] }[] {
  const groups: { videoId: string; title: string; clips: ClipDTO[] }[] = [];
  for (const c of rows) {
    const last = groups[groups.length - 1];
    if (last && last.videoId === c.video_id) last.clips.push(c);
    else groups.push({ videoId: c.video_id, title: c.video_title ?? "제목 없음", clips: [c] });
  }
  return groups;
}

/** 클립 탭 본문을 그린다. 커서 페이지로 클립을 불러와 썸네일 격자나 표로 보여 준다. */
export function ClipsPanel() {
  const { selectedProjectId } = useSelectedProject();
  const { slug } = useParams<{ slug: string }>();
  /*
    검색어와 보기 방식은 영상 탭처럼 주소에 둔다(`cq`·`view`). 새로고침·뒤로 가기에도 같은 조건이 열리고 주소를 그대로 공유할 수 있다.
    같은 주소에 영상 탭의 `q`가 함께 있어서 이름에 `c`를 붙인다.
  */
  const [params, setParams] = useSearchParams();
  const view: "grid" | "table" = params.get("view") === "table" ? "table" : "grid";
  const q = (params.get("cq") ?? "").trim();
  /** 주소의 조건 하나를 바꾼다. `replace`라 뒤로 가기 이력이 쌓이지 않고, 갱신 함수 형식이라 `?tab=`을 지우지 않는다. */
  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      }, { replace: true }),
    [setParams],
  );
  const setView = (v: "grid" | "table") => setParam("view", v === "table" ? "table" : null);
  /** 입력 중인 검색어. 입력이 멈추면 주소의 `cq`로 옮겨 서버에 보낸다. 주소가 바뀌면(뒤로 가기) 입력도 따라 바뀐다. */
  const [draft, setDraft] = useState(q);
  const [preview, setPreview] = useState<ClipDTO | null>(null);
  const filtered = q !== "";

  useEffect(() => {
    setDraft((d) => (d.trim() === q ? d : q));
  }, [q]);
  useEffect(() => {
    const next = draft.trim();
    if (next === q) return;
    const t = window.setTimeout(() => setParam("cq", next || null), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [draft, q, setParam]);

  const clips = useInfiniteQuery({
    // 조건을 키에 넣는다. 빼면 조건을 바꿔도 앞 조건의 캐시가 그대로 그려진다.
    queryKey: libraryKeys.projectClips(selectedProjectId, { q }),
    queryFn: ({ pageParam }) => fetchProjectClips(selectedProjectId, PAGE_SIZE, { q }, pageParam),
    enabled: !!selectedProjectId,
    initialPageParam: undefined as ClipCursor | undefined,
    // 마지막 페이지가 꽉 찼으면 그 마지막 클립을 커서로 다음 페이지를 요청한다.
    getNextPageParam: (last): ClipCursor | undefined => {
      if (last.length < PAGE_SIZE) return undefined;
      const c = last[last.length - 1];
      return {
        before: c.created_at,
        before_video_id: c.video_id,
        before_start: String(c.start_seconds),
        before_id: c.clip_id,
      };
    },
    // 조건을 바꾸는 동안 앞 결과를 둔다. 그래서 글자를 입력할 때마다 격자가 골격으로 깜빡이지 않는다.
    placeholderData: keepPreviousData,
    // 실패는 아래 `ErrorState`가 표시하므로 토스트를 띄우지 않는다.
    meta: { handled: true },
  });

  /* 분모. 목록과 같은 조건으로 서버가 센다. 실패하면 불러온 수만 쓴다(목록은 그대로 보여 준다). */
  const total = useQuery({
    queryKey: libraryKeys.projectClipCount(selectedProjectId, { q }),
    queryFn: () => fetchProjectClipCount(selectedProjectId, { q }),
    enabled: !!selectedProjectId,
    placeholderData: keepPreviousData,
    meta: { handled: true },
  });

  const rows = clips.data?.pages.flat() ?? [];
  const fmt = (n: number) => n.toLocaleString("ko-KR");
  /*
    수에는 센 대상을 밝힌다. 다 받았으면 받은 수가 곧 전체다.
    더 받을 행이 남았으면 서버가 센 분모와 불러온 수를 함께 쓰고, 분모를 받지 못했으면 불러온 수라고만 쓴다.
    받기 전에는 0 대신 `—`를 쓴다. 클립 단위는 `개`다.
  */
  const settled = clips.data !== undefined && !clips.isPlaceholderData;
  const totalNow = total.data !== undefined && !total.isPlaceholderData ? total.data.total : null;
  const countText = !settled
    ? filtered ? "결과 —개" : "전체 —개"
    : clips.hasNextPage
      ? totalNow !== null
        ? `${filtered ? "결과" : "전체"} ${fmt(totalNow)}개 중 ${fmt(rows.length)}개 불러옴`
        : `불러온 ${fmt(rows.length)}개 · 더 있음`
      : `${filtered ? "결과" : "전체"} ${fmt(rows.length)}개`;

  const resetFilters = () => {
    setDraft("");
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("cq");
      return next;
    }, { replace: true });
  };
  /*
    구간은 영상 상세·클립 보기와 같은 함수(`clip_range`)로 목록 전체를 한 형식으로 적는다.
  */
  const fmtRange = clipRangeFormatter(Math.max(0, ...rows.map((c) => c.end_seconds)));

  /*
    표의 열. 격자 카드와 같은 규칙으로 장면 요약 문장을 주 열로 둔다.
    요약 유무만 보여 주는 열은 같은 사실을 두 번 표시하므로 두지 않는다.
  */
  const columns: DataColumn<ClipDTO>[] = [
    {
      label: "클립",
      kind: "image",
      sortable: false,
      width: "7.5rem",
      cell: (c) => (
        <div className="w-24">
          <Thumb seed={c.clip_id} src={c.scene_thumbnail_url} />
        </div>
      ),
    },
    {
      /*
        장면 요약 문장이 주 열이고 영상 제목은 그 아래 작은 줄이다. 화면 용어는 `장면 요약`으로 통일한다.
        요약이 없으면 음성 인식 문장을 출처 라벨과 함께 쓴다(`ClipSummaryText`).
      */
      label: "장면 요약",
      kind: "text",
      sortable: false,
      value: (c) => clipSummary(c)?.text ?? "",
      cell: (c) => (
        /*
          표의 셀은 줄을 바꾸지 않는다(`DataTable`의 `whitespace-nowrap`). 요약만 줄바꿈을 허용해
          1280px 폭에서 긴 요약 때문에 표가 가로로 넘치지 않게 한다.
        */
        <div className="min-w-0 max-w-[480px] whitespace-normal">
          <div className="line-clamp-2 text-footnote text-label">
            <ClipSummaryText clip={c} />
          </div>
          <div className="mt-0.5 truncate text-caption text-label-3">{c.video_title ?? "—"}</div>
        </div>
      ),
    },
    {
      label: "구간",
      kind: "number",
      sortable: false,
      value: (c) => c.start_seconds,
      cell: (c) => (
        <span className="font-mono text-footnote tabular-nums text-label-2">
          {fmtRange(c.start_seconds, c.end_seconds)}
        </span>
      ),
    },
    /* 장면 번호가 없으면 값은 `null`이다. `?? 0`으로 채우면 `#0` 장면처럼 읽힌다(`ui/DataTable.tsx`의 `value` 규칙).
       불러온 클립 모두에 번호가 없으면 모든 행이 `—`라서 열을 두지 않는다. */
    ...(rows.some((c) => c.scene != null) ? [{
      label: "장면",
      kind: "number",
      align: "right",
      sortable: false,
      value: (c) => c.scene ?? null,
      cell: (c: ClipDTO) => <span className="tabular-nums text-label-2">{c.scene != null ? `#${c.scene}` : "—"}</span>,
    } satisfies DataColumn<ClipDTO>] : []),
    {
      /*
        라벨링 작업 상태(`work_status`, 서버가 페이지 단위로 싣는다).
        티켓이 없으면 `—`다. 티켓 없음을 `대기`로 쓰면 곧 라벨링될 클립으로 읽힌다.
      */
      label: "작업",
      kind: "status",
      width: "8rem",
      sortable: false,
      value: (c) => c.work_status ?? null,
      cell: (c) => {
        const w = c.work_status ? WORK_LABEL[c.work_status] : null;
        return w ? <Chip variant="dot" tone={w.tone}>{w.text}</Chip> : <span className="text-label-3">—</span>;
      },
    },
  ];

  const toolbar = (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="flex h-9 w-72 max-w-full items-center gap-2 rounded-control border border-separator-strong bg-panel px-3 transition-colors focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25">
        <Search size={14} className="shrink-0 text-label-3" aria-hidden="true" />
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="영상 제목·장면 요약"
          aria-label="영상 제목·장면 요약 검색"
          className="w-full border-0 bg-transparent text-callout text-label outline-none placeholder:text-placeholder"
        />
        {draft && (
          <button
            type="button"
            onClick={() => { setDraft(""); setParam("cq", null); }}
            className="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-label-3 hover:text-label-2"
            aria-label="검색어 지우기"
          >
            <X size={14} aria-hidden="true" />
          </button>
        )}
      </span>
    </span>
  );

  /* 빈 상태는 명사구 머리글과, 할 일이 있을 때만 버튼 하나다. */
  const empty = filtered ? (
    <EmptyState
      dense
      message="조건에 맞는 클립 없음"
      action={<Button variant="secondary" size="sm" onClick={resetFilters}>조건 초기화</Button>}
    />
  ) : (
    <EmptyState dense message="클립 없음" />
  );

  return (
    <div className="space-y-4">
      <Card>
        {/*
          도구 줄은 카드 머리에 둔다. 영상 탭과 같은 위치·같은 모양이다.
          바로 위 탭에 `클립`이 이미 있으므로 제목은 두지 않는다.
        */}
        <SectionHeader
          count={toolbar}
          action={
            <span className="flex items-center gap-3">
              <span className="text-caption tabular-nums text-label-3">{countText}</span>
              <SegmentedControl
                ariaLabel="보기 방식"
                value={view}
                onChange={setView}
                options={[
                  /* 아이콘과 글자를 한 줄로 묶는다. 버튼이 블록 흐름이면 아이콘이 글자 위로 쌓인다. */
                  {
                    value: "grid",
                    label: (
                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <LayoutGrid size={14} aria-hidden="true" />썸네일
                      </span>
                    ),
                  },
                  {
                    value: "table",
                    label: (
                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <Table2 size={14} aria-hidden="true" />표
                      </span>
                    ),
                  },
                ]}
              />
            </span>
          }
        />

        {/* 조회가 실패하면 빈 상태 대신 오류를 그린다. 빈 상태를 그리면 클립이 있는 프로젝트도 클립이 없는 것으로 표시된다. */}
        {clips.isError ? (
          <ErrorState
            message="클립 조회 실패"
            error={clips.error}
            /* 수도 함께 다시 받는다. 목록만 돌아오면 머리의 분모가 실패한 채 남는다. */
            onRetry={() => { void clips.refetch(); void total.refetch(); }}
          />
        ) : view === "grid" ? (
          <div className="p-4">
            {clips.isPending ? (
              <ClipGridSkeleton />
            ) : rows.length === 0 ? (
              empty
            ) : (
              /*
                영상별 묶음. 한 영상이 두 페이지에 걸치면 묶음 머리가 한 번 더 나온다.
                불러온 순서를 바꿔 합치면 `더 보기` 뒤 카드가 위쪽에 끼어들어서 이 중복을 허용한다.
              */
              <div className="space-y-6">
                {groupByVideo(rows).map((g, gi) => (
                  <section key={`${g.videoId}-${gi}`} aria-label={g.title}>
                    <div className="mb-3 flex items-baseline gap-2">
                      <h3 className="min-w-0 truncate text-callout font-semibold text-label">{g.title}</h3>
                      {slug && (
                        <Link
                          to={`/p/${slug}/videos/${g.videoId}`}
                          className="inline-flex shrink-0 items-center gap-0.5 text-caption text-label-3 hover:text-label hover:underline"
                        >
                          영상 상세
                          <ChevronRight size={12} aria-hidden="true" />
                        </Link>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                      {g.clips.map((c) => (
                        <div key={c.clip_id}>
                          <Thumb
                            seed={c.clip_id}
                            src={c.scene_thumbnail_url}
                            time={fmtRange(c.start_seconds, c.end_seconds)}
                            label={c.scene != null ? `#${c.scene}` : undefined}
                            onClick={() => setPreview(c)}
                          />
                          {/* 굵은 줄에는 카드마다 다른 내용인 장면 요약을 둔다. 고르는 규칙은 영상 상세·클립 보기와 같다(`clip_summary`). */}
                          <ClipCardCaption clip={c} />
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            )}
            {clips.hasNextPage && (
              <div className="flex justify-center pt-4">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void clips.fetchNextPage()}
                  disabled={clips.isFetchingNextPage}
                >
                  {clips.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                </Button>
              </div>
            )}
          </div>
        ) : (
          <DataTable<ClipDTO>
            bare
            columns={columns}
            rows={rows}
            rowKey={(c) => c.clip_id}
            onRowClick={(c) => setPreview(c)}
            /* 서버 커서 표라 내장 페이저를 끈다. 켜 두면 페이저의 분모가 불러온 행 수가 된다. */
            pageSize={0}
            loading={clips.isPending}
            empty={empty}
            /* 순서는 서버가 정한다. 헤더 정렬이 없으므로 정렬 기준을 바닥에 밝힌다. */
            footer={
              <>
                <span className="mr-auto text-caption text-label-3">등록 최신순 · 영상 안에서는 구간 순</span>
                {clips.hasNextPage && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void clips.fetchNextPage()}
                    disabled={clips.isFetchingNextPage}
                  >
                    {clips.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                  </Button>
                )}
                <span className="ml-auto" aria-hidden="true" />
              </>
            }
          />
        )}
      </Card>

      {/* 클립 미리보기. 구간 재생과 분석 결과, 다음 화면(영상 상세·클립 보기)으로 가는 링크를 둔다. */}
      {slug && <ClipPreviewModal clip={preview} slug={slug} onClose={() => setPreview(null)} />}
    </div>
  );
}
