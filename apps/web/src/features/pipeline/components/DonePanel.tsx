/**
 * 완료 단계 화면. 승인되어 내보내기에 실릴 클립을 모아 보여 준다.
 *
 * 서버는 내보내기와 같은 쿼리(`approved_rows_stmt`)로 행을 주고, 화면은 그 행을 거르지 않고 그린다.
 * 화면이 따로 거르거나 세면 보인 클립과 실제로 나가는 클립이 달라지고, 그 차이는 내보내기를 받아 보기 전에는 알 수 없다.
 * 그래서 나가면 안 되는 클립이 이 목록에 보이면 내보내기 쪽 결함이다.
 *
 * 축 네 개(`전체`, `납품 대상`, `납품 불가`, `규칙만 검사`)는 주소(`?filter=`)로 고르고 서버가 거른다.
 * 받은 50건 안에서 거르면 `납품 불가 없음`이 잘못 표시된다.
 * 건수는 `/completed/summary`가 센 값만 쓴다. 서버가 최대 2,000행을 검증해 세므로 목록(`summary=false`)과 따로 받아 캐시한다.
 * 목록과 함께 받으면 `더 보기`를 누를 때마다 그 계산이 다시 돈다.
 *
 * 고른 축 때문에 모든 행이 같은 값이 되는 열은 뺀다(`납품 대상`의 납품 열, `규칙만 검사`의 품질검수 열).
 * `납품 불가` 축에서는 납품 열을 사유 열로 바꾼다. 사용자는 사유를 보려고 그 축을 고른다.
 * 표는 다른 단계 화면과 같은 `DataTable`을 써서 정렬·검색·행 수 표기 관례를 맞춘다.
 * 이 화면에는 편집 기능이 없다. 목록을 보고 한 건을 열어 확인만 한다.
 */
import { useCallback, useEffect, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { SegmentedControl } from "@/shared/ui/SegmentedControl";
import type { components } from "@reborn/contracts";

import { Chip } from "@/shared/ui/Chip";
import { type ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { hasProjectRole } from "@/lib/roles";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";
import { useProjects } from "@/features/projects";
import {
  COMPLETED_FILTERS,
  completedFilterLabel,
  completedFilterOf,
  QC_RULES_NOTE,
  type CompletedFilter,
  visibleCompletedFilters,
} from "../lib/completed_filter";
import { type CompletedSummary, fetchCompleted, fetchCompletedSummary, pipelineKeys } from "../api";
import { ExportPanel } from "./ExportPanel";

/** 목록을 한 번에 받는 행 수. */
const PAGE = 50;

/* 계약 타입을 그대로 쓴다.
   손으로 적은 타입은 서버가 필드를 더해도 화면에 반영되지 않고, 드리프트 게이트도 잡아내지 못한다.
   목록 쪽(`CompletedPage`)과 집계(`CompletedSummary`)의 타입은 호출과 함께 `api.ts`에 있다. */
type CompletedRow = components["schemas"]["CompletedRow"];

/** 수를 천 단위 구분 기호를 넣어 쓴다. */
const num = (n: number) => n.toLocaleString("ko-KR");

/**
 * 승인 날짜를 `9월 23일`처럼 쓴다. 올해가 아니면 연도를 붙인다.
 * 같은 영역의 내보내기 이력과 날짜 형식을 맞춘다.
 */
function fmtDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const thisYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("ko-KR", {
    ...(thisYear ? {} : { year: "numeric" as const }),
    month: "long", day: "numeric",
  });
}

/**
 * 축마다 서버가 센 수를 돌려준다. 값이 없는 수는 `null`이고, 그때 세그먼트는 수를 그리지 않는다.
 *
 * 수는 모두 `/completed/summary`에서 온다. 화면이 받은 행을 세면 50건 안의 수가 되어 전체 수가 틀린다.
 * `납품 불가`는 서버가 따로 주지 않으므로 같은 응답의 두 수를 빼서 구한다.
 * 목록의 `total`과 섞어 빼면 조회 시각이 달라 음수가 나올 수 있다.
 *
 * `deliverable_total`이 `null`이면(상한 초과) 납품 두 축의 수를 비운다.
 * 0으로 그리면 하나도 내보낼 수 없다는 뜻으로 읽힌다. 세지 않은 이유는 옆 줄이 알린다.
 */
function filterCounts(summary?: CompletedSummary): Record<CompletedFilter, number | null> {
  const scanned = summary?.deliverable_total ?? null;
  return {
    all: summary ? summary.total : null,
    deliverable: scanned,
    excluded: summary && scanned != null ? summary.total - scanned : null,
    qc_rules: summary ? summary.qc_unreviewed_total : null,
  };
}

/** 품질검수 열의 이름. 열을 숨길 때도 이 이름으로 찾는다. */
const QC_COL = "품질검수";

/**
 * 빈 상태 문구를 고른다. 완료가 없는 경우와 이 축에 해당하는 클립이 없는 경우를 구분한다.
 *
 * 축을 고른 채 `완료 클립 없음`을 그리면 승인분이 모두 사라진 것처럼 읽힌다.
 * 완료가 0건인데 `납품 불가 클립 없음`이라고 쓰면 모두 관문을 통과한 것처럼 읽힌다. 그래서 완료 전체 수를 먼저 확인한다.
 *
 * @param completedTotal 완료 전체 수. 아직 받지 않았으면 `undefined`이고, 곧 바뀔 문구를 먼저 보이지 않도록 기본 문구를 쓴다.
 */
function emptyOf(filter: CompletedFilter, completedTotal: number | undefined): { message: string } {
  if (filter === "all" || completedTotal === 0 || completedTotal == null) return { message: "완료 클립 없음" };
  if (filter === "deliverable") return { message: "납품 대상 클립 없음" };
  if (filter === "excluded") return { message: "납품 불가 클립 없음" };
  return { message: "규칙만 검사한 클립 없음" };
}

/**
 * 거르기 축 세그먼트를 그린다. 거르기와 세기는 모두 서버가 한다.
 *
 * 수를 세그먼트 안에 넣는다.
 * 머리에 `완료 92 · 납품 불가 4`를 따로 그리면 바로 아래 세그먼트와 같은 수를 두 번 보여 준다.
 * 수가 무엇을 세는지는 축 이름이 보여 준다.
 */
function FilterBar({
  value, onChange, summary, shown,
}: {
  value: CompletedFilter;
  onChange: (f: CompletedFilter) => void;
  summary?: CompletedSummary;
  /** 그릴 축. 역할과 품질검수 사용 여부로 `visibleCompletedFilters`가 정한다. */
  shown: readonly CompletedFilter[];
}) {
  const counts = filterCounts(summary);
  return (
    <SegmentedControl
      size="sm"
      ariaLabel="완료 목록 거르기"
      value={value}
      onChange={onChange}
      options={COMPLETED_FILTERS.filter((f) => shown.includes(f.value)).map(({ value: v, label }) => ({
        value: v,
        label: (
          <span className="flex items-center gap-1" title={v === "qc_rules" ? QC_RULES_NOTE : undefined}>
            {label}
            {counts[v] != null && <span className="tabular-nums">{num(counts[v]!)}</span>}
          </span>
        ),
      }))}
    />
  );
}

/** 완료 단계 화면을 그린다. 관리자에게는 내보내기 패널을, 모두에게는 완료 목록을 보여 준다. */
export function DonePanel() {
  const { selectedProjectId } = useSelectedProject();
  const { user } = useAuth();
  const navigate = useNavigate();
  const { slug } = useParams<{ slug: string }>();

  /* 고른 축은 주소에 둔다. 그래서 북마크, 공유, 뒤로 가기가 그대로 동작한다.
     내보내기 패널의 `납품 불가 4건`도 링크 하나로 이 축을 열 수 있다.
     세그먼트를 바꾸는 동안 뒤로 가기 기록이 쌓이지 않게 `replace`를 쓴다.
     기록이 쌓이면 화면을 떠날 때 뒤로 가기를 네 번 눌러야 한다. */
  const [params, setParams] = useSearchParams();
  const rawFilter = completedFilterOf(params.get("filter"));
  const setFilter = useCallback(
    (f: CompletedFilter) =>
      // 갱신 함수 형태로 쓴다. 객체를 넘기면 같은 주소의 `?stage=done`이 사라진다.
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (f === "all") next.delete("filter");
        else next.set("filter", f);
        return next;
      }, { replace: true }),
    [setParams],
  );

  // 내보내기의 서버 가드는 project_admin이다.
  // 쓸 수 없는 버튼을 비활성으로 두지 않고 패널을 아예 그리지 않는다.
  const projects = useProjects();
  const project = projects.data?.find((p) => p.project_id === selectedProjectId);
  const canExport = hasProjectRole(user, project, ["project_admin"]);
  // 전체 집계는 목록과 따로 한 번 받는다.
  // 완료 워크벤치와 같은 쿼리 키라서 캐시를 함께 쓴다. 보이는 축을 정할 때도 쓰므로 목록보다 먼저 둔다.
  const summary = useQuery({
    queryKey: pipelineKeys.completedSummary(selectedProjectId),
    queryFn: () => fetchCompletedSummary(selectedProjectId),
    enabled: !!selectedProjectId,
    staleTime: 30_000,
  });
  /*
    품질검수를 쓰지 않는 프로젝트에는 `규칙만 검사` 축이 없다(`visibleCompletedFilters`).
    주소에 볼 수 없는 축이 남아 있으면 전체 축으로 처리한다.
  */
  const shownFilters = visibleCompletedFilters({
    qcEnabled: project?.caption_qc?.enabled ?? false,
    qcRulesCount: summary.data?.qc_unreviewed_total,
  });
  /* 품질검수 축은 남은 건수가 필요하므로 프로젝트와 요약을 받은 뒤에 판정한다. */
  const filterHidden = !!project && !shownFilters.includes(rawFilter) && summary.data != null;
  const filter: CompletedFilter = filterHidden ? "all" : rawFilter;
  /*
    볼 수 없는 축은 주소에서도 지운다. 주소에 `납품 불가`가 남은 채 전체 목록이 보이면
    걸러진 목록으로 읽힌다.
  */
  useEffect(() => {
    if (filterHidden) setFilter("all");
  }, [filterHidden, setFilter]);

  const q = useInfiniteQuery({
    // 쿼리 키에 축을 넣는다. 넣지 않으면 축을 바꿔도 이전 축의 캐시가 그대로 그려진다.
    queryKey: pipelineKeys.completed(selectedProjectId, filter),
    queryFn: ({ pageParam = 0 }) => fetchCompleted(selectedProjectId, { limit: PAGE, offset: pageParam, filter }),
    initialPageParam: 0,
    // 서버가 offset을 받으므로 다음 페이지 위치는 지금까지 받은 행 수로 정한다.
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
    enabled: !!selectedProjectId,
    // 승인 검사가 끝나지 않은 행이 있으면 그동안만 다시 받는다. 검사는 보통 수십 초 안에 끝난다.
    // 다시 받지 않으면 화면을 다시 열 때까지 `검사 중`이 남는다.
    refetchInterval: (query) =>
      (query.state.data?.pages ?? []).some((pg) => pg.items.some((r) => r.qc_running)) ? 5_000 : false,
  });
  const rows = useMemo(() => (q.data?.pages ?? []).flatMap((p) => p.items), [q.data]);
  const head = q.data?.pages[0];
  const total = head?.total ?? 0;
  /* 관문 검사가 상한에서 끊겼는지 판정한다. 서버는 검사한 수와 후보 수를 둘 다 준다.
     끊긴 사실을 알리지 않으면 사용자는 이 목록을 전체로 보고 `납품 불가 없음`을 사실로 받아들인다. */
  const cut =
    head?.filter_scanned != null && head.filter_candidates != null
    && head.filter_scanned < head.filter_candidates
      ? { scanned: head.filter_scanned, candidates: head.filter_candidates }
      : null;

  /* 품질검수 열은 값이 하나라도 있을 때만 그린다. QC를 켠 적 없는 프로젝트에서는 모든 행이 `미검사`인 열이 된다.
     조건은 `caption_qc.enabled` 대신 받은 값으로 판단한다. 지금은 껐어도 이전에 검사를 돌린 적이 있으면 그 열은 쓸모가 있다.
     `규칙만 검사` 축에서는 모든 행이 `규칙만`이고 축 이름이 그 값을 이미 보여 주므로 열을 뺀다. */
  const showQc = rows.some((r) => r.qc_checked_by || r.qc_running) && filter !== "qc_rules";

  /* 헤더 정렬을 끈다(모든 열 `sortable: false`). 이 표는 서버가 50건씩 주는 커서 표라서 헤더 정렬은 받은 행만 정렬한다.
     일부만 받은 상태에서 `승인`을 누르면 최신 승인 순처럼 보이지만 받지 않은 행은 빠져 있다.
     아래 `pageSize={0}`도 같은 이유로 둔다. 정렬 캐럿을 그리지 않아 정렬할 수 없는 표임을 드러낸다. */
  const columns: DataColumn<CompletedRow>[] = [
    {
      label: "영상 · 구간",
      kind: "text",
      sortable: false,
      value: (r) => r.video_title ?? r.program_name ?? "",
      cell: (r) => (
        <div>
          <div className="text-label">{r.video_title ?? r.program_name ?? "제목 없음"}</div>
          <div className="font-mono text-caption tabular-nums text-label-3">
            {/* 클립 구간은 `fmtClipRange` 한 함수로 그린다. 표와 상세가 같은 구간을 다른 함수로 그리지 않는다. */}
            {r.start_seconds != null && r.end_seconds != null
              ? fmtClipRange(r.start_seconds, r.end_seconds)
              : "영상 단위"}
          </div>
        </div>
      ),
    },
    {
      label: "캡션",
      kind: "text",
      sortable: false,
      value: (r) => r.caption_brief,
      /*
        캡션은 폭을 제한하고 두 줄에서 자른다.
        표 셀은 `whitespace-nowrap`이라 전문이 한 줄로 늘어나고, 그 뒤의 납품·승인 열이 화면 밖으로 밀린다.
        전문은 행을 눌러 여는 완료 워크벤치와 마우스를 올렸을 때의 툴팁에서 본다.
        제한하지 않으면 1440px에서 이 열이 1,150px까지 늘어나 표가 가로로 넘친다.
      */
      cell: (r) =>
        r.caption_brief ? (
          <p
            className="line-clamp-2 min-w-[14rem] max-w-[28rem] whitespace-normal"
            title={r.caption_brief}
          >
            {r.caption_brief}
          </p>
        ) : (
          <span className="text-label-3">캡션 없음</span>
        ),
    },
    {
      label: "문장",
      kind: "number",
      align: "right",
      sortable: false,
      // 헤더에 정의를 단다. 이 수가 무엇을 세는지 화면이 알려 준다.
      define: "삭제하지 않은 캡션 문장 수입니다.",
      value: (r) => r.sentence_count,
      cell: (r) => (r.sentence_count === 0 ? "—" : r.sentence_count.toLocaleString("ko-KR")),
    },
    {
      /* 열 이름은 설정·품질검수 화면과 같은 `품질검수`다. */
      label: QC_COL,
      kind: "text",
      sortable: false,
      value: (r) => r.qc_checked_by ?? "",
      cell: (r) => {
        /* 세 값을 합치지 않는다.
           `rules`는 검사가 돌았지만 모델을 부르지 못했다는 뜻이고, `null`은 그때 검사가 없었다는 뜻이다.
           하나로 그리면 검사 도입 전의 승인분이 모두 경고가 되어 실제 경고를 구분할 수 없다. */
        if (r.qc_checked_by === "model") return "모델";
        if (r.qc_checked_by === "rules") return <Chip tone="amber" title={QC_RULES_NOTE}>규칙만</Chip>;
        /* 승인 직후에는 검사가 아직 돌지 않았다. `미검사`로 그리면 검사 기능이 없던 승인분과 같아 보인다.
           판정은 서버(`qc_running`)가 한다. */
        if (r.qc_running) return <span className="text-label-2">검사 중</span>;
        return <span className="text-label-3">미검사</span>;
      },
    },
    /* `납품 불가` 축에서는 이 열이 사유 열이 된다.
       모든 행이 `불가`인 칩 열은 쓸모가 없고, 사용자는 사유를 보려고 그 축을 고른다.
       사유는 서버가 만든 문장을 그대로 쓴다. */
    filter === "excluded"
      ? {
          label: "납품 불가 사유",
          kind: "text" as const,
          sortable: false,
          /* 관문 세 가지를 정의에 나열하지 않는다.
             사유 문장이 이미 `비식별화 신고:`, `타임코드:`, `캡션:`처럼 막힌 항목 이름으로 시작한다
             (`export_package.blocker`).
             나열하면 같은 내용을 두 번 보여 주고, 항목이 늘면 이 문장만 실제와 달라진다. */
          define: "내보내기와 같은 기준이 낸 사유입니다. 여럿이면 첫 사유를 보여 주고 나머지 수를 같이 적습니다.",
          value: (r: CompletedRow) => r.blockers[0] ?? "",
          cell: (r: CompletedRow) =>
            r.blockers.length === 0 ? (
              // 축과 행이 어긋난 경우. 임의의 사유 대신 사유가 없다고 표시한다.
              <span className="text-label-3">사유 없음</span>
            ) : (
              <span className="text-bad-text" title={r.blockers.join("\n")}>
                {r.blockers[0]}
                {r.blockers.length > 1 && (
                  <span className="text-label-3"> 외 {r.blockers.length - 1}건</span>
                )}
              </span>
            ),
        }
      : {
          label: "납품",
          kind: "text" as const,
          sortable: false,
          // 이 열의 뜻은 헤더가 정의한다. 값은 패키지가 쓰는 관문 전부(비식별화 신고, 타임코드, 스키마)의 통과 여부다.
          define: "지금 내보내면 납품 패키지에 실리는지입니다. 내보내기와 같은 기준으로 판정하므로 여기서 불가면 패키지에도 실리지 않습니다.",
          value: (r: CompletedRow) => (r.deliverable ? "" : "불가"),
          cell: (r: CompletedRow) =>
            r.deliverable ? (
              // 실리는 행은 칩 없이 흐린 글자로 둔다.
              // 모든 행에 칩을 달면 불가 행이 눈에 띄지 않는다.
              <span className="text-label-3">대상</span>
            ) : (
              /* 이 열에는 신호만 보여 준다. 사유 전문은 상세에 있고, 행을 누르면 상세로 간다.
                 툴팁 사유는 서버가 화면 이름으로 만든 문장이다(`캡션: 10자 이상 필요`).
                 값 이름은 클립 상태의 `제외`와 겹치지 않게 `불가`로 쓴다. */
              <span className="flex min-w-[9rem] flex-col gap-0.5 whitespace-normal" title={r.blockers.join("\n") || undefined}>
                <span className="inline-flex items-center gap-1.5 font-medium text-label">
                  <span className="size-2 rounded-capsule bg-bad" aria-hidden="true" />불가
                </span>
                {r.blockers[0] && <span className="text-caption text-label-3">{r.blockers[0]}</span>}
              </span>
            ),
        },
    {
      label: "승인",
      kind: "date",
      sortable: false,
      /* 개발 용어(`수기 전이`)를 쓰지 않는다. */
      define: "마지막 승인 날짜입니다. 승인 기록이 없는 클립은 이전 방식으로 승인된 것이며 납품에는 영향이 없습니다.",
      value: (r) => r.approved_at ?? "",
      cell: (r) =>
        r.approved_at
          ? <span title={new Date(r.approved_at).toLocaleString("ko-KR")}>{fmtDay(r.approved_at)}</span>
          : <span className="text-label-3">승인 기록 없음</span>,
    },
  ];

  /* `납품 대상` 축에서는 납품 열이 모든 행에서 `대상`이 된다.
     축 이름이 이미 그 값을 보여 주므로 열을 뺀다.
     `납품 불가` 축에서는 위에서 사유 열로 바꿨으므로 남긴다. */
  const shownColumns = columns.filter(
    (c) => (showQc || c.label !== QC_COL) && !(filter === "deliverable" && c.label === "납품"),
  );

  if (q.isError) {
    /* 원인이 확인된 오류는 원인을 알린다.
       403(멤버 아님)에 "다시 시도"를 주면 사용자는 눌러도 같은 오류만 보게 된다.
       재시도 버튼은 일시 오류(5xx, 연결 실패)에만 준다. */
    const err = q.error as unknown as ApiError;
    const t = errorText(err?.detail, { message: "완료 목록 조회 실패" });
    return (
      <ErrorState
        message={t.message}
        description={t.hint}
        onRetry={!err?.status || err.status >= 500 ? () => void q.refetch() : undefined}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* 완료가 0건이어도 패널은 남긴다. 만들기만 막고 이유는 패널이 알린다.
          패널이 없으면 완료가 모두 보류·회수됐을 때 지난 패키지를 다시 받을 방법이 없다. */}
      {canExport && selectedProjectId && (
        <ExportPanel
          projectId={selectedProjectId}
          /* 축을 고르면 `total`은 거른 뒤의 수다.
             이 값을 완료 전체 수로 넘기면 `납품 불가`를 고른 순간 패널이 "완료 4건"이라고 표시한다.
             그래서 요약을 받기 전의 대체값은 축이 `전체`일 때만 쓴다. */
          completedTotal={summary.data?.total ?? (q.data && filter === "all" ? total : null)}
          deliverableTotal={summary.data?.deliverable_total ?? null}
          qcUnreviewed={summary.data?.qc_unreviewed_total ?? 0}
          qcEnabled={project?.caption_qc?.enabled ?? false}
        />
      )}

      <Card>
        {/* 카드 머리에는 세그먼트만 왼쪽에 둔다. 레일 캡슐이 이미 `완료`라고 보여 주므로 제목은 없다.
            수가 세그먼트 안에 있어 세그먼트가 머리의 중심이 된다. 오른쪽에 두면 왼쪽 절반이 빈다. */}
        <div className="border-b border-separator px-5 py-3">
          <FilterBar value={filter} onChange={setFilter} summary={summary.data} shown={shownFilters} />
        </div>
        {/* 납품 두 축의 수를 세지 않았다는 사실을 알린다. 세그먼트가 수를 비운 이유다.
            이 줄이 없으면 사용자가 빈 수를 0으로 읽는다.
            전체 수가 상한 안이면 그리지 않는다. */}
        {summary.data && summary.data.deliverable_total == null && (
          <p className="border-b border-separator px-5 py-2 text-caption text-label-3">
            완료 클립 {num(summary.data.total)}개가 집계 상한{" "}
            {num(summary.data.deliverable_scan_limit)}개를 넘어 납품 대상·납품 불가는 세지 않았습니다.
          </p>
        )}
        {/* 관문 검사가 끊긴 목록이다. 판정 대상 수가 줄었다는 사실까지 알린다. */}
        {cut && (
          <div role="status" className="border-b border-separator bg-warn-muted px-5 py-2">
            <p className="text-caption font-medium text-warn-text">
              먼저 등록된 {num(cut.scanned)}개까지만 판정
            </p>
            <p className="text-caption text-label-2">
              후보 {num(cut.candidates)}개 중 나머지 {num(cut.candidates - cut.scanned)}개는 이 목록에
              없습니다.
            </p>
          </div>
        )}
        <DataTable
          columns={shownColumns}
          rows={rows}
          rowKey={(r) => r.work_item_id}
          loading={q.isLoading}
          /* 내장 페이저를 끈다(서버 커서 표의 규칙, `DataTable`의 `footer` 주석 참고).
             켜 두면 페이저가 받은 행 수를 전체로 계산해 전체 수가 틀린다. */
          pageSize={0}
          /* 상세로 들어가는 방법은 행 클릭 하나다. 행을 누르면 완료 워크벤치가 그 클립을 고른 채로 열린다.
             별도 버튼을 두면 행을 눌렀을 때 어디로 가는지 헷갈린다.
             상대 경로는 없는 라우트로 풀려 오류 화면이 뜨므로 절대 경로를 쓴다. */
          onRowClick={(r) => navigate(`/p/${slug}/completed?item=${r.work_item_id}`)}
          empty={<EmptyState {...emptyOf(filter, summary.data?.total)} />}
          footer={
            <>
              {q.hasNextPage && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void q.fetchNextPage()}
                  disabled={q.isFetchingNextPage}
                >
                  {q.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                </Button>
              )}
              {/* 받은 수와 전체 수를 함께 보여 줘 목록이 끊긴 사실을 드러낸다.
                  축을 고르면 전체 수도 그 축의 수라서 축 이름을 붙인다. 붙이지 않으면 거른 목록의 수를 완료 전체 수로 읽는다.
                  클립 수라서 단위는 `개`다. */}
              <span className="tabular-nums">
                {num(rows.length)}개 표시 · {completedFilterLabel(filter)} {num(total)}개
              </span>
            </>
          }
        />
      </Card>
    </div>
  );
}
