/**
 * 영상 등록 콘솔(`/p/:slug/pipeline?stage=ingest`).
 *
 * 화면은 세 부분으로 나뉜다.
 *   상태 타일 셋(IngestStatusTiles): 요청 대기·분석 중·문제의 영상 수를 늘 보여 준다.
 *   기본 본문: 등록 Job 목록이다. 첫 화면에 보인다.
 *   `?batch=`: Job 상세다. 그 Job의 모든 상태를 섞은 목록을 칩으로 거르고, 분석 요청을 여기서 한다.
 *   문제 영상은 행마다 `문제 해결` 메뉴로 한 건씩 조치한다. 같은 조치는 영상 상세의 준비 진행에도 있다.
 *
 * 일반적인 배치 처리 콘솔처럼 Job 목록을 본문으로 두고, 여러 Job에 걸친 실패 큐는 최상위에 두지 않는다.
 *
 * 분석 현황(MAIU job 표)은 두지 않는다. MAIU job은 등록 Job과 단위가 달라 나란히 두면 비교할 수 없다.
 * 단계 분포는 Job 목록의 분포 막대가, 분석 비용은 요청 대기 타일과 분석 요청 확인 창이 보여 준다.
 *
 * 용어: 코드는 묶음을 Job, 막힘을 이슈, 묶음 보기를 상세라고 부른다.
 * 화면 문구는 Job을 `등록 묶음`, 이슈를 `문제`라고 쓴다.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createPortal } from "react-dom";
import {
  ArrowRight, ChevronDown, FileSpreadsheet, Loader2, Plus, RotateCw, Scissors, Send,
} from "lucide-react";

import { useOutsideClose } from "@/shared/hooks/useOutsideClose";
import { IngestSheet } from "./IngestSheet";
import { MediaRow, MediaRows } from "./MediaRow";
import { JobHeader } from "./JobHeader";
import { MetaImportModal } from "./MetaImportModal";
import { StageBadge } from "./StageBadge";
import { JobRail, jobRailHasCards } from "./JobRail";
import { SortControl, sortRows, type SortKey } from "./SortControl";
import { JobIssueBanner } from "./JobIssueBanner";
import { StageChips } from "./StageChips";
import {
  STAGE_TONE, stagesOf, TONE_FILL,
  type PrepChipKey, type PrepStage,
} from "../lib/vocab";
import { Chip } from "@/shared/ui/Chip";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { LastUpdated } from "@/shared/ui/LastUpdated";
import { Modal } from "@/shared/ui/Modal";
import { Skeleton } from "@/shared/ui/Skeleton";
import { useToast } from "@/shared/ui/Toast";
import { useAnalysisStack } from "@/hooks/useAnalysisStack";
import { type DeferredNote, deferredNote } from "../lib/deferred_note";
import { dispatchToast } from "../lib/dispatch_toast";
import { costKrw, fmtHourMin } from "../lib/span";
import { cn } from "@/shared/lib/cn";
import { apiErrorText } from "@/shared/api/error_text";
import { fmtTimecode } from "@/shared/lib/fmtSecs";
import { useAuth } from "@/store/auth";
import { useSelectedProject } from "@/store/project";

import { IngestJobsPanel } from "./IngestJobsPanel";
import { IngestStatusTiles, type IngestTile } from "./IngestStatusTiles";
import { HeaderActions, pipelineKeys } from "@/features/pipeline";
import type { UploadProgress } from "./UploadQueue";
import {
  dispatchAnalysis,
  fetchIngestJob,
  fetchMaiuCostStats,
  fetchMaiuJobs,
  fetchProjectVideos,
  fetchVideoBuckets,
  ingestKeys,
  resetProbe,
  retryAnalysis,
  retryTranscode,
  selectVideos,
  type VideoDTO,
} from "../api";
import { ConnectionNotice, useProjects } from "@/features/projects";
import { retryFailTitle, retryToast, SUMMARY_CONFIRM, SUMMARY_RETRY_LABEL } from "../lib/retry_text";


/** 이슈 한 건을 사용자가 읽을 문장으로 담은 값. 사유(결과)와 다음 할 일을 나눠 담는다. */
type Block = {
  /** 무엇이 문제인지. 행 노트에 표시된다. */
  reason: string;
  /**
   * 다음에 할 수 있는 일. 할 수 있는 일이 있을 때만 채운다.
   * 조치가 없는 사유 뒤에 고정 안내(`재업로드가 필요합니다`)를 붙이면 실제로 없는 다음 단계를 안내하게 된다.
   */
  nextStep?: string;
  /** 규격 재검사(`probe/reset`)로 해결되면 true. 검증에서 멈춘 경우다. */
  reprobable?: boolean;
  /** 변환 재시도(`transcode/retry`)로 해결되면 true. 분석본 변환에서 멈춘 경우다. */
  retranscodable?: boolean;
  /** 분석 재요청(`maiu/retry`)으로 해결되면 true. 분석에서 실패한 경우다. */
  reanalyzable?: boolean;
  /** 요약 재요청(`maiu/retry`)으로 해결되면 true. 클립은 있지만 장면 요약을 받지 못한 경우다. */
  resummarizable?: boolean;
};

/**
 * 영상 메타데이터(`v.metadata`).
 * 막힌 단계별 오류 기록이 들어 있고, `blockOf`가 서버가 준 단계에 맞춰 골라 쓴다.
 */
type Meta = Record<string, unknown>;

/**
 * 사유 뒤에 붙일 서버 기록을 꺼낸다.
 * `[일시]` 표식은 재시도 정책이 붙인 내부 표시라서 관리자에게 보여 주지 않는다.
 */
function noted(meta: Meta, key: string): string {
  return String(meta[key] ?? "").replace(/^\[일시\]\s*/, "").trim().slice(0, 80);
}

/**
 * 검증에서 막힌 영상의 사유를 만든다. 거부(`rejected`)와 검사 실패(`error`)는 다음 할 일이 다르다.
 *
 * 코덱 위반만 있으면 되살릴 수 있다.
 * probe를 되돌리면 서버가 passed와 변환 pending으로 다시 판정한다.
 * 그러면 h264 사본을 만든 뒤 분석까지 이어진다.
 * 크기·길이 초과가 섞여 있으면 재검사해도 결과가 같으므로 규격에 맞는 파일이 필요하다.
 */
function probeBlock(v: VideoDTO, meta: Meta): Block {
  if (v.probe_status === "rejected") {
    const viol: string[] = (meta.probe as { violations?: string[] } | undefined)?.violations ?? [];
    const codecOnly = viol.length > 0 && viol.every((x) => x.startsWith("codec_not_allowed"));
    if (codecOnly && v.transcode_status === "skipped")
      return {
        reason: "지원하지 않는 코덱입니다",
        nextStep: "검증 재검사를 하면 변환 대상에 들어갑니다.",
        reprobable: true,
      };
    return {
      reason: "규격을 벗어나 분석할 수 없습니다",
      nextStep: "규격에 맞는 파일로 다시 등록해야 합니다.",
    };
  }
  /* 원본을 읽지 못해(권한·일시적 연결 끊김·깨진 헤더) 검사를 하지 못한 경우.
     서버의 규격 재검사가 `error` 상태도 받으므로 되살릴 수 있다. */
  const err = String((meta.probe as { error?: string } | undefined)?.error ?? "").trim().slice(0, 80);
  return {
    reason: err ? `규격 검증을 하지 못했습니다 (${err})` : "규격 검증을 하지 못했습니다",
    nextStep: "검증 재검사로 다시 검사합니다.",
    reprobable: true,
  };
}

/**
 * 분석에서 막힌 영상의 사유를 만든다. 벤더가 준 사유를 그대로 쓴다.
 *
 * `분석 재요청` 조치를 주고 사유를 같은 줄 앞에 둔다. 파일 자체의 문제이면 다시 요청해도 결과가 같으므로
 * 관리자가 그 줄의 사유를 보고 판단한다. 조치는 영상 상세 타임라인과 같다.
 *
 * 사유 문장은 서버가 만든다(`error_text.analysis_error_text`). 화면은 자르거나 괄호로 감싸지 않는다.
 * 글자 수로 자르면 벤더 API 경로만 남고 원인(400)이 사라진다. 긴 사유는 두 줄에서 말줄임되고 전문은 툴팁에 보인다.
 */
function analysisBlock(jobError?: string | null): Block {
  const said = (jobError ?? "").trim();
  return {
    reason: said || "분석하지 못했습니다 (사유 미확인)",
    nextStep: "분석 재요청으로 다시 분석합니다.",
    reanalyzable: true,
  };
}

/**
 * 분석 요청 직전(`ticket`)에서 멈춘 영상의 사유를 만든다. 서버 CASE의 else 분기라서 세 경우가 섞인다.
 *
 * 사유가 확실한 경우부터 본다. 지난 요청이 남긴 사유, 티켓 없음, 사유 미상 순서다.
 * 세 경우 모두 화면에서 누를 조치가 없다.
 */
function ticketBlock(v: VideoDTO): Block {
  /* 요청은 보냈는데 서버에서 막힌 경우. 서버가 `extra.dispatch_error`에 사유를 남긴다. */
  const dispatchErr = dispatchErrOf(v);
  if (dispatchErr) return { reason: dispatchErr };
  // 분석 티켓(영상 단위 work_item)이 없으면 dispatch가 알림 없이 건너뛴다.
  // 분석 요청을 눌러도 효과가 없으므로 사유만 알린다.
  if (v.clip_count === 0 && v.pipeline_stage === "registered")
    return { reason: "분석 요청 정보가 없어 분석할 수 없습니다" };
  return { reason: "진행 조건 미충족" };
}

/**
 * 막힌 이유를 사용자가 읽을 문장으로 만든다. 분류는 서버가 하고, 이 함수는 문구만 만든다.
 *
 * 서버가 준 단계(`prep_blocked_stage`)로 분기하고, 원시 상태 값을 다시 훑지 않는다.
 * 화면이 따로 판정하면 행 사유와 레일 버튼이 서로 다른 단계를 가리킬 수 있다.
 * 단계가 없으면(막힌 행이 아니거나 이전 형식의 응답) `null`을 돌려준다.
 */
function blockOf(v: VideoDTO, jobError?: string | null): Block | null {
  const meta = (v.metadata ?? {}) as Meta;
  switch (v.prep_blocked_stage) {
    case "probe":
      return probeBlock(v, meta);
    case "transcode": {
      /*
        변환 실패. 사유는 서버가 기록한 값을 그대로 쓴다.
        변환 전용 경로(`transcode/retry`)로 되살린다.
        이미 규격을 통과한 영상이라 규격 재검사를 부르면 409 응답을 받는다.
      */
      const err = noted(meta, "transcode_error");
      return {
        reason: err ? `변환하지 못했습니다 (${err})` : "변환하지 못했습니다 (사유 미확인)",
        nextStep: "변환 재시도로 다시 변환합니다.",
        retranscodable: true,
      };
    }
    case "publish": {
      /* 재생본도 `변환 재시도`로 되살린다.
         서버가 분석본과 재생본 중 멈춘 쪽을 골라 다시 돌린다. */
      const err = noted(meta, "publish_error");
      return {
        reason: err ? `재생본을 만들지 못했습니다 (${err})` : "재생본을 만들지 못했습니다",
        nextStep: "변환 재시도로 다시 변환합니다.",
        retranscodable: true,
      };
    }
    case "deident": {
      /* 비식별화는 영상 상세에 전용 재시도가 있어 위치만 안내한다. */
      const err = noted(meta, "deident_error");
      return {
        reason: err ? `비식별화하지 못했습니다 (${err})` : "비식별화하지 못했습니다",
        nextStep: "다시 시도는 영상 상세의 준비 진행에 있습니다.",
      };
    }
    case "deident_unmasked":
      /*
        비식별화를 켜기 전에 처리돼 비식별화를 시도한 적이 없는 영상이다. 그래서 `deident`(시도 후 실패)와 문구가 다르다.
        그대로 분석에 보내면 외부 분석 서비스가 원본 얼굴을 받게 되므로 이슈로 분류한다.
      */
      return {
        reason: "비식별화를 켜기 전에 처리된 영상입니다",
        nextStep: "비식별화 실행은 영상 상세의 준비 진행에 있습니다.",
      };
    case "upload":
      return { reason: "업로드 미완료", nextStep: "파일을 다시 등록해야 합니다." };
    case "analysis":
      return analysisBlock(jobError);
    case "no_clips":
      /* 분석은 끝났지만 장면을 하나도 찾지 못한 경우. 서버가 `ready_for_work`로 분류하면 작업할 것이 없는데 `준비 완료`로 표시된다.
         조치 버튼이 없는 유일한 막힘이다. 분석은 정상 종료했고 그 영상에 장면 경계가 없다.
         그래서 다시 분석해도 결과가 같고, 재분석 버튼을 주면 과금만 다시 발생한다. */
      return {
        reason: "장면이 검출되지 않아 클립이 없습니다",
        nextStep: "다시 분석해도 결과가 같습니다. 영상 상세에서 구간을 직접 추가합니다.",
      };
    case "summary":
      /* 분석은 끝나 클립은 작업에 풀렸지만 라벨러의 AI 초안 요약이 비어 있다.
         같은 재요청 경로를 부르고 서버가 요약만 다시 요청한다. 분석 비용이 다시 들지 않아 분석 재요청과 이름을 나눈다. */
      return {
        reason: "장면 요약을 받지 못했습니다",
        nextStep: "요약 재요청으로 요약만 다시 받습니다.",
        resummarizable: true,
      };
    case "ticket":
      return ticketBlock(v);
    default:
      // 막힘이 없는 행. 단계 패널처럼 이슈 목록 밖의 행에도 이 함수를 부른다.
      return null;
  }
}

/**
 * 이 막힘에 화면에서 누를 조치가 있는지 판정한다. 행의 조치 메뉴가 이 결과를 쓴다.
 *
 * `blockOf`가 여는 조치는 네 가지다(규격 재검사·변환 재시도·분석 재요청·요약 재요청).
 * 조치가 늘어도 메뉴가 어긋나지 않도록 판정을 이 함수 하나에 둔다.
 */
function isActionable(b: Block | null): boolean {
  return !!(b?.reprobable || b?.retranscodable || b?.reanalyzable || b?.resummarizable);
}

/**
 * 지난 분석 요청이 막힌 사유를 꺼낸다. 서버가 `extra.dispatch_error`에 남긴 완결된 문장이다.
 *
 * 요청 대기 행과 이슈 행이 같은 값을 같은 문구로 쓰도록 꺼내는 곳을 하나로 둔다.
 * 요청이 성공하면 서버가 이 키를 지우므로(`merge_extra(remove=…)`) 지난 사유가 남지 않는다.
 * 문장은 서버가 화면용으로 바꿔 준 것을 자르지 않고 쓴다.
 */
function dispatchErrOf(v: VideoDTO): string | undefined {
  const s = String((v.metadata as Record<string, unknown>)?.dispatch_error ?? "").trim();
  return s || undefined;
}

/**
 * 일시 오류로 보류된 분석 요청이면 행에 붙일 안내를 돌려준다. 서버가 `dispatch_deferred`로 준다.
 *
 * 보류된 영상은 서버가 잠시 뒤 다시 요청한다(`redispatch_deferred`). 표시가 없으면 일반 요청 대기로
 * 보여 관리자가 같은 영상을 다시 요청하게 된다. 재요청이 성공하거나 막히면 서버가 표시를 지운다.
 * 짧은 사유와 다음 재요청 시각은 `deferredNote`가 쓰고 전체 사유는 마우스를 올리면 보인다.
 */
function deferredNoteOf(v: VideoDTO): DeferredNote | undefined {
  return deferredNote(v.dispatch_deferred, new Date());
}

/**
 * 등록 묶음 머리의 로딩 골격. 묶음 정보가 도착하면 이 위치에 머리와 단계 칩 줄이 그려진다.
 *
 * 골격의 크기와 여백은 `JobHeader`·칩 줄과 같게 둔다. 크기가 다르면 데이터가 올 때 아래 영상 목록이 밀린다.
 * 블록은 모두 `aria-hidden`이라 바깥에 `role="status"`를 둔다(`ui/PageSkeleton`과 같은 이유).
 */
function JobHeadSkeleton() {
  return (
    <div role="status" aria-label="등록 묶음 정보 불러오는 중">
      <div className="space-y-4 px-5 pb-4 pt-1">
        {/* 이름, 출처와 시각, 수치 한 줄, 분포 막대 */}
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-1.5 w-full" />
      </div>
      {/* 단계 칩 줄. 높이와 간격은 `StageChips`와 같다.
          폭 클래스는 리터럴로 써야 Tailwind가 인식한다.
          문자열을 조합하면 Tailwind가 클래스를 만들지 않아 폭이 0이 된다. */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-separator px-4 py-2">
        {["w-16", "w-14", "w-14", "w-20", "w-20", "w-16", "w-16", "w-16"].map((w, i) => (
          <Skeleton key={i} className={cn("h-7 rounded-capsule", w)} />
        ))}
      </div>
    </div>
  );
}

/**
 * 영상 행의 로딩 골격. 목록이 도착하면 이 위치가 `MediaRow` 행들로 바뀐다.
 *
 * 격자 트랙, 여백, 구분선은 `MediaRow`와 같은 값으로 맞춘다.
 * 조치 버튼은 요청 대기·이슈 행에만 있어서 골격에는 그리지 않는다.
 * 높이는 가장 흔한 행(제목과 경로 두 줄)에 맞춰 데이터가 올 때 영역이 줄지 않게 한다.
 */
function VideoRowsSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div role="status" aria-label="영상 목록 불러오는 중">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="border-b border-separator/60 last:border-b-0">
          <div className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto_auto] items-center gap-x-3 px-4 py-2.5">
            <Skeleton className="aspect-video w-14 rounded-control" />
            <div className="min-w-0 space-y-1.5">
              <Skeleton className="h-4 w-3/5" />
              <Skeleton className="h-3 w-2/5" />
            </div>
            <div className="flex items-center gap-3 justify-self-end">
              <Skeleton className="h-5 w-16 rounded-capsule" />
              <Skeleton className="h-3 w-10" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * 이슈 행의 조치 메뉴(`문제 해결 ▾`, 포털로 띄운다).
 * 되살릴 수 있는 이슈에만 실제 조치를 주고, 나머지는 다음에 할 수 있는 일을 메뉴 안에 쓴다.
 * 조치도 다음 할 일도 없으면 빈 메뉴가 열리므로 버튼을 그리지 않는다.
 * 표 안에 그리면 스크롤 컨테이너가 메뉴를 잘라내므로 포털을 쓴다.
 */
function BlockedAction({
  video, jobError, busy, onReprobe, onRetranscode, onReanalyze, onResummarize,
}: {
  video: VideoDTO;
  jobError?: string | null;
  busy?: boolean;
  /** 규격 재검사를 요청한다. 검증에서 멈춘 영상에 쓴다. */
  onReprobe: (id: string) => void;
  /** 변환 재시도를 요청한다. 분석본 변환에서 멈춘 영상에 쓴다. */
  onRetranscode: (id: string) => void;
  /**
   * 분석 재요청 확인 창을 연다.
   * 분석에서 실패한 영상에 쓴다. 과금되므로 확인 창을 거친다.
   */
  onReanalyze: (id: string) => void;
  /** 요약 재요청 확인 창을 연다. 장면 요약을 받지 못한 영상에 쓴다. */
  onResummarize: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  // 포털로 띄운 메뉴라서 `menu`도 바깥 클릭 판정에서 뺀다.
  // 빼지 않으면 mousedown이 먼저 메뉴를 언마운트해 항목 클릭이 전달되지 않는다.
  // 메뉴는 고정 좌표에 띄우므로 스크롤과 리사이즈 때도 닫는다.
  useOutsideClose(open, btn, () => { setOpen(false); setPos(null); },
    { extraRef: menu, reposition: true });

  const block = blockOf(video, jobError);
  if (!isActionable(block) && !block?.nextStep) return null;
  return (
    <>
      <button
        ref={btn}
        type="button"
        disabled={busy}
        onClick={() => {
          if (open) { setOpen(false); setPos(null); return; }
          const r = btn.current!.getBoundingClientRect();
          const below = window.innerHeight - r.bottom;
          setPos({ top: below < 130 ? r.top - 116 : r.bottom + 6, left: Math.max(8, r.right - 176) });
          setOpen(true);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        /* 행마다 같은 이름이라 접근 이름에 대상 영상을 넣는다. */
        aria-label={`${video.title} 문제 해결`}
        className="inline-flex items-center gap-1 rounded-control border border-bad/30 bg-panel px-2.5 py-1 text-caption font-semibold text-bad-text transition-colors hover:bg-bad-muted disabled:opacity-40"
      >
        문제 해결 <ChevronDown size={12} aria-hidden="true" />
      </button>
      {pos && createPortal(
        <div
          role="menu"
          ref={menu}
          style={{ top: pos.top, left: pos.left, width: 176 }}
          className="fixed z-50 origin-top animate-pop-in overflow-hidden rounded-card border border-separator bg-panel py-1 shadow-pop"
        >
          {block?.reprobable ? (
            <button
              type="button" role="menuitem"
              onClick={() => { setOpen(false); setPos(null); onReprobe(video.video_id); }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-footnote text-label-2 transition-colors hover:bg-fill"
            >
              <RotateCw size={14} aria-hidden="true" /> 검증 재검사
            </button>
          ) : block?.retranscodable ? (
            <button
              type="button" role="menuitem"
              onClick={() => { setOpen(false); setPos(null); onRetranscode(video.video_id); }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-footnote text-label-2 transition-colors hover:bg-fill"
            >
              <Scissors size={14} aria-hidden="true" /> 변환 재시도
            </button>
          ) : block?.reanalyzable ? (
            <button
              type="button" role="menuitem"
              onClick={() => { setOpen(false); setPos(null); onReanalyze(video.video_id); }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-footnote text-label-2 transition-colors hover:bg-fill"
            >
              <Send size={14} aria-hidden="true" /> 분석 재요청
            </button>
          ) : block?.resummarizable ? (
            <button
              type="button" role="menuitem"
              onClick={() => { setOpen(false); setPos(null); onResummarize(video.video_id); }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-footnote text-label-2 transition-colors hover:bg-fill"
            >
              <RotateCw size={14} aria-hidden="true" /> {SUMMARY_RETRY_LABEL}
            </button>
          ) : block?.nextStep ? (
            /* 누를 조치가 없으면 실제로 있는 다음 단계만 쓴다.
               다음 단계가 없으면 아무것도 쓰지 않는다. */
            <p className="px-3 py-1.5 text-caption leading-snug text-label-3">{block.nextStep}</p>
          ) : null}
        </div>,
        document.body,
      )}
    </>
  );
}

/**
 * 앱이 정한 저장 키의 끝 두 단계(`{영상 id}/source`, `{영상 id}/source.mp4`)에 맞는 정규식.
 * 업로드한 파일과 보관으로 옮긴 원본의 키가 이 모양이다. 사용자가 고른 경로가 아니라서 화면에 쓰지 않는다.
 * 분석 입력으로 쓰려고 확장자를 붙인 업로드 원본(`ensure_input_ext`)도 같은 키라 확장자가 있어도 맞춘다.
 */
const APP_KEY_TAIL = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/source(\.[a-z0-9]+)?$/i;

/**
 * 행의 보조 줄에 쓸 경로를 만든다. 제목만으로는 같은 회차를 구분할 수 없어서 원본 위치를 함께 보여 준다.
 *
 * 저장소에서 등록한 영상은 경로 뒤 두 단계만 보여 준다. 앞부분은 같은 폴더의 행이 모두 같다.
 * 업로드한 영상의 키는 서버가 정한 `intake/{영상 id}/source`나 확장자를 붙인 같은 키라 사용자에게 정보가 되지 않는다.
 * 이때는 제목이 이미 파일 이름이므로 경로를 쓰지 않는다.
 */
function pathOf(v: VideoDTO) {
  const src = v.source_uri ?? "";
  const tail = src.split("/").slice(-2).join("/");
  const shown = tail && !APP_KEY_TAIL.test(tail) ? tail : null;
  return [v.broadcaster, v.program_name, shown].filter(Boolean).join(" · ");
}

/**
 * 영상 등록 콘솔을 그린다.
 *
 * `embedded`이면 파이프라인 단계 안에 들어가므로 단계 헤더와 겹치지 않게 자체 제목을 숨긴다.
 */
export function IngestPage({ embedded }: { embedded?: boolean } = {}) {
  const { selectedProjectId: pid } = useSelectedProject();
  const { slug } = useParams<{ slug: string }>();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const projects = useProjects();
  const myRole = projects.data?.find((p) => p.project_id === pid)?.my_role;
  const isSuper = user?.global_role === "super_admin";
  /** 등록은 자산과 비용을 늘리므로 관리자만 할 수 있다. */
  const canIngest = isSuper || myRole === "project_admin";
  /** 메타 일괄 입력 창이 열려 있으면 true. */
  const [metaImportOpen, setMetaImportOpen] = useState(false);
  const batch = params.get("batch");
  /**
   * `?prep=` 값. Job 상세의 칩 필터다. URL에 남겨 새로고침·공유에도 유지된다.
   * 칩은 그룹 키를, 단계 필터는 서버 단계 값(vocab.stagesOf)을 넣는다.
   */
  const prepParam = params.get("prep") as PrepChipKey | PrepStage | null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();

  const [sheetOpen, setSheetOpen] = useState(false);
  const [uploading, setUploading] = useState(0);
  /** 접힌 진행 줄에 쓰는 값. 몇 건 중 몇 건인지와 전체 진행률이다. */
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  /**
   * 확인 모달의 상태. 분석 서비스에 다시 보내는 요청(dispatch·reanalyze·resummarize, 1건 포함)에 쓴다.
   *
   * `reanalyze`도 `dispatch`와 같은 벤더에 같은 과금으로 다시 요청한다.
   * 재시도라는 이름 때문에 무료처럼 읽히기 쉬워 확인 창에서 한 번 알린다.
   * `resummarize`는 장면 요약만 다시 요청하고 분석을 다시 하지 않아 문구가 다르다.
   */
  const [confirm, setConfirm] = useState<{
    kind: "dispatch" | "reanalyze" | "resummarize";
    ids: string[];
  } | null>(null);

  const project = projects.data?.find((p) => p.project_id === pid);
  /**
   * 이 서버에서 저장소·분석 연동을 쓸 수 있으면 true. 판정은 서버(`presets.readiness`)가 하고 화면은 읽기만 한다.
   * 프로젝트를 아직 받지 못했으면 막지 않는다. 막았다 풀면 버튼이 깜박이고, 막아야 할 요청은 서버가 409로 거부한다.
   */
  const storageReady = project?.storage_ready ?? true;
  /**
   * 새 영상 등록을 막아야 하면 true. 저장소가 없거나, 프로젝트에 저장소 대상(NCP 저장소 방식)이나
   * 분석 대상(MI 방식)이 저장되지 않은 경우다. 판정은 서버(`storage_stack_missing`·`analysis_stack_missing`,
   * `presets.storage_target_missing`·`presets.target_missing`)가 하고, 사유는 연동 안내 배너가 표시한다.
   */
  const registerBlocked = !storageReady || (project?.storage_stack_missing ?? false)
    || (project?.analysis_stack_missing ?? false);
  /** 분석 요청은 저장소도 쓰므로 둘 중 하나라도 없으면 막는다. 분석 대상이 없어도 서버가 막는다. */
  const dispatchBlocked = registerBlocked || !(project?.analysis_ready ?? true);
  /**
   * 이 프로젝트 스택에 분석 비용이 없는지 판정한다.
   * 카탈로그를 아직 받지 못했으면 비용이 있다고 본다. 과금 결정 앞에서는 비용이 있다고 보는 쪽이 안전하다.
   * 카탈로그를 받기 전에는 `useAnalysisStack`이 `adapterKey`를 비워 둔다.
   * 벤더 분석기가 없는 스택(`ncp-shots`)에서 전역 단가로 `₩`를 보이면 사용자가 실제로 없는 비용 때문에 요청을 미룬다.
   */
  const stack = useAnalysisStack(project);
  const costFree = !!stack?.adapterKey && !stack.usesVendorAnalyzer;
  /**
   * 실패 토스트를 띄운다. 원인이 확인된 오류만 원인을 표시한다.
   * 오류 사전이 코드를 문구로 바꾸고, 사전에 없는 코드면 동작 이름을 쓴다.
   * 일시 오류(429·5xx·응답 없음)는 상태 코드로 판정해 다시 시도 안내를 붙인다(`apiErrorText`).
   * 권한 문제로 단정하면 잠시 뒤 성공할 요청도 영구 실패로 읽힌다.
   */
  const fail = (what: string) => (e: unknown) => {
    const t = apiErrorText(e, what);
    toast("error", t.message, t.hint);
  };

  /** 상태별 전체 집계(서버). 상태 타일 수의 단일 출처이고, 목록 필터와 같은 SQL 규칙을 쓴다. */
  const counts = useQuery({
    queryKey: ingestKeys.videoBuckets(pid),
    queryFn: () => fetchVideoBuckets(pid),
    enabled: !!pid,
    refetchInterval: 30_000,
  });
  const c = counts.data;
  /**
   * 집계를 한 번도 받지 못했는지 판정한다.
   * 받은 값이 있으면 오래된 값이라도 그 값을 보여 준다.
   */
  const countsFailed = counts.isError && !counts.data;

  /**
   * 그룹 목록을 단계별 쿼리로 나눠 받는다.
   * 서버 필터는 단계 단위라서 그룹(최대 2단계)은 쿼리 2개로 받는다.
   * 훅 순서가 바뀌지 않도록 항상 2개를 선언하고 enabled로만 켠다.
   */
  const prepStages = prepParam ? stagesOf(prepParam) : [];
  const usePrepList = (stage: PrepStage | undefined) =>
    useQuery({
      queryKey: ingestKeys.prepVideos(pid, batch, stage),
      queryFn: () =>
        fetchProjectVideos(pid, {
          prep: stage, batch: batch ?? undefined, limit: 100,
        }),
      enabled: !!pid && !!stage,
      refetchInterval: 10_000,
    });
  const prepListA = usePrepList(prepStages[0]);
  const prepListB = usePrepList(prepStages[1]);
  const prepRows = useMemo(
    () => [...(prepListA.data ?? []), ...(prepListB.data ?? [])],
    [prepListA.data, prepListB.data],
  );

  const jobs = useQuery({
    queryKey: ingestKeys.maiuJobs(pid),
    queryFn: () => fetchMaiuJobs(pid),
    // Job 상세 문제 행의 실패 사유가 이 조회를 쓴다.
    // 서버가 관리자 외에는 `not_project_admin`으로 거부하므로 관리자일 때만 조회한다.
    // 아래 `costStats`, `IngestSheet`의 `canIngest` 가드와 같은 방식이다.
    enabled: !!pid && !!batch && canIngest && (c?.blocked ?? 0) > 0,
  });
  const jobByVideo = useMemo(() => new Map((jobs.data ?? []).map((j) => [j.video_id, j])), [jobs.data]);

  /** Job 상세 모드의 조회. 전용 상세 API를 쓴다. 목록 DTO에는 수행자와 이슈 분해가 없다. */
  const job = useQuery({
    queryKey: ingestKeys.job(batch),
    queryFn: () => fetchIngestJob(batch),
    enabled: !!batch,
    // 등록이나 준비가 진행 중일 때만 다시 조회한다. 끝난 Job은 바뀌지 않는다.
    refetchInterval: (qy) => {
      const d = qy.state.data;
      if (!d) return false;
      const moving =
        d.import_status === "pending" || d.import_status === "running" ||
        d.prep.total > d.prep.ready_for_work + d.prep.blocked + d.prep.awaiting_analysis;
      return moving ? 10_000 : false;
    },
  });
  const jobData = job.data;
  const batchImporting =
    jobData?.import_status === "pending" || jobData?.import_status === "running";
  /**
   * Job 안에서 준비가 아직 진행 중인지 판정한다. 목록을 다시 조회할지 정하는 값이다.
   *
   * 판정은 헤더와 같은 식을 쓴다. ready·blocked·awaiting 어느 것도 아닌 영상이 있으면 진행 중이다.
   * 폴더째 등록 중일 때만 다시 조회하면 변환·분석 등록·분석이 도는 동안 헤더와 칩만 바뀌고 행은 이전 상태로 남는다.
   */
  const jobMoving = !!jobData && (
    batchImporting
    || jobData.prep.total
       > jobData.prep.ready_for_work + jobData.prep.blocked + jobData.prep.awaiting_analysis
  );
  const batchVideos = useQuery({
    queryKey: ingestKeys.batchVideos(pid, batch),
    queryFn: () => fetchProjectVideos(pid, { batch: batch ?? undefined, limit: 100 }),
    enabled: !!pid && !!batch,
    /* 헤더(10초)보다 느린 주기로 다시 조회한다. 단계 변화는 칩에 먼저 뜨고 행이 뒤따른다. */
    refetchInterval: jobMoving ? 15_000 : false,
  });
  const batchRows = batchVideos.data ?? [];
  /**
   * 확인 창에 표시할 영상 제목을 찾는 표. 과금되는 조치의 판단 근거를 같은 화면에 둔다.
   *
   * 확인 창을 여는 곳은 Job 상세의 두 목록(칩으로 거른 목록, 전체 목록)이다.
   * 확인 창을 여는 목록을 늘리면 이 표에도 넣는다. 빠뜨리면 그 행에서 연 창만 대상을 표시하지 못한다.
   */
  const videoOf = useMemo(
    () => new Map([...prepRows, ...batchRows].map((v) => [v.video_id, v])),
    [prepRows, batchRows],
  );
  const batchTruncated = !!jobData && jobData.video_count > batchRows.length;

  /**
   * 분석 단가를 서버 설정에서 읽는다.
   * 화면에 고정해 두면 단가가 바뀐 뒤로 틀린 금액을 보여 준다.
   */
  const costStats = useQuery({
    queryKey: ingestKeys.maiuCostStats(pid),
    queryFn: () => fetchMaiuCostStats(pid),
    // 서버가 관리자 외에는 `not_project_admin`으로 거부하므로 관리자일 때만 조회한다.
    // 단가가 없으면 `costOf`가 `null`을 돌려주므로 금액 표시(상태 타일·확인 창)는 생략된다.
    enabled: !!pid && canIngest,
    staleTime: 10 * 60_000,
  });
  const rate = costStats.data?.cost_per_hour_krw;
  /** 영상 길이 합과 서버 단가로 예상 분석 비용을 낸다. 산식은 서버 비용 집계와 같다(`lib/span.costKrw`). */
  const costOfSecs = (secs: number) => costKrw(secs, rate);
  const costOf = (rows: VideoDTO[]) => costOfSecs(rows.reduce((s, v) => s + (v.duration_seconds ?? 0), 0));

  /**
   * 확인 창을 닫는다. 창을 닫는 경로(취소·바깥 클릭·성공·실패)가 모두 이 함수를 부른다.
   * 창에 입력을 더하면 여기서 함께 초기화한다. 한 경로에서만 초기화하면 고른 값이 다음 요청에 남는다.
   */
  const closeConfirm = () => setConfirm(null);

  /** 조치가 끝난 뒤 확인 창을 닫고, 영향을 받는 조회를 무효화한다. */
  const afterMutate = () => {
    closeConfirm();
    void qc.invalidateQueries({ queryKey: ingestKeys.videosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.batchVideosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.videoBucketsAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.prepVideosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
    /* 묶음 상세의 머리·칩·오른쪽 레일이 이 조회를 쓴다. 무효화에서 빼면 요청을 보낸 뒤에도 요청 대기 수와 버튼이 남는다. */
    void qc.invalidateQueries({ queryKey: ingestKeys.jobAll() });
    void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
  };

  /** 고른 영상의 분석 요청을 보낸다. */
  const dispatch = useMutation({
    mutationFn: (ids: string[]) => dispatchAnalysis(pid, ids),
    /* 문구는 서버가 준 수로만 정한다. 규칙과 이유는 `dispatchToast`에 있다. */
    onSuccess: (r) => {
      afterMutate();
      void qc.invalidateQueries({ queryKey: ingestKeys.maiuJobsAll() });
      const t = dispatchToast(r);
      toast(t.tone, t.title, t.detail);
    },
    onError: (e) => {
      closeConfirm();
      fail("분석 요청 실패")(e);
    },
  });

  /**
   * 분석 재요청을 보낸다. 분석에서 실패한 잡을 되살린다.
   *
   * 영상 상세 타임라인과 같은 경로(`maiu/retry`)와 이름을 쓴다.
   * 처리 방식은 서버가 정한다. 등록된 자산이 있으면 분석만 다시 하고, 없으면 등록부터 한다.
   */
  const reanalyze = useMutation({
    mutationFn: (ids: string[]) => retryAnalysis(pid, ids),
    onSuccess: (r, ids) => {
      afterMutate();
      void qc.invalidateQueries({ queryKey: ingestKeys.maiuJobsAll() });
      // 서버가 영상마다 분석 재요청과 요약 재요청 중 하나로 처리한 수를 그대로 알린다(`retryToast`).
      const t = retryToast(r, ids.length);
      toast(t.tone, t.title, t.detail);
    },
    onError: (e) => {
      // 확인 창을 닫기 전에 어느 버튼으로 연 창인지 읽는다.
      const title = retryFailTitle(confirm?.kind === "resummarize");
      closeConfirm();
      fail(title)(e);
    },
  });

  /** 재검사·재시도 요청 뒤 영향을 받는 조회를 무효화한다. */
  const afterRetry = () => {
    void qc.invalidateQueries({ queryKey: ingestKeys.videosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.batchVideosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.videoBucketsAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.prepVideosAll() });
    void qc.invalidateQueries({ queryKey: ingestKeys.jobAll() });
  };

  /**
   * 영상 한 건의 규격 재검사를 요청한다. 검증에서 멈춘 영상에 쓴다.
   * probe를 되돌리면 서버가 다시 판정해 영상을 변환 단계로 보낸다. 코덱 위반만 있으면 변환을 거쳐 처리가 이어진다.
   */
  const reprobe = useMutation({
    mutationFn: (id: string) => resetProbe(pid, id),
    onSuccess: () => {
      afterRetry();
      toast("success", "검증 재검사 요청", "검사가 끝나면 목록이 갱신됩니다.");
    },
    onError: (e) => {
      afterRetry();
      fail("검증 재검사 실패")(e);
    },
  });

  /**
   * 영상 한 건의 변환 재시도를 요청한다. 재시도 상한에 걸려 멈춘 변환을 되살린다.
   * 변환 전용 경로(`transcode/retry`)를 쓴다.
   * 이 영상은 규격을 통과했으므로 규격 재검사를 부르면 409 응답을 받는다.
   */
  const retranscode = useMutation({
    mutationFn: (id: string) => retryTranscode(pid, id),
    onSuccess: () => {
      afterRetry();
      toast("success", "변환 재시도 요청", "변환이 끝나면 요청 대기로 이동합니다.");
    },
    onError: (e) => {
      afterRetry();
      fail("변환 재시도 실패")(e);
    },
  });

  const busy =
    dispatch.isPending || reprobe.isPending || retranscode.isPending || reanalyze.isPending;

  /** 상태 타일 셋의 값. 수와 길이 합은 서버 집계(`spans`)다. */
  const readySpan = c?.spans?.find((x) => x.bucket === "ready");
  const readyCost = readySpan ? costOfSecs(readySpan.total_seconds) : null;
  const oldestAnalyzing = c?.spans?.find((x) => x.bucket === "analyzing")?.oldest_requested_at;
  const statusTiles: IngestTile[] = c
    ? [
        {
          key: "ready", label: "요청 대기", count: c.ready, dot: "bg-label-3",
          /* 길이를 모르는 영상이 있으면 합과 비용은 하한이라 `+`를 붙이고 그 수를 밝힌다. */
          note: readySpan && c.ready > 0
            ? [
                `총 ${fmtHourMin(readySpan.total_seconds)}${readySpan.unmeasured > 0 ? "+" : ""}`,
                !costFree && canIngest && readyCost != null
                  ? `예상 비용 ₩${readyCost.toLocaleString("ko-KR")}${readySpan.unmeasured > 0 ? "+" : ""}`
                  : "",
                readySpan.unmeasured > 0 ? `길이 모름 ${readySpan.unmeasured.toLocaleString("ko-KR")}개` : "",
              ].filter(Boolean).join(" · ")
            : undefined,
        },
        {
          key: "analyzing", label: "분석 중", count: c.analyzing, dot: "bg-note",
          note: oldestAnalyzing && c.analyzing > 0
            ? `가장 오래된 요청 ${fmtHourMin(Math.max(0, (Date.now() - new Date(oldestAnalyzing).getTime()) / 1000))} 경과`
            : undefined,
        },
        { key: "blocked", label: "문제", count: c.blocked, dot: "bg-bad" },
      ]
    : [];

  // ── 행 렌더: 열 정의 대신 행을 조립한다(features/ingest/README.md) ───────────────

  /** 행의 공통 메타(길이). 행마다 다른 정보를 붙일 수 있도록 각 행에 직접 넣는다. */
  const durMeta = (v: VideoDTO) =>
    v.duration_seconds != null ? (
      <span className="tabular-nums">{fmtTimecode(v.duration_seconds)}</span>
    ) : null;

  /**
   * 불러온 영상 중 하나라도 썸네일이 있으면 true. 그때 썸네일 열을 그린다.
   * 칩으로 거른 목록과 전체 목록이 이 값 하나를 써서 열 폭이 같아진다.
   */
  const showThumb = [prepRows, batchRows].some((rs) => rs.some((v) => !!v.thumbnail_url));

  /**
   * Job 상세의 행.
   * 모든 상태가 섞여 있어 상태 배지가 필요하다.
   */
  const batchRow = (v: VideoDTO) => {
    const ps = v.prep_stage;
    return (
      <MediaRow
        key={v.video_id}
        thumbnailUrl={v.thumbnail_url}
        thumb={showThumb}
        title={v.title}
        subtitle={pathOf(v)}
        /*
          요청 대기 행에도 지난 요청이 실패한 사유를 둔다. 그래서 분석 요청을 누르는 관리자가 직전 실패를 볼 수 있다.
          일시 오류로 보류된 요청이면 서버가 잠시 뒤 다시 요청한다는 안내를 둔다.
          안내가 없으면 일반 요청 대기로 보여 관리자가 같은 영상을 다시 요청하게 된다.
        */
        note={
          ps === "blocked"
            ? blockOf(v, jobByVideo.get(v.video_id)?.error)?.reason ?? "사유 미상"
            : ps === "awaiting_analysis"
              ? dispatchErrOf(v) ?? deferredNoteOf(v)?.text
              : undefined
        }
        noteTone={ps === "awaiting_analysis" && !dispatchErrOf(v) ? "warn" : "bad"}
        noteTitle={ps === "awaiting_analysis" && !dispatchErrOf(v) ? deferredNoteOf(v)?.title : undefined}
        accent={ps ? TONE_FILL[STAGE_TONE[ps]] : undefined}
        meta={
          <>
            {ps && (
              <StageBadge stage={ps} />
            )}
            {/* 같은 내용의 영상이 이미 있다는 표시.
                업로드 확정 때 서버가 남긴다. */}
            {(v.metadata as { duplicate_of?: string } | null)?.duplicate_of && (
              <Chip tone="warn" title="같은 내용의 영상이 이미 등록되어 있습니다">중복</Chip>
            )}
            {/*
              분석이 끝났으면 클립 수가 0이어도 표시한다.
              표시하지 않으면 0인지 아직 분석 전인지 구분할 수 없어, 장면을 하나도 찾지 못한 영상이 `준비 완료`로만 보인다.
              앞 단계(변환 중·요청 대기 등)에서는 0이 아직 분석 전이라는 뜻이므로 표시하지 않는다.
            */}
            {ps === "ready_for_work"
              ? <span className={cn("tabular-nums", v.clip_count === 0 && "text-bad-text")}>
                  클립 {v.clip_count}
                </span>
              : v.clip_count > 0 && <span className="tabular-nums">클립 {v.clip_count}</span>}
            {durMeta(v)}
          </>
        }
        onClick={() => slug && navigate(`/p/${slug}/videos/${v.video_id}#prep`)}
        action={
          /* 분석은 사용자가 대상을 보고 길이를 골라 직접 요청하므로 대기 중인 영상에는 항상 버튼이 있다. */
          ps === "awaiting_analysis" && canIngest ? (
            /*
              1건 요청도 확인 창을 거친다. 과금은 시간 단위라서 두 시간짜리 영상 한 편도 큰 금액이 될 수 있다.
              누르는 곳에 따라 판단 근거(비용)가 보이거나 안 보이면 같은 결정을 서로 다른 근거로 하게 된다.
            */
            <Button size="sm" variant="secondary" disabled={busy || dispatchBlocked} aria-label={`${v.title} 분석 요청`}
              onClick={() => setConfirm({ kind: "dispatch", ids: [v.video_id] })}>
              <Send size={14} className="mr-1" aria-hidden="true" /> 분석 요청
            </Button>
          ) : ps === "blocked" && canIngest ? (
            <BlockedAction
              video={v}
              jobError={jobByVideo.get(v.video_id)?.error}
              busy={busy}
              onReprobe={(id) => reprobe.mutate(id)}
              onRetranscode={(id) => retranscode.mutate(id)}
              onReanalyze={(id) => setConfirm({ kind: "reanalyze", ids: [id] })}
              onResummarize={(id) => setConfirm({ kind: "resummarize", ids: [id] })}
            />
          ) : null
        }
      />
    );
  };

  /**
   * 등록이 끝난 뒤 이동할 곳을 정한다.
   * 1건이면 그 영상의 준비 타임라인으로, 여러 건이면 Job 상세로 간다.
   * 그래서 1건만 올린 사용자는 Job 화면을 거치지 않는다.
   */
  const afterIngest = (batchId: string, count: number, firstVideoId?: string, note?: string) => {
    setSheetOpen(false);
    // 등록은 분석을 요청하지 않으므로 실제로 있는 다음 할 일(분석 요청)을 알린다.
    // 분석은 사용자가 대상을 보고 길이를 골라 직접 요청한다.
    // 등록 결과 토스트는 이 함수에서만 띄운다. 엔진이 따로 띄우면 같은 결과가 두 번 뜬다.
    // 엔진이 알린 덧붙임(이미 등록됐던 파일 수)이 있으면 안내 대신 덧붙임을 쓴다.
    toast("success", `영상 ${count}개를 등록했습니다`, note ?? "규격 검증이 끝나면 요청 대기로 들어갑니다.");
    if (count === 1 && firstVideoId && slug) {
      navigate(`/p/${slug}/videos/${firstVideoId}#prep`);
      return;
    }
    setParams({ stage: "ingest", batch: batchId });
  };

  /**
   * 폴더째 등록 작업을 시작한 뒤 Job 상세로 이동한다.
   * 시작 시점에는 건수가 정해지지 않아 표시하지 않는다. Job 행이 등록되는 대로 센다.
   */
  const afterJob = (batchId: string) => {
    setSheetOpen(false);
    setParams({ stage: "ingest", batch: batchId });
  };

  const ingestButton = canIngest && (
    <Button onClick={() => setSheetOpen(true)} disabled={registerBlocked}>
      <Plus size={14} className="mr-1" aria-hidden="true" /> 영상 등록
    </Button>
  );

  /**
   * 조건에 맞는 영상 전체의 id를 서버에서 받는다.
   *
   * 화면에 불러온 행을 모으면 커서 한 페이지인 50건까지만 담긴다.
   * 그러면 "88건 요청"이라고 쓰고 50건만 처리하게 된다. 그래서 전체를 조회할 수 있는 서버가 조건을 id로 푼다.
   * 서버 상한(truncated)에 걸리면 그 사실을 그대로 알린다.
   */
  const selectByCondition = async (cond: {
    prep?: PrepStage;
  }): Promise<{ ids: string[]; total: number; truncated: boolean }> => {
    const r = await selectVideos(pid, { batch: batch ?? undefined, ...cond });
    return { ids: r.video_ids, total: r.total, truncated: r.truncated };
  };

  /**
   * 레일의 분석 요청을 실행한다. 조건을 서버에서 id로 풀어 분석 요청 API에 넘긴다.
   *
   * 주의: id를 나눠 보내지 않고 한 번에 넘긴다. 서버 조회는 최대 2000건(`_SELECT_CAP`)을 준다.
   * 일괄 API에는 요청당 상한이 있다(`MaiuDispatchRequest` 100건).
   */
  const runOnCondition = async (
    cond: { prep?: PrepStage },
    run: (ids: string[]) => void,
  ) => {
    const { ids, truncated } = await selectByCondition(cond);
    if (!ids.length) {
      toast("warning", "대상이 없습니다.");
      return;
    }
    if (truncated) {
      toast("warning", `대상이 많아 영상 ${ids.length}개까지만 처리합니다`, "남은 영상은 다시 실행하세요.");
    }
    run(ids);
  };

  const [sortKey, setSortKey] = useState<SortKey>("recent");

  /*
    옛 주소 `/p/:slug/storage`는 `?tab=storage`를 붙여 이 단계로 온다. 그 주소는 저장소에서 등록하던 화면이라
    등록 창을 저장소 탭(창의 첫 탭)으로 연다. 한 번 열면 값을 지워 새로고침마다 다시 열리지 않게 한다.
    역할 정보를 받기 전에는 기다리고, 등록할 수 없는 역할이면 값만 지운다.
  */
  const tabParam = params.get("tab");
  useEffect(() => {
    if (tabParam !== "storage" || (!isSuper && !projects.data)) return;
    // 저장소 연동이나 분석 대상이 없으면 창을 열지 않는다. 그 이유는 위의 안내가 표시한다.
    if (canIngest && !registerBlocked) setSheetOpen(true);
    const next = new URLSearchParams(params);
    next.delete("tab");
    setParams(next, { replace: true });
  }, [tabParam, isSuper, projects.data, canIngest, registerBlocked, params, setParams]);

  const pageEmpty = c && c.total === 0;

  /* 자식인 Job 표가 데이터를 받은 시각. 캐시에서 읽기만 한다.
     값을 부모로 올리려고 콜백을 두면 렌더마다 상태가 오간다. 이 컴포넌트에서 같은 조회를 또 열면 요청이 두 배가 된다.
     캐시에서 읽으면 시각이 늦게 갱신될 수는 있어도 오래된 값을 최신처럼 보이게 하지는 않는다. */
  const jobsUpdatedAt = qc.getQueryState(ingestKeys.batches(pid))?.dataUpdatedAt ?? 0;

  return (
    <div className="space-y-4">
      {!embedded && <h2 className="text-headline text-label">영상 등록</h2>}
      {/* 갱신 시각과 주 버튼은 작업 단계 화면 머리 오른쪽에 둔다(`HeaderActions`). */}
      <HeaderActions>
        {/* 갱신 시각은 하나만 두고, 누르면 이 화면이 쓰는 조회를 함께 다시 부른다.
            시각은 두 조회 중 더 오래된 쪽을 써서 두 데이터가 모두 그 시각 이후의 값이게 한다.
            있는 값만 모으고 하나도 없으면 넘기지 않는다. `Infinity` 같은 대체값을 넣으면
            두 값이 모두 없을 때 머리에 `마지막 업데이트 Invalid Date`가 뜬다. */}
        <LastUpdated
          at={
            [counts.dataUpdatedAt, jobsUpdatedAt].filter((t): t is number => !!t).length > 0
              ? Math.min(...[counts.dataUpdatedAt, jobsUpdatedAt].filter((t): t is number => !!t))
              : undefined
          }
          onRefresh={() => {
            void counts.refetch();
            void qc.invalidateQueries({ queryKey: ingestKeys.batchesAll() });
          }}
          refreshing={counts.isRefetching}
        />
        {ingestButton}
      </HeaderActions>

      {/* 등록 시트는 업로드 상태를 유지하려고 항상 마운트하고, open으로 보이기와 숨기기만 바꾼다.
          단, 등록 권한이 있을 때만 마운트한다. 시트를 여는 버튼(`ingestButton`)도 `canIngest`로 가려져 있고,
          권한 없이 마운트하면 시트 안 업로드 엔진이 쓸 수 없는 업로드 조건을 조회한다.
          `/ingest-batches`의 403은 화면이 조회 권한 없음을 표시하는 데 쓰므로 그대로 둔다. */}
      {pid && canIngest && (
        <IngestSheet
          projectId={pid}
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          onDone={afterIngest}
          onJobQueued={afterJob}
          onBusyChange={setUploading}
          onProgress={setUploadProgress}
        />
      )}

      {/*
        접힌 진행 줄. 시트를 닫아도 업로드는 계속된다.
        몇 건 중 몇 건, 전체 몇 %인지 표시한다.
        값은 업로드 큐가 이미 가진 바이트 합에서 계산한다. 버튼 글자는 명사구로 쓴다.
      */}
      {!sheetOpen && uploading > 0 && (
        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          className="flex w-full items-center gap-2.5 rounded-card border border-accent/30 bg-accent-muted px-3.5 py-2 text-footnote text-label-2 transition-colors hover:bg-accent-muted/70"
        >
          <Loader2 size={14} className="animate-spin text-accent" aria-hidden="true" />
          업로드 진행 중
          {uploadProgress && (
            <span className="tabular-nums text-label-3">
              {uploadProgress.done.toLocaleString("ko-KR")} / {uploadProgress.total.toLocaleString("ko-KR")}개
              {" · "}{Math.round(uploadProgress.ratio * 100)}%
            </span>
          )}
          <span className="ml-auto font-medium text-accent">펼치기</span>
        </button>
      )}

      {/* 저장소·분석 연동이 없으면 할 수 없는 일과 해결 방법을 먼저 알린다. 아래의 버튼은 비활성 상태다. */}
      <ConnectionNotice project={project} />

      {batch ? (
        /* ── Job 상세: 목록 하나와 오른쪽 레일(진행·할 수 있는 일) ── */
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <section className="min-w-0 flex-1 overflow-hidden rounded-card border border-separator bg-panel shadow-e1">
          <div className="px-5 pt-4">
            <button
              type="button"
              onClick={() => setParams({ stage: "ingest" })}
              className="inline-flex items-center gap-1 rounded-control text-footnote text-label-3 transition-colors hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              <ArrowRight size={14} className="rotate-180" aria-hidden="true" /> 등록 묶음
            </button>
          </div>

          {jobData ? (
            <>
              <JobHeader
                job={jobData}
                activeChip={prepParam}
                /* 서버는 영상 메타 편집과 같은 관리자 권한으로 받는다. 영상이 없는 묶음에는 채울 행이 없다. */
                actions={jobData.video_count > 0 ? (
                  <Button size="sm" variant="secondary" onClick={() => setMetaImportOpen(true)}>
                    <FileSpreadsheet size={14} aria-hidden="true" /> 메타 일괄 입력
                  </Button>
                ) : undefined}
                onPick={(k) => {
                  const next = new URLSearchParams(params);
                  next.set("prep", k);
                  setParams(next, { replace: true });
                }}
              />
              <MetaImportModal
                open={metaImportOpen}
                onClose={() => setMetaImportOpen(false)}
                batchId={jobData.batch_id}
                videoCount={jobData.video_count}
              />
              <JobIssueBanner
                issues={jobData.issues ?? []}
                total={jobData.prep.blocked}
                active={prepParam === "blocked"}
                onPick={() => {
                  const next = new URLSearchParams(params);
                  next.set("prep", "blocked");
                  setParams(next, { replace: true });
                }}
                onClear={() => {
                  const next = new URLSearchParams(params);
                  next.delete("prep");
                  setParams(next, { replace: true });
                }}
              />
              {/* 칩과 정렬을 한 줄에 둔다. 더 세밀한 단계(업로드 중과 검증 대기)는 행 배지가 이미 보여 준다.
                  영상이 없는 묶음에는 0뿐인 칩 줄을 그리지 않는다.
                  같은 기준의 단계 필터를 따로 두면 두 컨트롤이 같은 `?prep=`에 서로 다른 값(그룹 키와 서버 단계 값)을 넣어
                  라벨에 `단계: awaiting` 같은 내부 키가 그대로 표시된다. */}
              {jobData.prep.total > 0 && (
              <div className="flex flex-wrap items-center gap-2 border-b border-separator px-4 py-2">
                <StageChips
                  counts={jobData.prep}
                  active={prepParam}
                  onPick={(k) => {
                    const next = new URLSearchParams(params);
                    if (k) next.set("prep", k);
                    else next.delete("prep");
                    setParams(next, { replace: true });
                  }}
                />
                {(prepParam ? prepRows : batchRows).length > 1 && (
                  <span className="ml-auto">
                    <SortControl
                      value={sortKey}
                      onChange={setSortKey}
                      scopeNote={
                        /* 정렬은 불러온 행 기준이다.
                           불러온 행이 전체보다 적으면 그 사실을 표시한다. */
                        !prepParam && batchTruncated
                          ? `목록의 ${batchRows.length.toLocaleString("ko-KR")}개 기준`
                          : undefined
                      }
                    />
                  </span>
                )}
              </div>
              )}
            </>
          ) : job.isError ? (
            /* 권한 없음이나 존재하지 않는 Job을 "불러오는 중"으로 두지 않는다.
               그러면 로딩이 끝나지 않는 것처럼 보인다. */
            <div className="px-4 py-6">
              <EmptyState
                message={(job.error as { status?: number })?.status === 403
                  ? "등록 묶음 조회 권한 없음"
                  : "등록 묶음 없음"}
                description={(job.error as { status?: number })?.status === 403
                  ? "등록 묶음은 프로젝트 관리자만 볼 수 있습니다."
                  : "삭제되었거나 잘못된 링크입니다."}
              />
            </div>
          ) : (
            <JobHeadSkeleton />
          )}

          {(() => {
            const rows = sortRows(prepParam ? prepRows : batchRows, sortKey);
            const loading = prepParam
              ? prepListA.isLoading || prepListB.isLoading
              : batchVideos.isLoading;
            /* 목록 위치에도 골격을 그린다. 위의 머리와 함께 그려야 두 영역이 따로 움직이지 않는다. */
            if (loading) return <VideoRowsSkeleton />;
            if (rows.length > 0) {
              return (
                <>
                  <MediaRows>{rows.map(batchRow)}</MediaRows>
                  {/* 불러온 수가 전체보다 적으면 그 사실을 표시한다.
                      커서 페이지에서 불러온 행만 세면 전체보다 적은 수가 뜬다. */}
                  {!prepParam && batchTruncated && (
                    <p className="border-t border-separator px-4 py-2 text-caption tabular-nums text-label-3">
                      {rows.length.toLocaleString("ko-KR")} / 전체 {jobData!.video_count.toLocaleString("ko-KR")}개 표시
                    </p>
                  )}
                  {prepParam === "blocked" && jobData && rows.length < jobData.prep.blocked && (
                    <p className="border-t border-separator px-4 py-2 text-caption tabular-nums text-label-3">
                      {rows.length.toLocaleString("ko-KR")} / 문제 {jobData.prep.blocked.toLocaleString("ko-KR")}개 표시
                    </p>
                  )}
                </>
              );
            }
            return (
              <div className="px-4 py-6">
                {prepParam ? (
                  <EmptyState message="이 상태의 영상 없음" description="다른 상태를 고르면 해당 영상이 표시됩니다." />
                ) : batchImporting ? (
                  <EmptyState message="등록 영상 없음" description="저장소 폴더의 영상을 등록하는 중입니다. 등록이 끝난 영상부터 표시됩니다." />
                ) : jobData?.import_status === "done" ? (
                  // 폴더째 등록이 0건으로 끝나는 흔한 이유는
                  // 모든 파일이 이미 등록된 경우다.
                  <EmptyState message="새로 등록된 영상 없음" description="이 폴더의 영상은 모두 이미 등록되어 있어 건너뛰었습니다." />
                ) : (
                  /* 묶음은 불러왔으므로 링크는 정상이다. 확인되지 않은 원인은 쓰지 않는다. */
                  <EmptyState
                    message="등록된 영상 없음"
                    description="이 묶음으로 올린 파일이 없거나 업로드가 끝나지 않았습니다."
                  />
                )}
              </div>
            );
          })()}
        </section>

        {/* 오른쪽 레일. 분석 요청 카드를 둔다.
            좁은 화면에서는 목록 위에 놓인다. */}
        {jobData && jobRailHasCards({ counts: jobData.prep, canAct: canIngest }) && (
          <div className="order-first lg:order-none">
            <JobRail
              counts={jobData.prep}
              busy={busy}
              canAct={canIngest}
              costFree={costFree}
              onDispatch={() => void runOnCondition(
                { prep: "awaiting_analysis" },
                (ids) => setConfirm({ kind: "dispatch", ids }),
              )}
            />
          </div>
        )}
        </div>
      ) : pageEmpty ? (
        <EmptyState message="등록 영상 없음" action={ingestButton || undefined} />
      ) : (
        /* ── 콘솔: 상태 타일 · 등록 묶음 표 ── */
        <>
          {/* 집계를 받지 못했으면 실패로 표시한다. "할 일 없음"으로 표시하지 않는다. */}
          {countsFailed ? (
            <div className="rounded-card border border-separator bg-panel px-3 py-2 shadow-e1">
              <ErrorState inline message="등록 집계 조회 실패" onRetry={() => void counts.refetch()} />
            </div>
          ) : c && (
            <IngestStatusTiles
              tiles={statusTiles}
              emphasize={canIngest ? "ready" : undefined}
            />
          )}

          {/* 기본 본문은 등록 묶음 표다.
              영상이 1건인 묶음도 묶음 상세로 간다. 분석 요청과 문제 조치는 묶음 상세에 있다. */}
          <IngestJobsPanel
            onOpen={(b, prep) => {
              const next: Record<string, string> = { stage: "ingest", batch: b.batch_id };
              if (prep) next.prep = prep;
              setParams(next);
            }}
            /* 묶음 없는 영상 수를 밝히는 데 쓰는 서버 집계. */
            projectVideoTotal={c?.total}
          />
        </>
      )}

      {/* 확인 창. 과금되는 요청(dispatch·reanalyze)에 쓴다.
          본문은 굵은 결과 한 줄, 대상 이름, 판단 근거 표, 조건 한 줄 순서다.
          마지막 버튼은 본문과 같은 단위로 결과를 쓴다(`영상 7개 분석 요청`). */}
      <Modal
        open={!!confirm}
        onClose={closeConfirm}
        title={confirm?.kind === "resummarize" ? SUMMARY_RETRY_LABEL
          : confirm?.kind === "reanalyze" ? "분석 재요청" : "분석 요청"}
        footer={
          confirm && (
            <>
              <Button variant="secondary" onClick={closeConfirm}>취소</Button>
              <Button
                disabled={busy || dispatchBlocked}
                onClick={() =>
                  confirm.kind === "dispatch"
                    ? dispatch.mutate(confirm.ids)
                    : reanalyze.mutate(confirm.ids)
                }
              >
                {busy ? "처리 중…"
                  : confirm.kind === "dispatch" ? `영상 ${confirm.ids.length.toLocaleString("ko-KR")}개 분석 요청`
                  : confirm.kind === "resummarize" ? `영상 ${confirm.ids.length.toLocaleString("ko-KR")}개 ${SUMMARY_RETRY_LABEL}`
                  : `영상 ${confirm.ids.length.toLocaleString("ko-KR")}개 분석 재요청`}
              </Button>
            </>
          )
        }
      >
        {confirm && (() => {
          const n = confirm.ids.length.toLocaleString("ko-KR");
          const known = confirm.ids.map((id) => videoOf.get(id)).filter((v): v is VideoDTO => !!v);
          /* 대상 이름을 셋까지 적고 나머지는 수로 쓴다. 사용자가 잘못 고른 대상을 확인하는 근거다. */
          const names = known.slice(0, 3).map((v) => v.title);
          const rest = confirm.ids.length - names.length;
          const targets = names.length > 0 && (
            <p className="text-footnote text-label-2">
              {names.join(" · ")}{rest > 0 ? ` 외 ${rest.toLocaleString("ko-KR")}개` : ""}
            </p>
          );
          /* 길이와 비용은 대상 전체의 길이가 있을 때만 적는다. 일부만 있으면 합계가 틀린다. */
          const allKnown = known.length === confirm.ids.length;
          const secs = known.reduce((sum, v) => sum + (v.duration_seconds ?? 0), 0);
          const cost = allKnown && canIngest ? costOf(known) : null;
          const facts: [string, ReactNode][] = [];
          if (confirm.kind !== "dispatch" && known.length === 1) {
            const reason = blockOf(known[0], jobByVideo.get(known[0].video_id)?.error)?.reason;
            if (reason) facts.push(["실패 사유", reason]);
          }
          // 요약 재요청은 분석을 다시 하지 않으므로 길이와 비용을 판단 근거로 싣지 않는다.
          if (allKnown && secs > 0 && confirm.kind !== "resummarize") {
            facts.push(["총 길이", fmtHourMin(secs)]);
            if (!costFree && cost != null) facts.push(["예상 비용", <b key="c">₩{cost.toLocaleString("ko-KR")}</b>]);
          }
          return (
            <div className="space-y-4">
              {confirm.kind === "resummarize" ? (
                <div>
                  <p className="text-callout text-label"><b>{SUMMARY_CONFIRM.lead}</b></p>
                  <p className="mt-1 text-footnote text-label-3">{SUMMARY_CONFIRM.sub}</p>
                </div>
              ) : (
              <p className="text-callout text-label">
                {confirm.kind === "dispatch" ? (
                  <><b>영상 {n}개</b>를 분석 서비스에 보냅니다.</>
                ) : (
                  /* 재시도라는 말이 무료처럼 읽히기 쉬워 비용을 같은 문장에 둔다. */
                  <><b>영상 {n}개</b>를 분석 서비스에 다시 보냅니다.{costFree ? "" : " 첫 요청과 같이 비용이 듭니다."}</>
                )}
              </p>
              )}
              {targets}
              {facts.length > 0 && (
                <dl className="space-y-2 rounded-control bg-canvas-2 px-3.5 py-3 text-footnote">
                  {facts.map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-4">
                      <dt className="shrink-0 text-label-3">{k}</dt>
                      <dd className="text-right tabular-nums text-label">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {confirm.kind === "dispatch" && !costFree && cost != null && (
                <p className="text-footnote text-label-3">금액은 요청할 때 확정됩니다.</p>
              )}
              {confirm.kind === "reanalyze" && (
                <p className="text-footnote text-label-3">파일 자체의 문제라면 다시 요청해도 같은 결과가 나옵니다.</p>
              )}
            </div>
          );
        })()}
      </Modal>
    </div>
  );
}
