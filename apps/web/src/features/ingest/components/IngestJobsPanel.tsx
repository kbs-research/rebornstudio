/**
 * 등록 Job의 진행 보드.
 *
 * Job은 영상 준비 과정 전체를 보는 단위다. 각 행은 준비 분포 막대와 숫자를 함께 싣는다.
 * 막대는 완료를 왼쪽부터 채우고, 진행을 그 뒤에, 이슈를 오른쪽 끝에 경고색으로 그린다.
 * 막대만으로는 이슈 1건이 보이지 않으므로 숫자를 함께 쓴다.
 *
 * 용어: 코드는 묶음을 Job, 막힘을 이슈, 묶음 보기를 상세라고 부른다.
 * 화면 문구는 Job을 `등록 묶음`, 이슈를 `문제`라고 쓴다.
 *
 * 정렬: 진행 중인 Job(이슈 포함)을 먼저 두고, 완료된 Job은 접힌 그룹에 둔다.
 * 마지막 전이 최신순 정렬에는 단계 실행 기록(stage_runs)이 필요해서 생성 최신순으로 근사한다.
 * 분포와 판정은 서버가 한다(DTO.prep, 같은 SQL CASE). 이 패널은 표시와 클릭만 맡는다.
 */
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check, ChevronDown, ChevronRight, FolderInput, Loader2, Pencil, UploadCloud, X,
} from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { FilterMenu, type FilterOption } from "@/shared/ui/FilterMenu";
import { useToast } from "@/shared/ui/Toast";
import { type ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { errorText } from "@/shared/api/error_text";
import { relativeTime } from "@/shared/lib/time";
import { SegmentBar, type Segment } from "./SegmentBar";
import { GROUP_LABEL, SOURCE_LABEL, SOURCE_REGISTER_LABEL, TONE_FILL, fmtElapsed, type PrepChipKey } from "../lib/vocab";
import { useSelectedProject } from "@/store/project";
import { fetchIngestBatches, ingestKeys, renameIngestBatch, type IngestBatchDTO } from "../api";

/** Job 이름을 돌려준다. 이름이 없으면 등록 시각과 출처로 만든다. 그래서 이름 입력은 선택 사항이다. */
function jobName(b: IngestBatchDTO): string {
  if (b.label) return b.label;
  const d = new Date(b.created_at);
  const when = `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${when} ${SOURCE_REGISTER_LABEL[b.source] ?? "등록"}`;
}

/**
 * Job 진행을 준비 분포 막대와 숫자 요약으로 그린다.
 *
 * 막대는 `SegmentBar`가 그린다. 최소 폭 규칙이 그 파일에 있다.
 * 이 함수는 어떤 조각을 어떤 색으로 묶을지만 정한다.
 * 진행 중인 4그룹은 막대에서 accent 계열 색(`bg-accent`, `bg-accent/70`)만 쓴다.
 * 색을 5종 쓰면 구분이 어렵고, 세부 단계는 숫자 요약과 상세 칩이 보여 준다.
 *
 * 막대는 장식이고, 단계로 바로 가는 링크는 아래 수 줄의 항목이다.
 * 막대 조각은 높이 5px라 버튼으로 쓰면 누르기 어렵다(WCAG 2.5.8).
 */
function BatchProgress({ b, onOpen }: { b: IngestBatchDTO; onOpen: (prep?: PrepChipKey) => void }) {
  const pc = b.prep;
  if (!pc || !pc.total) {
    // 영상이 모두 삭제된 Job과 처음부터 빈 Job은 다르게 표시한다.
    // 둘 다 '없음'으로 쓰면 삭제했다는 사실이 화면에서 빠진다.
    return (
      <span className="text-caption text-label-3">
        {b.deleted_videos > 0 ? `영상 ${b.deleted_videos.toLocaleString("ko-KR")}개 삭제` : "영상 없음"}
      </span>
    );
  }
  // 순서와 문구는 Job 상세 헤더와 같다. 같은 대상을 두 화면에서 같은 이름으로 부른다.
  const segments: Segment[] = [
    { key: "done", label: "완료", n: pc.ready_for_work, fill: TONE_FILL.ok },
    { key: "analyze", label: GROUP_LABEL.analyze, n: pc.analyzing + pc.summarizing, fill: TONE_FILL.run },
    { key: "mi", label: GROUP_LABEL.mi, n: pc.registering, fill: TONE_FILL.run },
    { key: "awaiting", label: "요청 대기", n: pc.awaiting_analysis, fill: TONE_FILL.wait },
    { key: "transcode", label: "변환", n: pc.transcoding, fill: "bg-accent/70" },
    { key: "register", label: "등록", n: pc.uploading + pc.probing, fill: "bg-accent/70" },
    { key: "blocked", label: "문제", n: pc.blocked, fill: TONE_FILL.bad },
  ];
  return (
    <span className="flex w-full flex-col gap-1">
      <SegmentBar segments={segments} total={pc.total} />
      <span className="flex flex-wrap items-center gap-x-0.5 text-caption tabular-nums text-label-3">
        {segments.filter((x) => x.n > 0).map((x) => (
          <button
            key={x.key}
            type="button"
            onClick={() => onOpen(x.key as PrepChipKey)}
            aria-label={`${jobName(b)}의 ${x.label} 영상 ${x.n.toLocaleString("ko-KR")}개 보기`}
            className="rounded-control px-1 py-1 leading-none transition-colors hover:bg-fill hover:text-label"
          >
            {x.label} {x.n.toLocaleString("ko-KR")}
          </button>
        ))}
      </span>
    </span>
  );
}

/**
 * Job 이름 셀.
 * 이름을 셀 안에서 바로 고친다. 이름 수정을 별도 화면에 두면 사용자가 이름을 거의 달지 않는다.
 */
function NameCell({ b }: { b: IngestBatchDTO }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(b.label ?? "");

  const save = useMutation({
    mutationFn: () => renameIngestBatch(b.batch_id, draft.trim()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
      setEditing(false);
      toast("success", "묶음 이름을 저장했습니다.");
    },
    // 원인이 확인되지 않으면 권한 문제로 단정하지 않는다.
    // 오류 사전(`errorText`)에 있는 원인만 표시한다.
    onError: (e) => {
      const t = errorText((e as unknown as ApiError | null)?.detail, { message: "묶음 이름 저장 실패" });
      toast("error", t.message, t.hint);
    },
  });

  if (editing) {
    return (
      <span className="flex items-center gap-1.5">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save.mutate();
            if (e.key === "Escape") setEditing(false);
          }}
          placeholder={jobName(b)}
          aria-label="묶음 이름"
          className="w-56 rounded-field border border-separator bg-canvas px-2 py-1 text-footnote text-label outline-none focus:border-focus"
        />
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}
          aria-label="저장" className="rounded-control p-1 text-ok-text hover:bg-fill">
          <Check size={14} />
        </button>
        <button type="button" onClick={() => setEditing(false)}
          aria-label="취소" className="rounded-control p-1 text-label-4 hover:bg-fill">
          <X size={14} />
        </button>
      </span>
    );
  }
  return (
    <span className="group/name flex min-w-0 items-center gap-1.5">
      <span className={cn("truncate font-medium", b.label ? "text-label" : "text-label-2")}>
        {jobName(b)}
      </span>
      {/* 누르는 영역은 24px 이상이다. 키보드 포커스를 받으면 보이게 한다. */}
      <button
        type="button"
        onClick={() => { setDraft(b.label ?? ""); setEditing(true); }}
        aria-label={`${jobName(b)} 이름 수정`}
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-control text-label-3 opacity-0 transition-opacity hover:bg-fill hover:text-label-2 focus-visible:opacity-100 group-hover/name:opacity-100"
      >
        <Pencil size={14} aria-hidden="true" />
      </button>
    </span>
  );
}

/**
 * 묶음 상태 열. 지금 볼 상태 하나를 점과 글자로 쓴다.
 * 우선순위는 등록 중, 영상 없음, 문제, 분석 중, 요청 대기, 준비 완료 순이다.
 * 점 색은 준비 분포 막대의 같은 상태와 같다.
 */
function BatchState({ b }: { b: IngestBatchDTO }) {
  if (b.import_status === "pending" || b.import_status === "running")
    return <Dot fill="bg-accent" label="등록 중" />;
  if (b.video_count === 0)
    // 남은 영상이 없으면 '완료'로 쓰지 않는다. 영상이 삭제됐거나 처음부터 비어 있는 경우다.
    return <span className="text-caption text-label-3">영상 없음</span>;
  const pc = b.prep;
  return pc && pc.blocked > 0 ? <Dot fill={TONE_FILL.bad} label={`문제 ${pc.blocked.toLocaleString("ko-KR")}개`} />
    : b.analyzing > 0 ? <Dot fill={TONE_FILL.run} label={`분석 중 ${b.analyzing.toLocaleString("ko-KR")}개`} />
    : b.ready > 0 ? <Dot fill={TONE_FILL.wait} label={`요청 대기 ${b.ready.toLocaleString("ko-KR")}개`} />
    : pc && pc.ready_for_work === pc.total ? <Dot fill={TONE_FILL.ok} label="준비 완료" />
    : <Dot fill="bg-accent" label="진행 중" />;
}

/** 상태 점과 글자. */
function Dot({ fill, label }: { fill: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-footnote font-medium tabular-nums text-label">
      <span className={cn("size-2 shrink-0 rounded-capsule", fill)} aria-hidden="true" />
      {label}
    </span>
  );
}

/** 출처 필터 옵션. */
const SOURCE_OPTIONS: FilterOption[] = [
  { value: "upload", label: "파일 업로드" },
  { value: "ncp", label: SOURCE_LABEL.ncp },
];
/*
  상태 필터 옵션. 점 색은 감사 로그 심각도와 같은 규칙을 따른다.
  빨강은 조치할 일, 초록은 끝난 일이다.
  힌트는 같은 형태의 명사구로 쓴다.
  힌트에 서버 상수(`_MAX_ERRORS`의 재시도 횟수)를 적지 않는다. 상수가 바뀌면 문구가 틀리게 된다.
*/
/*
  이름을 되풀이하는 힌트는 쓰지 않는다. 새 정보가 있는 힌트만 둔다.
*/
const STATE_OPTIONS: FilterOption[] = [
  { value: "issue", label: "문제 있음", hint: "문제로 멈춘 영상이 있는 묶음", dot: "bg-bad" },
  { value: "running", label: "진행 중", dot: "bg-accent" },
  { value: "done", label: "준비 완료", hint: "모든 영상의 준비가 끝난 묶음", dot: "bg-ok" },
  { value: "importing", label: "등록 중", hint: "저장소 폴더를 등록하는 중인 묶음", dot: "bg-accent" },
  { value: "import_failed", label: "등록 실패", hint: "폴더 등록이 중단된 묶음", dot: "bg-bad" },
];

/** 한 번에 받는 Job 수. 더 있으면 `더 보기`가 커서로 다음 페이지를 받는다. */
const JOB_LIMIT = 50;

/**
 * 등록 Job 목록을 보여 준다.
 *
 * `onOpen`은 Job 상세(`?batch=`)를 연다. 고른 칩이 있으면 함께 넘긴다.
 * 영상 수와 관계없이 늘 Job 상세로 간다.
 */
export function IngestJobsPanel({
  onOpen, projectVideoTotal,
}: {
  onOpen: (b: IngestBatchDTO, prep?: PrepChipKey) => void;
  /**
   * 프로젝트의 전체 영상 수(서버 집계). 묶음 없이 등록된 영상 수를 밝히는 데 쓴다.
   * 밝히지 않으면 상태 줄의 전체 수와 목록 꼬리의 영상 수가 다르고, 차이만큼의 영상이 어디에도 보이지 않는다.
   */
  projectVideoTotal?: number;
}) {
  const { selectedProjectId: pid } = useSelectedProject();
  const { slug } = useParams<{ slug: string }>();
  const [showDone, setShowDone] = useState(false);
  const [showEmpty, setShowEmpty] = useState(false);
  /* 필터는 감사 로그와 같은 컴포넌트(FilterMenu)를 써서 같은 성격의 조작을 같은 모양으로 둔다.
     목록이 최근 50건씩 받는 커서라서 이 필터는 화면에서 불러온 Job만 거르고, 결과 수를 분모와 함께 표시한다.
     값은 주소(`?source=`·`?state=`)에 두어 새로고침·뒤로 가기·링크 공유에도 같은 필터로 열린다. */
  const [params, setParams] = useSearchParams();
  const srcFilter = params.get("source") ?? "";
  const stateFilter = params.get("state") ?? "";
  /** 필터 하나를 주소에 쓴다. 필터 변경은 이력에 쌓지 않는다. */
  const setFilter = (key: "source" | "state", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  /*
    커서로 다음 페이지를 받는다.
    서버는 `(before, before_id)` 커서를 받는다(`/projects/{id}/ingest-batches`).
    커서가 없으면 목록이 최근 50건에서 끝나 이전 Job을 열 수 없다.
  */
  const batches = useInfiniteQuery({
    queryKey: ingestKeys.batches(pid),
    queryFn: ({ pageParam }) => fetchIngestBatches(pid, JOB_LIMIT, pageParam),
    enabled: !!pid,
    /*
      403은 아래 `조회 권한 없음` 빈 상태로 화면이 직접 그린다.
      `queryClient`는 첫 로드가 실패하면 전역 `queryCache.onError`로 토스트를 띄우므로 `meta.handled`로 토스트를 막는다.
      막지 않으면 묶음을 볼 수 없는 역할이 `영상 등록` 단계를 열 때마다 권한 토스트가 뜬다.
    */
    meta: { handled: true },
    initialPageParam: undefined as { at: string; id: string } | undefined,
    getNextPageParam: (last) =>
      last.length === JOB_LIMIT
        ? { at: last[last.length - 1].created_at, id: last[last.length - 1].batch_id }
        : undefined,
    // 서버 등록 작업이 도는 Job이 있을 때만 10초마다 다시 조회한다.
    // 그래서 등록 건수가 늘어나는 과정이 화면에 보인다.
    refetchInterval: (q) =>
      (q.state.data?.pages.flat() ?? []).some(
        (b) => b.import_status === "pending" || b.import_status === "running",
      )
        ? 10_000
        : false,
  });
  /* 이 패널은 분석 단가를 조회하지 않고 목록에 금액 열을 두지 않는다.
     분석 요청 전의 금액은 길이와 단가로 계산한 추정치라서 목록에 ₩를 붙이면 확정된 값처럼 읽힌다.
     Job 상세 머리에 예상 금액을 두지 않는 이유와 같다. */

  const columns: DataColumn<IngestBatchDTO>[] = [
    {
      label: "등록 묶음",
      kind: "title",
      value: (b) => jobName(b),
      cell: (b) => (
        <span className="flex min-w-0 flex-col leading-tight">
          <NameCell b={b} />
          {/* `truncate` 요소 둘을 한 줄에 나란히 두지 않는다. 줄어들 수 있는 요소가 그 둘뿐이면 서로 공간을 빼앗아
              경로와 메모가 모두 `· …`만 남는다. 메모는 길이 제한이 없는 자유 입력이다.
              그래서 줄바꿈을 허용하고 각 요소에 `title`을 주어 잘려도 원문을 볼 수 있게 한다. */}
          <span className="flex flex-wrap items-center gap-x-1 gap-y-0.5 text-caption text-label-3">
            {b.source === "ncp" ? <FolderInput size={12} aria-hidden="true" /> : <UploadCloud size={12} aria-hidden="true" />}
            {SOURCE_LABEL[b.source] ?? b.source} · {relativeTime(b.created_at)}
            {b.import_prefix && (
              <span className="min-w-0 truncate font-mono" title={b.import_prefix}> · {b.import_prefix}</span>
            )}
            {b.note && <span className="min-w-0 truncate" title={b.note}> · {b.note}</span>}
          </span>
        </span>
      ),
    },
    {
      label: "영상",
      kind: "number",
      align: "right",
      width: "5rem",
      value: (b) => b.video_count,
      // 영상 수라서 단위는 `개`다.
      cell: (b) => `${b.video_count.toLocaleString("ko-KR")}개`,
    },
    {
      label: "총 길이",
      tone: "muted",
      kind: "number",
      align: "right",
      width: "7rem",
      value: (b) => b.total_duration_seconds,
      cell: (b) => (
        <span>
          {fmtElapsed(b.total_duration_seconds)}
        </span>
      ),
    },
    {
      label: "진행",
      sortable: false,
      width: "13rem",
      cell: (b) =>
        // 폴더째 등록이 진행 중이거나 실패했으면 분포보다 등록 상태를 먼저 보여 준다.
        // 진행 중이면 건수가 아직 늘고 있어 분포가 확정되지 않았다.
        b.import_status === "pending" || b.import_status === "running" ? (
          <span className="inline-flex items-center gap-1.5 text-caption text-accent">
            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            저장소 등록 중 · 영상 {b.video_count.toLocaleString("ko-KR")}개
          </span>
        ) : b.import_status === "failed" ? (
          /* 결과는 굵게, 사유는 작게 쓴다. 재시도 횟수는 적지 않는다.
             사유는 서버가 관리자용 문장으로 남긴 값이다. */
          <span className="flex flex-col text-caption leading-snug">
            <b className="font-medium text-bad-text">등록 실패</b>
            <span className="truncate text-label-3" title={b.import_error ?? undefined}>
              {b.import_error ?? "재시도 후 중단"}
              {b.video_count > 0 ? ` · 영상 ${b.video_count.toLocaleString("ko-KR")}개 등록` : ""}
            </span>
          </span>
        ) : (
          <BatchProgress b={b} onOpen={(prep) => onOpen(b, prep)} />
        ),
    },
    {
      label: "상태",
      sortable: false,
      width: "11rem",
      cell: (b) => <BatchState b={b} />,
    },
    {
      label: "",
      sortable: false,
      align: "right",
      width: "2.5rem",
      /* 행 전체가 묶음 상세로 가는 링크다(`onRowClick`). 끝의 꺾쇠가 링크 표시이고, 키보드 사용자는 이 버튼으로 이동한다. */
      cell: (b) => (
        <button
          type="button"
          onClick={() => onOpen(b)}
          aria-label={`${jobName(b)} 상세`}
          className="inline-flex size-7 items-center justify-center rounded-control text-label-3 transition-colors hover:bg-fill hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      ),
    },
  ];

  const all = useMemo(() => batches.data?.pages.flat() ?? [], [batches.data]);
  /*
    진행 중인 Job을 먼저 보여 준다.
    완료는 남은 영상 전부가 ready_for_work인 경우다.
    영상이 전부 이슈이거나 등록에 실패한 Job은 진행 중으로 분류한다. 완료로 분류하면 접힌 그룹에 묻힌다.
    prep이 없으면(이전 캐시 응답) 진행 중으로 본다. 잘못 펼치는 쪽이 잘못 접어 숨기는 쪽보다 안전하다.
  */
  const isDone = (b: IngestBatchDTO) =>
    b.import_status !== "failed" &&
    b.video_count > 0 && !!b.prep && b.prep.ready_for_work === b.prep.total;
  /**
   * 상태 필터가 쓰는 Job 상태 키를 정한다.
   *
   * 주의: `상태` 열은 이 함수를 쓰지 않고 셀 안에서 따로 판정한다.
   */
  const stateOf = (b: IngestBatchDTO): string => {
    if (b.import_status === "pending" || b.import_status === "running") return "importing";
    if (b.import_status === "failed") return "import_failed";
    if ((b.prep?.blocked ?? 0) > 0) return "issue";
    if (isDone(b)) return "done";
    return "running";
  };
  /** 출처·상태 필터에 맞는 Job인지 판정한다. */
  const match = (b: IngestBatchDTO) =>
    (!srcFilter || b.source === srcFilter) && (!stateFilter || stateOf(b) === stateFilter);

  /*
    남은 영상이 없는 묶음은 접는다. 업로드를 시작만 하고 끝내지 않은 묶음과 영상을 모두 지운 묶음이다.
    진행 중 목록은 지금 할 일이 있는 묶음을 보여 주는데, 이 묶음에는 할 일이 없다.
    삭제 기록은 접힌 목록의 행이 `영상 N개 삭제`로 그대로 보여 준다.
    등록이 도는 중이거나 등록에 실패한 묶음은 확인할 내용이 남아 있어 접지 않는다.
  */
  const isEmpty = (b: IngestBatchDTO) =>
    b.import_status !== "pending" && b.import_status !== "running" && b.import_status !== "failed"
    && b.video_count === 0;
  const filtered = all.filter(match);
  const rows = filtered.filter((b) => !isDone(b) && !isEmpty(b));
  const doneRows = filtered.filter(isDone);
  const emptyRows = filtered.filter(isEmpty);
  /* 묶음 없이 등록된 영상 수. 묶음을 모두 받았을 때만 센다. 일부만 받았으면 뺄셈 결과가 틀린다. */
  const batchedVideos = all.reduce((s, b) => s + b.video_count, 0);
  const unbatched =
    projectVideoTotal != null && !batches.hasNextPage && !batches.isLoading
      ? Math.max(0, projectVideoTotal - batchedVideos)
      : 0;

  return (
    <div>
      {/*
        필터는 거르는 대상인 표 상자 안 맨 위에 두고 구분선을 둔다.
        그러면 무엇을 거르는 필터인지 위치로 알 수 있고, 표 앞의 조작 줄이 셋에서 둘로 준다.
      */}
      <div className="overflow-hidden rounded-card border border-separator bg-panel shadow-e1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-separator px-4 py-3">
          <h3 className="text-headline text-label">등록 묶음</h3>
          {/* 필터를 걸면 분모와 함께 적는다. 목록이 50건씩 받는 커서라 화면에 불러온 묶음만 거른다. */}
          <span className="text-footnote tabular-nums text-label-3">
            {srcFilter || stateFilter
              ? `${filtered.length.toLocaleString("ko-KR")} / ${all.length.toLocaleString("ko-KR")}건`
              : `${all.length.toLocaleString("ko-KR")}건${batches.hasNextPage ? " 불러옴" : ""} · 영상 ${batchedVideos.toLocaleString("ko-KR")}개`}
          </span>
          <span className="ml-auto flex items-center gap-2">
            <FilterMenu label="출처" value={srcFilter} options={SOURCE_OPTIONS} onChange={(v) => setFilter("source", v)} />
            <FilterMenu label="상태" value={stateFilter} options={STATE_OPTIONS} onChange={(v) => setFilter("state", v)} />
          </span>
        </div>
        <DataTable<IngestBatchDTO>
          bare
          columns={columns}
          rows={rows}
          rowKey={(b) => b.batch_id}
          loading={batches.isLoading}
          density="compact"
          pageSize={0}
          onRowClick={(b) => onOpen(b)}
          empty={
            /*
              권한이 없는 경우를 "없습니다"로 표시하지 않는다.
              403인데 빈 상태를 그리면 묶음을 볼 수 없는 역할에게 목록이 빈 것으로 잘못 보인다.
            */
            batches.isError ? (
              // 403일 때만 권한 문제로 표시한다. 5xx·타임아웃은 일시 오류로 다룬다.
              (batches.error as { status?: number } | null)?.status === 403 ? (
                <EmptyState
                  message="조회 권한 없음"
                  description="등록 묶음은 프로젝트 관리자만 볼 수 있습니다."
                />
              ) : (
                <ErrorState
                  message="등록 묶음 조회 실패"
                  onRetry={() => void batches.refetch()}
                />
              )
            ) : doneRows.length > 0 || emptyRows.length > 0 ? (
              <EmptyState message="진행 중인 등록 묶음 없음" />
            ) : (
              <EmptyState message="등록 묶음 없음" />
            )
          }
          footer={(unbatched > 0 && slug) || batches.hasNextPage ? (
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums">
              {/* 묶음 수와 영상 수는 머리에 있다. 이 줄에는 목록 밖에 남은 것만 둔다. */}
              {/* 묶음 없이 등록된 영상은 이 목록에 나오지 않는다. 그 수를 밝히고 전체 영상 목록으로 안내한다. */}
              {unbatched > 0 && slug && (
                /* 표의 행과 구분되게 링크 모양(화살표·밑줄)을 준다. 이 줄은 목록 밖으로 나가는 링크다. */
                <Link
                  to={`/p/${slug}/content`}
                  className="inline-flex items-center gap-1 font-medium text-accent underline-offset-2 hover:underline"
                >
                  묶음 없는 영상 {unbatched.toLocaleString("ko-KR")}개 보기
                  <ChevronRight size={12} aria-hidden="true" />
                </Link>
              )}
              {batches.hasNextPage && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void batches.fetchNextPage()}
                  disabled={batches.isFetchingNextPage}
                >
                  {batches.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                </Button>
              )}
            </span>
          ) : undefined}
        />
        {/* 이 목록은 진행 중인 Job을 먼저 보여 주므로 완료된 Job은 접힌 그룹에 둔다. */}
        {doneRows.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setShowDone((o) => !o)}
              aria-expanded={showDone}
              className="flex w-full items-center gap-1.5 border-t border-separator px-4 py-2 text-caption text-label-3 transition-colors hover:bg-fill"
            >
              <ChevronDown size={12} className={cn("shrink-0 transition-transform", !showDone && "-rotate-90")} aria-hidden="true" />
              완료된 묶음 {doneRows.length.toLocaleString("ko-KR")}건
            </button>
            {showDone && (
              <DataTable<IngestBatchDTO>
                bare
                columns={columns}
                rows={doneRows}
                rowKey={(b) => b.batch_id}
                density="compact"
                pageSize={0}
                onRowClick={(b) => onOpen(b)}
              />
            )}
          </>
        )}
        {/* 빈 묶음 접힘 그룹. 올린 파일이 없거나, 업로드가 끝나지 않았거나, 영상을 모두 지운 묶음이다. */}
        {emptyRows.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setShowEmpty((o) => !o)}
              aria-expanded={showEmpty}
              className="flex w-full items-center gap-1.5 border-t border-separator px-4 py-2 text-caption text-label-3 transition-colors hover:bg-fill"
            >
              <ChevronDown size={12} className={cn("shrink-0 transition-transform", !showEmpty && "-rotate-90")} aria-hidden="true" />
              영상이 없는 묶음 {emptyRows.length.toLocaleString("ko-KR")}건
            </button>
            {showEmpty && (
              <DataTable<IngestBatchDTO>
                bare
                columns={columns}
                rows={emptyRows}
                rowKey={(b) => b.batch_id}
                density="compact"
                pageSize={0}
                onRowClick={(b) => onOpen(b)}
              />
            )}
          </>
        )}
      </div>

    </div>
  );
}
