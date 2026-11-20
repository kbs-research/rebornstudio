/**
 * 내보내기 패널. 완료 클립을 납품 패키지 zip으로 묶고 내려받는다.
 *
 * 용어는 동작과 이력은 내보내기, 산출물은 납품 패키지, 행 판정은 납품 대상·불가로 나눠 쓴다.
 *
 * 내보내기는 만든 순간의 완료 클립 전체를 담는 스냅숏이라 그 뒤 승인이 늘면 완료 수와 달라진다.
 * 두 수가 다르면 시점 차이라는 사실을 알린다. 알리지 않으면 숫자가 틀린 것으로 읽힌다.
 * 이력에 남은 검수용 샘플·선택 항목 제외 패키지는 칩으로 구별하고, 건수 비교는 마지막 전체 내보내기와만 한다.
 *
 * 행마다 요청 후 경과 시간과 재시도 횟수(k/n)를 그린다. 기다림이 `STALE_MS`를 넘으면 `처리 지연`을 알리고 버튼을 다시 연다.
 * 워커가 도중에 멈춘 건은 서버의 `export_service.reclaim_stale`이 회수한다.
 * 버튼을 잠근 채 두면 워커가 내려가 있을 때 관리자가 새로 만들 수도, 이유를 알 수도 없다.
 *
 * 실패 사유는 `error`에 코드로 온다. 화면은 `errorText` 사전으로 굵은 결과와 작은 안내를 그린다.
 * 완료된 행의 제외 수는 `excluded_count`로 오고, 사유는 zip 대신 완료 목록으로 안내한다.
 *
 * 다운로드 링크는 누를 때 받는다. presigned URL은 유효 기간이 있어 미리 받으면 화면의 링크가 알림 없이 만료된다.
 * 받은 링크의 내용은 페이지가 받아 저장하고(`downloadUrl`), 받지 못하면 같은 창에서 연다.
 * 서버 왕복 뒤의 `window.open`은 팝업 차단에 걸릴 수 있다.
 * 파일 이름은 서버가 정한 행의 `filename`이다(`slug-YYYYMMDD-HHMM[-sampleN].zip`).
 *
 * 내보내기는 프로젝트 관리자만 만든다(서버 가드 `project_admin`). 다른 역할에는 패널을 그리지 않는다(`DonePanel`이 판단).
 * 눌러도 403을 받는 버튼은 검수자에게 아무것도 안내하지 못한다.
 *
 * 패키지는 고객에게 나가고 되돌릴 수 없으므로 판단 근거(실릴 수·빠질 수·규칙만 검사 수)를 `내보내기 추가` 창에 둔다.
 * 수는 모두 서버가 센 전체 수다. 이력은 최근 한 건만 펼쳐 둔다.
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Download, Plus, RefreshCw } from "lucide-react";

import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { DataTable, type DataColumn } from "@/shared/ui/DataTable";
import { EmptyState } from "@/shared/ui/EmptyState";
import { Modal } from "@/shared/ui/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { type ApiError } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { downloadUrl } from "@/shared/lib/download";
import { errorText, type ErrorText } from "@/shared/api/error_text";
import type { CompletedFilter } from "../lib/completed_filter";
import {
  createExport, type ExportDownloadResponse as DownloadResponse, type ExportDTO, fetchExportDownload, fetchExports, pipelineKeys,
} from "../api";

/* 계약 타입을 그대로 쓴다. 내보내기 행과 다운로드 응답 타입은 호출과 함께 `api.ts`에 있다.
   손으로 적은 DTO는 서버가 필드를 더해도 반영되지 않아 `sample_size` 같은 새 필드가 화면에 오지 않는다. */
type ExportStatus = ExportDTO["status"];

const STATUS_LABEL: Record<ExportStatus, string> = {
  pending: "대기 중",
  running: "생성 중",
  completed: "완료",
  failed: "실패",
};

/**
 * 이 시간이 지나도 끝나지 않으면 처리가 멈춘 것으로 본다.
 *
 * 대기 상태는 beat가 30초 주기라 정상이면 1분 안에 워커가 가져간다.
 * 그래서 5분이 지나도 대기 중이면 가져갈 워커가 없다고 본다.
 * 다만 서버가 승인 검사를 기다리느라 일부러 미루는 동안(`qc_wait_until`, 최대 10분)은 멈춤으로 세지 않는다.
 * 생성 중 상태는 서버가 30분에 끊고 회수하므로(`run_pending_exports` 시간 제한) 그보다 넉넉히 둔다.
 * 서버가 시작 시각을 따로 남기지 않아서 기준은 요청 시각이다.
 */
const STALE_MS: Record<"pending" | "running", number> = {
  pending: 5 * 60_000,
  running: 40 * 60_000,
};

/**
 * 이력에서 처음 펼치는 건수. 나머지는 펼치기로 보고 전체 수를 함께 보여 준다.
 * 여러 줄을 펼쳐 두면 완료 목록이 첫 화면에서 한 행만 보인다.
 */
const HISTORY_SHOWN = 1;

/**
 * 이력의 시각을 쓴다. `9월 23일 17:06`처럼 쓰고 올해가 아니면 연도를 붙인다.
 * 같은 영역 안에서 시각 형식이 섞이지 않게 한다.
 */
function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const thisYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString("ko-KR", {
    ...(thisYear ? {} : { year: "numeric" as const }),
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** 천 단위 구분 기호를 넣는다. 이 카드의 수는 모두 이 함수로 그려서 표기를 맞춘다. */
const fmt = (n: number) => n.toLocaleString("ko-KR");

/** 끝난 상태(완료, 실패)면 true. 끝나지 않은 행이 하나라도 있으면 폴링한다. */
const isSettled = (s: ExportStatus) => s === "completed" || s === "failed";

/** 요청 후 경과 시간(ms)을 구한다. 읽을 수 없는 시각은 0으로 두어 멈췄다고 잘못 알리지 않는다. */
const ageOf = (e: ExportDTO, now: number) => {
  const t = Date.parse(e.created_at);
  return Number.isNaN(t) ? 0 : Math.max(0, now - t);
};

/**
 * 기준 시간을 넘도록 끝나지 않았으면 true. 워커가 가져가지 않았거나 만들다 멈춘 경우다.
 * 서버가 품질검수 대기로 시작을 미루는 행(`qc_wait_until`)은 멈춘 것으로 보지 않는다. 미루기가 끝나면 서버가 값을 비운다.
 */
const isStale = (e: ExportDTO, now: number) =>
  (e.status === "pending" || e.status === "running") && !e.qc_wait_until && ageOf(e, now) > STALE_MS[e.status];

/** 시각을 `21:35`처럼 쓴다. 품질검수 대기가 끝나는 시각에 쓴다. */
const fmtClock = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
};

/** 경과 시간을 분 단위 문구로 만든다. 초 단위는 폴링 간격보다 잘게 바뀌어 읽기 어렵다. */
function elapsedLabel(ms: number): string {
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "1분 미만";
  if (min < 60) return `${min}분`;
  return `${Math.floor(min / 60)}시간 ${min % 60}분`;
}

/**
 * 이력 행의 사유를 두 줄로 만든다. 굵은 결과와 작은 안내로 나눈다.
 *
 * 재시도 대기 중이면 안내를 실제 시도 횟수로 바꾼다. 사전의 고정 문구에는 시도 차수가 없다.
 * 품질검수 대기로 미뤄진 행은 그 사유와 늦어도 시작하는 시각을 쓴다.
 */
function reasonOf(e: ExportDTO): ErrorText | null {
  // 승인 검사를 기다리느라 서버가 시작을 미루는 경우. 사유를 적지 않으면 이유 없이 `대기 중`만 길어진다.
  if (e.status === "pending" && e.qc_wait_until) {
    const until = fmtClock(e.qc_wait_until);
    return {
      message: "품질검수 대기",
      hint: until ? `늦어도 ${until}에 시작합니다.` : "승인 검사가 끝나면 시작합니다.",
    };
  }
  if (!e.error || e.status === "completed") return null;
  const t = errorText(e.error, { message: "생성 실패", hint: "다시 생성해주세요." });
  if (e.status === "pending" && e.attempts && e.max_attempts) {
    return { message: t.message, hint: `자동으로 다시 시도합니다 (${e.attempts}/${e.max_attempts})` };
  }
  return t;
}

/** 내보내기 카드를 그린다. 만들기 전 요약, 종류 선택, 진행 알림, 이력과 다운로드를 둔다. */
export function ExportPanel({
  projectId,
  completedTotal,
  deliverableTotal,
  qcUnreviewed,
  qcEnabled,
}: {
  projectId: string;
  /**
   * 지금 완료된 전체 수. 내보내기 시점의 수와 다를 수 있다.
   * 불러오는 중이면 `null`이다. 0으로 주면 그동안 `완료 클립 없음`으로 버튼이 잠겼다가 풀린다.
   */
  completedTotal: number | null;
  /**
   * 지금 내보내면 실제로 실릴 전체 수. 관문(`check_row`)을 통과한 수를 서버가 센다.
   *
   * `null`은 서버가 세지 않았다는 뜻이고 0과 구분한다. 전체 수가 집계 상한을 넘으면 서버가 세지 않는다.
   * 그때 요약 줄은 두 수를 비우고 완료 수만 보여 준다.
   * 0으로 그리면 하나도 내보낼 수 없다는 뜻이 된다.
   */
  deliverableTotal: number | null;
  /** 모델 검사를 받지 못하고 통과한 건수. 받은 페이지의 수가 아니라 전체 수다. */
  qcUnreviewed: number;
  /** 캡션 품질검수를 켰으면 true. 껐으면 모든 행이 같은 값이라 경고를 그리지 않는다. */
  qcEnabled: boolean;
}) {
  const { slug } = useParams<{ slug: string }>();
  /* 이 카드 아래 완료 목록의 축으로 보낸다. 축 값은 사전(`completed_filter.ts`)의 `CompletedFilter`를 쓴다.
     문자열을 이 파일에 또 적으면 한쪽만 고쳤을 때 링크가 알림 없이 `전체`로 열린다. */
  const doneHref = (f: CompletedFilter) => `/p/${slug}/pipeline?stage=done&filter=${f}`;
  const qc = useQueryClient();
  const [note, setNote] = useState<ErrorText | null>(null);
  /* 추가 창이 열려 있으면 true. */
  const [adding, setAdding] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const list = useQuery({
    queryKey: pipelineKeys.exports(projectId),
    queryFn: () => fetchExports(projectId),
    // 끝나지 않은 행이 있는 동안만 다시 조회한다. 기다릴 만한 행이 있으면 5초, 멈춘 행만 남았으면 60초 간격이다.
    // 멈춘 행이 회수되면 다음 조회에서 버튼이 풀린다.
    refetchInterval: (q) => {
      const open = (q.state.data ?? []).filter((e) => !isSettled(e.status));
      if (!open.length) return false;
      const t = Date.now();
      return open.some((e) => !isStale(e, t)) ? 5_000 : 60_000;
    },
  });

  const rows = list.data ?? [];
  const openCount = rows.filter((e) => !isSettled(e.status)).length;

  // 폴링이 같은 값을 받으면 다시 그려지지 않아 경과 시간이 멈춰 보인다.
  // 그래서 끝나지 않은 행이 있는 동안 30초마다 현재 시각을 갱신한다.
  useEffect(() => {
    if (!openCount) return;
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, [openCount]);

  const create = useMutation<ExportDTO, ApiError>({
    mutationFn: () => createExport(projectId),
    onSuccess: () => {
      setNote(null);
      setAdding(false);
      void qc.invalidateQueries({ queryKey: pipelineKeys.exports(projectId) });
    },
    // `api()`는 Error 인스턴스 대신 `{ status, detail }`을 던진다.
    // `instanceof`로 확인하면 항상 `false`라 사유와 관계없이 같은 문구가 표시된다.
    onError: (e) => {
      setAdding(false);
      setNote(errorText(e.detail, { message: "내보내기 추가 실패" }));
    },
  });

  // 링크 발급과 파일 받기를 한 동작으로 둔다. 그래서 버튼은 파일을 다 받을 때까지 비활성이다.
  const download = useMutation<void, ApiError, ExportDTO>({
    mutationFn: async (e: ExportDTO) => {
      const d: DownloadResponse = await fetchExportDownload(e.export_id);
      await downloadUrl(d.download_url, e.filename ?? "package.zip");
    },
    onSuccess: () => setNote(null),
    onError: (e) => setNote(errorText(e.detail, { message: "다운로드 링크 발급 실패" })),
  });

  /** 끝나지 않았지만 아직 기다릴 만한 행이 있으면 true. 있으면 새로 만들지 않는다. */
  const busy = rows.some((e) => !isSettled(e.status) && !isStale(e, now));
  const stalled = rows.filter((e) => isStale(e, now));
  const noneDone = completedTotal === 0;
  /* 실릴 수와 빠질 수는 둘 다 있을 때만 그린다.
     빠질 수는 서버가 따로 주지 않아서 두 수를 빼서 구한다. 조회 시각이 다른 값과 섞어 빼면 음수가 나올 수 있다.
     한쪽이 `null`이면(집계 상한 초과) 둘 다 그리지 않고 그 사실을 알린다. 0으로 그리면 내보낼 클립이 없다는 뜻으로 읽힌다. */
  const willShip =
    completedTotal != null && deliverableTotal != null
      ? { deliverable: deliverableTotal, blocked: completedTotal - deliverableTotal }
      : null;
  const canOpen = completedTotal != null && !noneDone && !busy && !create.isPending;
  const canCreate = canOpen;
  // 스냅숏 안내는 마지막으로 완료된 전체 내보내기를 기준으로 한다.
  // 샘플은 원래 건수가 다르고, 끝나지 않은 내보내기에는 아직 건수가 없다.
  // `rows[0]`을 기준으로 하면 새로 만드는 동안 안내가 사라진다.
  const lastFull = rows.find((e) => e.status === "completed" && e.sample_size == null);
  /* 지금의 납품 불가 목록으로 가는 링크는 가장 최근에 완료된 행에만 단다.
     그 목록은 지금 상태라서 지난 패키지에서 빠진 이유와 다를 수 있다. */
  const newestDone = rows.find((e) => e.status === "completed");
  const shown = showAll ? rows : rows.slice(0, HISTORY_SHOWN);

  const openAdd = () => setAdding(true);

  /* 이력 표의 열. 상태 · 만든 시각 · 실린 클립(빠진 수) · 만든 사람 · 파일 · 다운로드 순서다. */
  const historyColumns: DataColumn<ExportDTO>[] = [
    {
      label: "상태",
      sortable: false,
      width: "9rem",
      cell: (e) => {
        const reason = reasonOf(e);
        const retries = (e.attempts ?? 0) > 1 && e.max_attempts;
        return (
          <span className="flex flex-col items-start gap-0.5 whitespace-normal">
            <StatusBadge status={e.status === "completed" ? "done" : e.status === "failed" ? "failed" : "pending"} label={STATUS_LABEL[e.status]} />
            {!isSettled(e.status) && (
              <span className="text-caption tabular-nums text-label-3">
                {elapsedLabel(ageOf(e, now))} 경과{retries ? ` · 재시도 ${e.attempts}/${e.max_attempts}` : ""}
              </span>
            )}
            {/* 실패와 재시도는 이유를 같은 행에 둔다. 이유 없이 '실패'만 보이면 사용자는 다시 눌러 보는 것밖에 할 수 없다. */}
            {reason && (
              <span className="text-caption">
                <span className={e.status === "failed" ? "text-bad-text" : "text-label-2"}>{reason.message}</span>
                {reason.hint && <span className="block text-label-3">{reason.hint}</span>}
              </span>
            )}
          </span>
        );
      },
    },
    {
      label: "만든 시각",
      sortable: false,
      width: "10rem",
      cell: (e) => (
        <span className="tabular-nums" title={new Date(e.created_at).toLocaleString("ko-KR")}>{fmtWhen(e.created_at)}</span>
      ),
    },
    {
      label: "실린 클립",
      sortable: false,
      cell: (e) =>
        e.status === "completed" ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 tabular-nums">
            {/* 샘플 패키지를 전량본으로 오해하면 납품 판단이 틀리므로 칩을 단다. */}
            {e.sample_size != null && <Chip>샘플 {fmt(e.sample_size)}개</Chip>}
            {e.omit_optional && <Chip>선택 항목 제외</Chip>}
            <span className="text-label">{fmt(e.item_count)}개</span>
            {/* 그 패키지에서 빠진 수. 납품 불가(관문 제외)와 품질검수 보류를 나눠 쓰고, 둘의 합이 리포트의 제외 수다.
                보류 수가 없는 이전 형식의 행은 납품 불가 수에 보류분이 섞여 있다.
                지난 패키지의 사유는 zip의 리포트에 있으므로 지금의 목록은 가장 최근 행에만 연결한다. */}
            {!!e.excluded_count && (
              e.export_id === newestDone?.export_id ? (
                <Link to={doneHref("excluded")} className="text-caption text-label-3 underline-offset-2 hover:underline">
                  납품 불가 {fmt(e.excluded_count)}개 빠짐
                </Link>
              ) : (
                <span className="text-caption text-label-3">납품 불가 {fmt(e.excluded_count)}개 빠짐</span>
              )
            )}
            {!!e.qc_held_count && (
              <span className="text-caption text-label-3">품질검수 보류 {fmt(e.qc_held_count)}개 빠짐</span>
            )}
          </span>
        ) : (
          <span className="text-label-3">—</span>
        ),
    },
    {
      label: "만든 사람",
      sortable: false,
      shrink: true,
      // 이름이 없으면(계정 삭제) 비운다. 임의의 대체 이름을 쓰지 않는다.
      cell: (e) => <span className="block truncate text-label-2">{e.created_by_name ?? ""}</span>,
    },
    {
      label: "파일",
      sortable: false,
      shrink: true,
      cell: (e) =>
        e.status === "completed" && e.filename ? (
          <span className="block truncate font-mono text-caption text-label-2" title={e.filename}>{e.filename}</span>
        ) : null,
    },
    {
      label: "",
      sortable: false,
      align: "right",
      width: "7.5rem",
      cell: (e) =>
        e.status === "completed" ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => download.mutate(e)}
            disabled={download.isPending}
            aria-label={`${fmtWhen(e.created_at)} 내보내기 다운로드`}
          >
            <Download size={14} aria-hidden="true" /> 다운로드
          </Button>
        ) : null,
    },
  ];

  return (
    <Card>
      <div className="flex items-center gap-3 border-b border-separator px-5 py-3.5">
        <h3 className="text-headline text-label">내보내기</h3>
        <Button className="ml-auto" onClick={openAdd} disabled={!canOpen}>
          {busy ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
          {busy ? "추가 중…" : "내보내기 추가"}
        </Button>
      </div>

      {/*
        관리자가 이 카드에서 확인하는 것은 지금 내보내도 되는지다. 그래서 큰 수 하나(지금 내보내면 실리는 클립 / 완료)를 둔다.
        납품 불가·규칙만 검사는 옆 항목의 수로 두고, 누르면 아래 완료 표의 같은 축이 열린다.
        서버가 세지 않았으면(집계 상한 초과) 0으로 그리지 않고 값이 없다고 쓴다.
      */}
      <div className={cn("grid border-b border-separator", qcEnabled ? "md:grid-cols-[2fr_1fr_1fr]" : "md:grid-cols-[2fr_1fr]")}>
        <div className="flex flex-col gap-1 px-5 py-4">
          <span className="text-footnote text-label-3">지금 내보내면 실리는 클립</span>
          {willShip ? (
            <span className="flex items-baseline gap-1.5">
              <span className="text-display tabular-nums text-label">{fmt(willShip.deliverable)}</span>
              <span className="text-footnote text-label-3">/ 완료 {fmt(completedTotal ?? 0)}개</span>
            </span>
          ) : (
            <span className="text-footnote text-label-2">
              {completedTotal == null ? "—" : noneDone ? "완료 클립 없음" : `완료 ${fmt(completedTotal)}개 · 집계 상한을 넘어 세지 않음`}
            </span>
          )}
        </div>
        <SummaryLink
          to={doneHref("excluded")}
          dot="bg-bad"
          label="납품 불가"
          n={willShip ? willShip.blocked : null}
        />
        {qcEnabled && <SummaryLink to={doneHref("qc_rules")} dot="bg-warn" label="규칙만 검사" n={qcUnreviewed} />}
      </div>

      {/* 멈춘 내보내기가 있으면 결과 한 줄과 안내 한 줄로 알린다. `busy`가 멈춘 행을 세지 않으므로 버튼은 다시 열려 있다. */}
      {stalled.length > 0 && (
        <div role="status" className="border-b border-separator bg-warn-muted px-5 py-2.5">
          <p className="text-footnote font-semibold text-warn-text">
            처리 지연 · {elapsedLabel(ageOf(stalled[0], now))} 경과
          </p>
          <p className="text-footnote text-label">처리 서버가 멈췄을 수 있습니다. 계속되면 최고관리자에게 문의해주세요.</p>
        </div>
      )}
      {note && (
        <div role="alert" className="border-b border-separator px-5 py-2.5">
          <p className="text-footnote text-bad-text">{note.message}</p>
          {note.hint && <p className="mt-0.5 text-footnote text-label-2">{note.hint}</p>}
        </div>
      )}

      {rows.length === 0 ? (
        list.isLoading ? null : <EmptyState dense message="내보내기 없음" />
      ) : (
        <DataTable<ExportDTO>
          bare
          columns={historyColumns}
          rows={shown}
          rowKey={(e) => e.export_id}
          pageSize={0}
          density="compact"
          /* 이력을 알림 없이 자르지 않는다. 전체 수와 나머지를 펼치는 버튼을 둔다. 기록이라 `건`으로 센다. */
          footer={
            rows.length > HISTORY_SHOWN ? (
              <span className="flex w-full items-center gap-2 tabular-nums">
                내보내기 이력 {fmt(rows.length)}건
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  aria-expanded={showAll}
                  onClick={() => setShowAll((v) => !v)}
                >
                  {showAll ? "접기" : "모두 보기"}
                </Button>
              </span>
            ) : undefined
          }
        />
      )}

      {/* 내보내기가 만든 시점의 기록이라는 사실은 두 수가 다를 때만 알린다. 제외가 있으면 차이의 원인이 시점 차이가 아니므로 알리지 않는다. */}
      {lastFull && !lastFull.excluded_count && !lastFull.qc_held_count && completedTotal != null && lastFull.item_count !== completedTotal && (
        <p className="border-t border-separator px-5 py-2.5 text-caption text-label-3">
          마지막 전체 내보내기에는{" "}
          <span className="tabular-nums text-label-2">{lastFull.item_count.toLocaleString("ko-KR")}</span>개가
          실렸고, 지금 완료된 것은{" "}
          <span className="tabular-nums text-label-2">{completedTotal.toLocaleString("ko-KR")}</span>개입니다.
          내보내기는 <span className="text-label-2">만든 시점의 기록</span>입니다.
        </p>
      )}

      {/*
        내보내기 추가 창. 누르면 고객에게 나가는 패키지가 만들어지므로 판단 근거를 같은 창에 둔다.
        판단 근거는 라벨-값 표로 두고, 빠지는 항목과 그 이유, 함께 실리는 항목을 라벨에 쓴다.
      */}
      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title="내보내기 추가"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAdding(false)}>취소</Button>
            <Button onClick={() => create.mutate()} disabled={!canCreate}>
              {create.isPending ? "추가 중…" : "추가"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-callout text-label">
            {willShip ? (
              <><b>납품 대상 클립 {fmt(willShip.deliverable)}개</b>를 납품 패키지로 묶습니다.</>
            ) : (
              <>완료 클립을 납품 패키지로 묶습니다.</>
            )}
          </p>
          {willShip ? (
            <dl className="space-y-2 rounded-control bg-canvas-2 px-3.5 py-3 text-footnote">
              <Fact k="완료 클립" v={`${fmt(completedTotal ?? 0)}개`} />
              <Fact k="빠지는 클립 · 납품 불가" v={`${fmt(willShip.blocked)}개`} />
              {/* QC를 켜지 않은 프로젝트에서는 모든 건이 검사를 받지 않아 항상 같은 줄이 되므로 그리지 않는다. */}
              {qcEnabled && <Fact k="함께 실리는 클립 · 규칙만 검사" v={`${fmt(qcUnreviewed)}개`} />}
            </dl>
          ) : (
            /* 서버가 세지 않은 경우. 0으로 그리지 않는다. */
            <p className="text-footnote text-label-3">완료 클립이 집계 상한을 넘어 실릴 수를 미리 세지 않았습니다.</p>
          )}
          <p className="text-footnote text-label-3">
            담는 것: 메타데이터·색인·규격 정의와 리포트. 미디어 파일은 담지 않습니다.
          </p>
        </div>
      </Modal>
    </Card>
  );
}

/** 요약 항목 하나를 그린다. 누르면 완료 표의 같은 축이 열린다. 수가 없으면 `—`다. */
function SummaryLink({ to, dot, label, n }: { to: string; dot: string; label: string; n: number | null }) {
  return (
    <Link
      to={to}
      aria-label={`${label} ${n == null ? "모름" : `${fmt(n)}개`} 보기`}
      className="flex flex-col gap-1 border-t border-separator px-5 py-4 transition-colors hover:bg-canvas-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus md:border-l md:border-t-0"
    >
      <span className="flex items-center gap-2 text-footnote font-semibold text-label">
        <span className={cn("size-2 rounded-capsule", dot)} aria-hidden="true" />
        {label}
        <ChevronRight size={14} className="ml-auto text-label-3" aria-hidden="true" />
      </span>
      <span className="flex items-baseline gap-1">
        <span className="text-title-1 tabular-nums text-label">{n == null ? "—" : fmt(n)}</span>
        {n != null && <span className="text-caption text-label-3">개</span>}
      </span>
    </Link>
  );
}

/** 판단 근거 표의 한 줄을 그린다. */
function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-label-3">{k}</dt>
      <dd className="tabular-nums text-label">{v}</dd>
    </div>
  );
}
