/**
 * 콘텐츠 화면(`/p/:slug/content`)의 영상 탭.
 *
 * 콘텐츠 가드(`content`)를 통과한 관리자가 연다. 행을 누르면 영상 상세 페이지를 연다(`onOpenVideo`).
 * 라벨링·운영 흐름과 분리하려고 메타 인라인 편집과 업로드 버튼은 두지 않는다.
 *
 * 구성은 `PageHeader`(임베드가 아닐 때만), 문제 영상 줄(있을 때만), 카드 머리의 도구 줄(검색·필터·수), 서버 커서 표다.
 * 관리자가 이 목록을 여는 주된 이유가 막힌 영상 찾기라서 문제 영상 줄을 표 위에 둔다.
 *
 * 검색과 필터는 서버가 적용하고(`q`·`prep`) 조건은 주소에 둔다.
 * 그래서 영상을 열었다 돌아와도 같은 조건이 다시 적용되고, 커서 페이지 밖의 영상도 찾을 수 있다.
 */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Film, Search, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { Button, buttonClasses } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { FilterMenu } from "@/shared/ui/FilterMenu";
import { PageHeader } from "@/shared/ui/PageHeader";
import { SectionHeader } from "@/shared/ui/SectionHeader";
import { Thumb } from "@/shared/ui/Thumb";
import { blockedAtLabel, STAGE_LABEL, type PrepStage } from "@/features/ingest";
import { stageChip } from "../lib/video_stage";
import { fetchContentVideos, fetchVideoBuckets, libraryKeys } from "../api";
import type { VideoDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { airedAtToInput } from "@/lib/video_meta";
import { ProjectShellState, useProjects } from "@/features/projects";

/** 한 번에 받는 영상 수. 서버 상한은 200이다. 검색·필터는 서버가 하므로 크게 받지 않는다. */
const PAGE_SIZE = 100;

/** 검색어를 주소에 싣기 전에 기다리는 시간(ms). 주소가 바뀌면 서버를 조회하므로 글자마다 조회하지 않게 한다. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * 준비 상태 필터 옵션. 서버 준비 단계(`prep_stage`) 값 그대로이고 이름은 준비 화면과 같은 사전이다.
 * 사람이 조치할 상태(문제·분석 요청 대기)를 앞에 둔다.
 */
const PREP_ORDER: readonly PrepStage[] = [
  "blocked", "awaiting_analysis", "uploading", "probing", "transcoding",
  "registering", "analyzing", "summarizing", "ready_for_work",
];
const PREP_OPTIONS = PREP_ORDER.map((k) => ({ value: k, label: STAGE_LABEL[k] }));

/** URL 값을 준비 단계로 읽는다. 목록에 없는 값은 필터가 없는 것으로 본다. */
function parsePrep(raw: string | null): PrepStage | null {
  return PREP_ORDER.find((k) => k === raw) ?? null;
}

/** 서버가 업로드 확정 때 남긴 중복 표식. 같은 내용의 영상이 이미 있다는 뜻이다. */
function duplicateOf(v: VideoDTO): string | undefined {
  return (v.metadata as { duplicate_of?: string } | null)?.duplicate_of ?? undefined;
}

/** 제목 아래 작은 줄을 만든다. 프로그램명(제목에 없을 때)·방송사·회차 중 있는 것만 잇는다. 모두 없으면 빈 문자열이다. */
function subline(v: VideoDTO): string {
  const program = v.program_name && !v.title.includes(v.program_name) ? v.program_name : null;
  return [program, v.broadcaster, v.episode].filter(Boolean).join(" · ");
}

/**
 * 표의 열을 만든다. 서버 커서 표라 모든 열의 정렬을 끈다(`sortable: false`).
 * 헤더 정렬은 불러온 행만 정렬해 페이지 밖의 영상이 순서에서 빠진다. 순서는 서버의 등록 최신순이다.
 */
function buildColumns(slug: string, rowOpens: boolean): DataColumn<VideoDTO>[] {
  return [
    {
      /*
        대표 썸네일(첫 클립의 장면 프레임). 작게 두어 한 화면에 행이 많이 보이게 한다.
        이미지가 없으면 작은 빈 표시를 그린다. 이 열은 눌러도 재생되지 않으므로 재생 아이콘을 쓰지 않는다.
      */
      label: "썸네일",
      kind: "image",
      width: "5rem",
      sortable: false,
      cell: (v) =>
        v.thumbnail_url ? (
          <div className="w-16">
            <Thumb seed={v.video_id} src={v.thumbnail_url} />
          </div>
        ) : (
          <div className="flex aspect-video w-16 items-center justify-center rounded-control bg-fill" aria-hidden="true">
            <Film size={14} className="text-label-4" />
          </div>
        ),
    },
    {
      label: "제목",
      kind: "title",
      sortable: false,
      value: (v) => v.title,
      // 제목은 기본 글자색으로 두고 hover 때만 accent로 바꿔 표 전체가 파란 링크로 덮이지 않게 한다.
      cell: (v) => (
        <span className="block min-w-0">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            {/*
              행 전체가 영상을 여는 버튼이면 제목은 글자로 둔다.
              행(`role="button"`) 안에 버튼을 또 두면 스크린리더가 겹친 컨트롤을 제대로 읽지 못한다.
            */}
            {rowOpens ? (
              <span className="font-medium text-label">{v.title}</span>
            ) : (
              <Link
                to={`/p/${slug}/videos/${v.video_id}`}
                className="font-medium text-label transition-colors hover:text-accent hover:underline"
              >
                {v.title}
              </Link>
            )}
            {/*
              중복 표시. 서버는 업로드 확정 때 내용 해시로 같은 파일을 찾아 `duplicate_of`를 남기고 등록은 막지 않는다.
              표시가 없으면 같은 파일을 두 번 올린 사실을 알 수 없어 분석 요청이 두 번 나간다.
            */}
            {duplicateOf(v) && (
              <Chip tone="warn" title="같은 내용의 영상이 이미 등록되어 있습니다">중복</Chip>
            )}
          </span>
          {/*
            제목 아래 작은 줄에 프로그램명·방송사·회차를 가운뎃점으로 잇는다.
            방송 영상 제목은 대개 `프로그램명 날짜 (방송사)` 형식이라 제목에 이미 든 프로그램명은 뺀다.
            검색이 세 값을 모두 찾으므로 검색어와 맞은 값을 이 줄에서 확인할 수 있다.
            방송사·회차를 따로 열로 두면 1280px 폭에서 표가 가로로 넘쳐 등록일 열이 잘린다.
          */}
          {subline(v) && (
            <span className="mt-0.5 block truncate text-caption tabular-nums text-label-3">{subline(v)}</span>
          )}
        </span>
      ),
    },
    {
      /*
        클립 수. 없으면 0이다. 진행 단계는 `준비 상태` 열이 서버의 준비 단계로 보여 준다.
      */
      label: "클립",
      kind: "number",
      align: "right",
      sortable: false,
      value: (v) => v.clip_count,
      /* 0개는 볼 클립이 없다는 뜻이라 흐린 색으로 두어 수가 있는 행보다 약하게 보이게 한다. */
      cell: (v) => (
        <span className={cn("tabular-nums text-callout", v.clip_count > 0 ? "text-label-2" : "text-label-3")}>
          {v.clip_count.toLocaleString("ko-KR")}
        </span>
      ),
    },
    /*
      모든 영상의 `copyright_policy`가 `pre_cleared`라 모든 행이 같은 값이므로 `정책` 열은 두지 않는다.
    */
    {
      /*
        서버가 정한 준비 단계(`prep_stage`). 막혔으면 위치를 붙인다(`문제 · 분석본 변환`).
        영상 상세 머리와 같은 칩이고, 문제 영상은 이 열에서 찾는다.
      */
      label: "준비 상태",
      kind: "status",
      sortable: false,
      value: (v) => v.prep_stage ?? null,
      /* 상태는 점 색으로 표시하고 글자는 기본 글자색으로 둔다. */
      cell: (v) => {
        const st = stageChip(v);
        return st ? (
          <Chip variant="dot" tone={st.tone}>{st.label}</Chip>
        ) : (
          <span className="text-caption text-label-3">—</span>
        );
      },
    },
    {
      label: "등록일",
      kind: "date",
      sortable: false,
      value: (v) => v.created_at,
      cell: (v) => (
        /* 영상 상세 사이드바의 등록일과 같은 형식(한국 날짜 `YYYY-MM-DD`)으로 적는다. */
        <span className="text-footnote tabular-nums text-label-2">{airedAtToInput(v.created_at)}</span>
      ),
    },
  ];
}

/** 영상 목록 화면을 그린다. `embedded`면 머리글 없이 콘텐츠 화면 안에 들어간다. */
export function VideoListPage({
  onOpenVideo,
  embedded = false,
}: { onOpenVideo?: (id: string) => void; embedded?: boolean } = {}) {
  const { slug } = useParams<{ slug: string }>();
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const prep = parsePrep(params.get("prep"));
  const filtered = q !== "" || prep !== null;

  /** 주소의 조건 하나를 바꾼다. `replace`라 조건을 바꿔도 뒤로 가기 이력이 쌓이지 않는다. */
  const setParam = useCallback(
    (key: string, value: string | null) =>
      // 갱신 함수 형식으로 쓴다. 객체를 넘기면 같은 주소의 `?tab=`이 지워진다.
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      }, { replace: true }),
    [setParams],
  );

  /*
    검색 입력은 바로 그리고, 주소에는 입력이 잠시 멈춘 뒤 싣는다.
    주소가 바뀌면(뒤로 가기) 입력도 따라 바뀐다. 입력 중인 앞뒤 공백은 지우지 않는다.
  */
  const [draft, setDraft] = useState(q);
  useEffect(() => {
    setDraft((d) => (d.trim() === q ? d : q));
  }, [q]);
  useEffect(() => {
    if (draft.trim() === q) return;
    const t = window.setTimeout(() => setParam("q", draft.trim() || null), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [draft, q, setParam]);

  const list = useProjects();
  const project = list.data?.find((p) => p.slug === slug);

  const videos = useInfiniteQuery({
    // 조건을 키에 넣는다. 빼면 조건을 바꿔도 앞 조건의 캐시가 그대로 그려진다.
    queryKey: libraryKeys.contentVideos(project?.project_id, { q, prep }),
    queryFn: ({ pageParam }) => fetchContentVideos(project!.project_id, PAGE_SIZE, { q, prep }, pageParam),
    enabled: !!project,
    initialPageParam: undefined as { at: string; id: string } | undefined,
    getNextPageParam: (last) =>
      last.length === PAGE_SIZE
        ? { at: last[last.length - 1].created_at, id: last[last.length - 1].video_id }
        : undefined,
    // 조건을 바꾸는 동안 앞 결과를 둔다. 그래서 글자를 입력할 때마다 표가 골격으로 깜빡이지 않는다.
    placeholderData: keepPreviousData,
    // 실패는 아래 `ErrorState`가 표시하므로 토스트를 띄우지 않는다.
    meta: { handled: true },
  });
  const counts = useQuery({
    queryKey: libraryKeys.contentBuckets(project?.project_id),
    queryFn: () => fetchVideoBuckets(project!.project_id),
    enabled: !!project,
  });

  if (list.isLoading) return <PageSkeleton table />;
  // 프로젝트가 없는 경우와 불러오지 못한 경우를 구분하려고 공용 셸(`features/projects`의 `ProjectShellState`)을 쓴다.
  // 직접 그리면 목록 조회가 실패해도 프로젝트가 없는 것으로 표시되고 재시도도 없다.
  if (!project) return <ProjectShellState isError={list.isError} onRetry={() => void list.refetch()} />;

  const columns = buildColumns(slug!, !!onOpenVideo);
  const rows = (videos.data?.pages ?? []).flat();
  const fmt = (n: number) => n.toLocaleString("ko-KR");
  /*
    수에는 센 대상을 밝힌다. `전체`는 서버 집계(`buckets.total`)일 때만 쓴다.
    조건이 걸렸고 더 받을 행이 남았으면 조건에 맞는 전체 수가 없어서 불러온 수라고 쓴다.
    아직 받기 전이면 0 대신 `—`를 쓴다.
  */
  const total = counts.data?.total;
  const settled = videos.data !== undefined && !videos.isPlaceholderData;
  const countText = !filtered
    ? total === undefined
      ? "전체 —개"
      : videos.hasNextPage
        ? `전체 ${fmt(total)}개 중 ${fmt(rows.length)}개 표시`
        : `전체 ${fmt(total)}개`
    : !settled
      ? "결과 —개"
      : videos.hasNextPage
        ? `불러온 ${fmt(rows.length)}개 · 더 있음`
        : `결과 ${fmt(rows.length)}개${total === undefined ? "" : ` · 전체 ${fmt(total)}개`}`;

  /*
    문제 영상 줄. 수는 막힌 위치별 수(`buckets.issues`)의 합이고, 목록 필터 `prep=blocked`와
    같은 서버 CASE(`_prep_stage_case`)로 센다. 그래서 `문제만 보기`를 누른 뒤 표의 수와 맞는다.
    `buckets.blocked`는 반입 상태 기준(`_ingest_bucket_case`)이라 수가 다를 수 있어 쓰지 않는다.
    이 화면에서 호박색은 이 줄에만 쓴다.
  */
  const issues = counts.data?.issues ?? [];
  const blockedTotal = issues.reduce((sum, i) => sum + i.count, 0);
  const resetFilters = () => {
    setDraft("");
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("q");
      next.delete("prep");
      return next;
    }, { replace: true });
  };

  const toolbar = (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="flex h-9 w-72 max-w-full items-center gap-2 rounded-control border border-separator-strong bg-panel px-3 transition-colors focus-within:border-focus focus-within:ring-2 focus-within:ring-focus/25">
        <Search size={14} className="shrink-0 text-label-3" aria-hidden="true" />
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="제목·방송사·프로그램명"
          aria-label="제목·방송사·프로그램명 검색"
          className="w-full border-0 bg-transparent text-callout text-label outline-none placeholder:text-placeholder"
        />
        {draft && (
          <button
            type="button"
            onClick={() => { setDraft(""); setParam("q", null); }}
            className="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-label-3 hover:text-label-2"
            aria-label="검색어 지우기"
          >
            <X size={14} aria-hidden="true" />
          </button>
        )}
      </span>
      <FilterMenu
        label="준비 상태"
        value={prep ?? ""}
        options={PREP_OPTIONS}
        onChange={(v) => setParam("prep", v || null)}
      />
    </span>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-0">
      {/* 콘텐츠 화면(영상·클립 탭)에 임베드되면 PageHeader는 부모(ContentPage)가 그린다. */}
      {!embedded && (
        <PageHeader
          title="콘텐츠"
          subtitle={total ? `영상 ${fmt(total)}개` : undefined}
        />
      )}

      {blockedTotal > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card bg-amber-soft px-4 py-3 text-label">
          <span className="size-2 shrink-0 rounded-capsule bg-amber" aria-hidden="true" />
          {/* 절 제목이 아니라 수라서 제목 요소를 쓰지 않는다. 글자색은 줄에서 물려받는다. */}
          <b className="text-callout font-semibold">문제 영상 {fmt(blockedTotal)}개</b>
          {/*
            막힌 위치별 수는 위치 이름과 수를 붙인 읽기 전용 칩 목록이다.
            가운뎃점으로 다섯 위치를 이으면 한 문장처럼 읽혀 수와 위치의 대응을 놓친다. 가운뎃점 나열은 셋까지만 쓴다.
          */}
          <ul className="flex flex-wrap items-center gap-1.5" aria-label="막힌 위치별 영상 수">
            {issues.map((i) => (
              <li key={i.stage}>
                <Chip className="gap-1.5 bg-panel font-medium">
                  {blockedAtLabel(i.stage)}
                  <b className="font-semibold tabular-nums text-label">{fmt(i.count)}</b>
                </Chip>
              </li>
            ))}
          </ul>
          {prep !== "blocked" && (
            <Button size="sm" variant="secondary" className="ml-auto" onClick={() => setParam("prep", "blocked")}>
              문제만 보기
            </Button>
          )}
        </div>
      )}

      <Card>
        {/* 바로 위 탭에 `영상`이 이미 있으므로 제목은 두지 않는다. 도구 줄이 왼쪽, 수가 오른쪽이다. */}
        <SectionHeader
          count={toolbar}
          action={<span className="text-caption tabular-nums text-label-3">{countText}</span>}
        />
        {videos.isError ? (
          <ErrorState
            message="영상 목록 조회 실패"
            error={videos.error}
            /* 위치별 수도 함께 다시 받는다. 같은 장애로 둘 다 실패했으면 목록만 돌아오고 `전체 —개`가 남기 때문이다. */
            onRetry={() => { void videos.refetch(); void counts.refetch(); }}
          />
        ) : (
          <DataTable<VideoDTO>
            bare
            columns={columns}
            rows={rows}
            rowKey={(v) => v.video_id}
            loading={videos.isPending}
            /* 내장 페이저를 끈다. 켜 두면 페이저의 분모가 불러온 행 수가 된다. */
            pageSize={0}
            onRowClick={onOpenVideo ? (v) => onOpenVideo(v.video_id) : undefined}
            empty={
              filtered ? (
                /* 빈 상태는 명사구 머리글과, 할 일이 있을 때만 버튼 하나다. */
                <EmptyState
                  dense
                  message="조건에 맞는 영상 없음"
                  action={<Button variant="secondary" size="sm" onClick={resetFilters}>조건 초기화</Button>}
                />
              ) : (
                <EmptyState
                  dense
                  message="영상 없음"
                  /* 영상 등록은 관리자가 여는 작업 단계 화면이고, 이 목록도 관리자만 연다. */
                  action={
                    <Link to={`/p/${slug}/pipeline?stage=ingest`} className={buttonClasses("secondary", "sm")}>
                      영상 등록 열기
                    </Link>
                  }
                />
              )
            }
            /* 순서는 서버가 정한다(등록 최신순). 헤더 정렬이 없으므로 정렬 기준을 바닥에 밝힌다. */
            footer={
              <>
                <span className="mr-auto text-caption text-label-3">등록 최신순</span>
                {videos.hasNextPage && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void videos.fetchNextPage()}
                    disabled={videos.isFetchingNextPage}
                  >
                    {videos.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                  </Button>
                )}
                <span className="ml-auto" aria-hidden="true" />
              </>
            }
          />
        )}
      </Card>
    </div>
  );
}
