/**
 * 영상 상세 화면(`/p/:slug/videos/:videoId`). 영상 메타와 준비 상태를 조회한다.
 *
 * 본문은 플레이어(재생본이 준비되면), 준비 진행(끝났으면 요약으로 접는다), 클립(12행에서 접는다) 순서다.
 * 준비가 막힌 영상은 준비 진행을 플레이어 위로 올리고 실패 사유를 펼친다.
 * 사이드바에는 영상 메타·등록 정보·파일이 있다. 관리자에게는 파일 묶음에 분석본 정리 버튼을 둔다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { MediaPlayerInstance } from "@vidstack/react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Check, ChevronDown, ChevronRight, ChevronUp, Copy, Loader2, Pencil, RotateCw,
  SkipForward, Trash2, TriangleAlert,
} from "lucide-react";

import { useCanOpen } from "@/shell/RoleLink";
import {
  deleteVideo, fetchPrepTimeline, fetchVideo, fetchVideoClips,
  libraryKeys, purgeVideoAnalysis, resetVideoProbe, retryVideoAnalysis, retryVideoDeident, retryVideoSceneSplit, retryVideoThumbnails, retryVideoTranscode,
  skipVideoDeident, updateVideoMeta,
} from "../api";
import { VideoPlayer } from "../components/VideoPlayer";
import {
  isSummaryBlocked, PrepTimeline, retryFailTitle, retryToast, SUMMARY_CONFIRM, SUMMARY_RETRY_LABEL,
} from "@/features/ingest";
import { ClipSummaryText } from "../components/ClipSummaryText";
import { clipSummary } from "../lib/clip_summary";
import { prepPoll, stageSignature } from "../lib/prep_poll";
import { summaryMark, summaryNotes } from "../lib/prep_summary";
import { clipRangeFormatter } from "../lib/clip_range";
import { stageChip } from "../lib/video_stage";
import { EmptyClips } from "../components/EmptyClips";
import { IdChip } from "@/shared/ui/IdChip";
import { LastUpdated } from "@/shared/ui/LastUpdated";
import { PageHeader } from "@/shared/ui/PageHeader";
import { PageSkeleton } from "@/shared/ui/PageSkeleton";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Button, buttonClasses } from "@/shared/ui/Button";
import { Card, CardBody, CardHeader } from "@/shared/ui/Card";
import { Chip } from "@/shared/ui/Chip";
import { isUuid } from "@/shared/lib/uuid";
import { DatePicker } from "@/shared/ui/DatePicker";
import { ErrorState } from "@/shared/ui/ErrorState";
import { Input } from "@/shared/ui/Input";
import { Label } from "@/shared/ui/Label";
import { Modal } from "@/shared/ui/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/shared/ui/Toast";
import { errorText } from "@/shared/api/error_text";
import { useAnalysisStack } from "@/hooks/useAnalysisStack";
import { useViewerIsAdmin } from "@/lib/anon";
import type { ApiError, ClipDTO, VideoDTO } from "@/shared/api/client";
import { cn } from "@/shared/lib/cn";
import { fmtBitRate, fmtBytes, fmtContainer } from "@/shared/lib/fmtBytes";
import { RUN_STAGE_LABEL, type PrepTimelineEntry } from "@/features/ingest";
import { fmtSecs, fmtSpan } from "@/shared/lib/fmtSecs";
import { RIGHTS_LABEL } from "@/lib/vocab";
import {
  airedAtFromInput, airedAtToInput, VIDEO_META_FIELDS, VIDEO_META_NAME, type VideoMetaKey,
} from "@/lib/video_meta";
import { ProjectShellState, useProjects } from "@/features/projects";

/**
 * 썸네일 실패 사유(`thumbnail_error`)를 읽는 클립 행.
 */
type ClipRow = ClipDTO & { thumbnail_error?: string | null };

/** 클립 행에 상태 배지를 붙이는 클립 상태. 작업을 멈춘 비식별화 신고와 제외만 해당한다. */
const EXCEPTION_RIGHTS: ReadonlySet<string> = new Set(["legal_review", "rejected"]);

/** 실패 토스트 문구를 만든다. 원인이 확인된 오류만 원인을 표시한다. */
function failText(e: unknown, what: string) {
  return errorText((e as ApiError | null)?.detail, { message: what });
}

/**
 * 플레이어 위치에 그릴 내용을 준비 타임라인의 상태로 정한다.
 *
 * 재생본 단계가 실행 중이거나 대기 중이면 중립적인 대기 표시를 그린다.
 * 앞 단계가 실패했으면 준비 중이 아니므로 재생을 시도하고, 재생할 수 없으면 플레이어가 사유를 표시한다.
 * `upload_status`만 보고 플레이어를 그리면 재생본이 확정되기 전(변환·비식별화·재생본 변환이 도는 수십 분)에
 * 409 오류 상자가 떠 고장인지 대기인지 구분되지 않는다.
 */
function playbackPending(entries: PrepTimelineEntry[]): PrepTimelineEntry | "waiting" | null {
  const by = new Map(entries.map((e) => [e.stage, e]));
  const publish = by.get("publish");
  if (!publish || (publish.status !== "running" && publish.status !== "pending")) return null;
  const chain = ["upload", "probe", "transcode", "deident", "publish"] as const;
  if (chain.some((st) => by.get(st)?.status === "failed")) return null;
  // 지금 인코딩 중인 단계. 진행률 표시에 쓴다. 없으면 앞 단계를 기다리는 중이다.
  return ["transcode", "deident", "publish"]
    .map((st) => by.get(st as PrepTimelineEntry["stage"]))
    .find((e) => e?.status === "running") ?? "waiting";
}

/** 규격 검사 결과. `VideoDTO.metadata["probe"]`에 담겨 온다. */
type ProbeDetail = {
  violations?: string[];
  error?: string;
  size_bytes?: number | null;
  duration_seconds?: number | null;
  video_codec?: string | null;
  format_name?: string | null;
  height?: number | null;
};

/**
 * 규격 위반 코드를 관리자가 읽을 수 있는 문구로 바꾼다.
 *
 * 서버는 `size_exceeds_limit (12345 > 10737418240)` 형식으로 주지만, 괄호 안을 파싱하지 않고
 * probe 메타의 구조화된 값을 써서 문자열 형식이 바뀌어도 깨지지 않게 한다.
 * 표에 없는 코드는 원문 그대로 두어 새 규칙도 화면에 나타나게 한다.
 * 코드 목록은 서버 `services/media/probe.check_spec`와 검은 화면 판정(`black_video`)이다.
 */
function violationLabel(code: string, p: ProbeDetail): string {
  if (code.startsWith("size_exceeds_limit")) {
    return `용량 초과 (${p.size_bytes != null ? (p.size_bytes / 1024 ** 3).toFixed(1) : "?"}GB)`;
  }
  if (code.startsWith("duration_exceeds_limit")) {
    return `길이 초과 (${p.duration_seconds != null ? (p.duration_seconds / 3600).toFixed(1) : "?"}시간)`;
  }
  if (code.startsWith("codec_not_allowed")) return `미지원 코덱 (${p.video_codec ?? "?"})`;
  if (code.startsWith("container_not_allowed")) {
    return `미지원 컨테이너 (${fmtContainer(p.format_name ?? undefined) ?? "?"})`;
  }
  if (code.startsWith("resolution_too_high")) return `해상도 초과 (${p.height ?? "?"}p)`;
  if (code.startsWith("resolution_too_low")) return `해상도 미달 (${p.height ?? "?"}p)`;
  if (code.startsWith("black_video")) return "영상 전체가 검은 화면";
  return code;
}

/**
 * 영상 상세 페이지를 그린다. 라우트 전용 페이지다(`p/:slug/videos/:videoId`).
 */
export function VideoDetailPage() {
  const { hash } = useLocation();
  // `#prep` 해시로 들어오면 그 위치로 이동한다. React Router는 해시 스크롤을 하지 않으므로 마운트 때 한 번 이동한다.
  useEffect(() => {
    if (hash === "#prep") document.getElementById("prep")?.scrollIntoView({ block: "start" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const params = useParams<{ slug: string; videoId: string }>();
  const slug = params.slug;
  /*
    형식이 틀린 영상 ID(`not-a-uuid`)는 조회하지 않고 없는 영상으로 그린다.
    조회하면 서버가 422를 돌려줘, 같은 없는 주소가 형식에 따라 다른 문구로 표시된다.
  */
  const badId = !!params.videoId && !isUuid(params.videoId);
  const videoId = badId ? undefined : params.videoId;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [deleteOpen, setDeleteOpen] = useState(false);
  /** 영상 메타 편집 창을 열었으면 true. */
  const [metaOpen, setMetaOpen] = useState(false);
  /** 12행을 넘는 클립 목록을 펼쳤으면 true. */
  const [clipsExpanded, setClipsExpanded] = useState(false);
  const projects = useProjects();
  const project = projects.data?.find((p) => p.slug === slug);
  /*
    관리자 판정은 `useViewerIsAdmin`(super_admin 또는 project_admin) 한 곳에서 한다.
    `my_role === "project_admin"`만 보면 프로젝트 멤버가 아닌 super_admin에게 삭제·재검사·정리·재시도가 숨겨진다.
  */
  const isAdmin = useViewerIsAdmin();
  /* 없는 영상 화면의 콘텐츠 목록 버튼은 라우터 가드와 같은 표(`lib/roles`의 `ROUTE_ACCESS`)로 정한다. */
  const canOpenContent = useCanOpen("content", project);
  /**
   * 확인 창 둘. 분석 재요청은 비용이 다시 들고, 비식별화 건너뛰기는 가리지 않은 원본으로 다음 단계를 진행한다.
   * 장면 요약을 받지 못한 영상은 같은 재요청 창이 요약 재요청 문구로 열린다.
   */
  const [analysisConfirmOpen, setAnalysisConfirmOpen] = useState(false);
  const [deidentSkipOpen, setDeidentSkipOpen] = useState(false);
  /** 분석본 정리 확인 창을 열었으면 true. */
  const [purgeOpen, setPurgeOpen] = useState(false);
  /*
    분석 비용이 없는 스택이면 true. 샷 검출처럼 외부 분석 서비스(`usesVendorAnalyzer`)를 쓰지 않는 스택은 비용이 없다.
    등록 화면의 분석 요청 창과 같은 판정이다(`IngestPage`의 `costFree`).
  */
  const stack = useAnalysisStack(project);
  const costFree = !!stack?.adapterKey && !stack.usesVendorAnalyzer;
  /*
    영상 메타 네 값(방송사·프로그램명·장르·방영일)을 고칠 수 있으면 참. 서버 `update`와 같은 기준(관리자)이다.
    네 값은 이 영상의 모든 클립이 물려받는다.
  */
  const canEditMeta = isAdmin;

  /**
   * 영상 메타를 저장한다. 바뀐 필드만 보낸다.
   * 클립 조회가 물려받은 네 값을 함께 실으므로 클립 조회도 다시 받는다.
   */
  const metaMut = useMutation<VideoDTO, ApiError, Record<string, string | null>>({
    mutationFn: (body) => updateVideoMeta(videoId, body),
    onSuccess: () => {
      setMetaOpen(false);
      toast("success", "영상 메타를 저장했습니다", "이 영상의 모든 클립에 같은 값이 쓰입니다.");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.clipAll() });
    },
    onError: (e) => {
      const t = failText(e, "영상 메타 저장 실패");
      toast("error", t.message, e.fields?.[0]?.message ?? t.hint);
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteVideo(videoId),
    onSuccess: () => {
      toast("success", "영상이 삭제되었습니다");
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      navigate(`/p/${slug}/content`);
    },
    onError: (e) => {
      const t = failText(e, "삭제 실패");
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 분석본을 정리한다. 원본이 있으면 분석본은 다시 만들 수 있는 사본이다.
   * 정리할 수 없으면 서버가 409와 사유 문장을 돌려주고 화면은 그 문장을 그대로 표시한다.
   * 원본 유무와 분석 상태는 서버만 판정한다.
   */
  const purgeMut = useMutation<{ freed_bytes: number }, ApiError>({
    mutationFn: () => purgeVideoAnalysis(videoId),
    onSuccess: (r) => {
      setPurgeOpen(false);
      toast("success", "분석본을 정리했습니다", `${fmtBytes(r.freed_bytes)} 확보`);
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    onError: (e) => {
      setPurgeOpen(false);
      const t = failText(e, "정리하지 못했습니다");
      toast("warning", t.message, t.hint);
    },
  });

  // 멈춘 규격 검사(error/rejected)를 pending으로 되돌린다. 다음 배치(1분)가 다시 검사한다.
  const resetProbeMut = useMutation({
    mutationFn: () => resetVideoProbe(project!.project_id, videoId),
    onSuccess: () => {
      toast("success", "규격 재검사를 요청했습니다", "잠시 후 반영됩니다");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    // 409 `not_resettable`은 오류 사전이 문구로 바꾼다.
    onError: (e) => {
      const t = failText(e, "재검사 요청 실패");
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 변환 재시도. 재시도 상한에 걸려 멈춘 변환을 되살린다.
   * 규격 재검사와 경로가 다르다. 변환만 실패한 영상은 규격을 통과했으므로 `probe/reset`이 409를 돌려준다.
   */
  const retryTranscodeMut = useMutation({
    mutationFn: () => retryVideoTranscode(project!.project_id, videoId),
    onSuccess: () => {
      toast("success", "변환을 다시 요청했습니다", "잠시 후 반영됩니다");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    onError: (e) => {
      const t = failText(e, "변환 재시도 실패");
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 썸네일 다시 만들기. 재시도 상한에 걸려 멈춘 대표 이미지·클립 썸네일의 실패 표시를 지운다.
   * 요청 안에서 ffmpeg를 실행하지 않고, 다음 주기(1분)의 워커가 다시 처리한다.
   */
  const retryThumbsMut = useMutation({
    mutationFn: () => retryVideoThumbnails(project!.project_id, videoId),
    onSuccess: () => {
      toast("success", "썸네일을 다시 만듭니다", "몇 분 안에 반영됩니다");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videoClips(project?.project_id, videoId) });
    },
    onError: (e) => {
      const t = failText(e, "썸네일 재생성 실패");
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 비식별화 재시도. 실패한 비식별화만 다시 요청한다.
   * 완료된 비식별화는 마스킹본이 이미 원본 자리를 대체해서 다시 돌리면 가린 영역을 또 가리므로 서버가 409로 막는다.
   */
  const retryDeidentMut = useMutation({
    mutationFn: () => retryVideoDeident(project!.project_id, videoId),
    onSuccess: () => {
      toast("success", "비식별화를 다시 요청했습니다", "잠시 후 반영됩니다");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    onError: (e) => {
      const t = errorText((e as { detail?: string } | null)?.detail,
                          { message: "비식별화 재시도 실패" });
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 비식별화 건너뛰기. 비식별화를 끈 프로젝트에서 실패로 확정된 영상을 재생본 단계로 넘긴다.
   * 켠 프로젝트와 실패가 아닌 영상은 서버가 409로 막는다. 누가 넘겼는지는 감사 기록에 남는다.
   */
  const skipDeidentMut = useMutation({
    mutationFn: () => skipVideoDeident(project!.project_id, videoId),
    onSuccess: () => {
      setDeidentSkipOpen(false);
      toast("success", "비식별화를 건너뛰었습니다", "재생본을 만듭니다.");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    onError: (e) => {
      const t = errorText((e as { detail?: string } | null)?.detail,
                          { message: "비식별화 건너뛰기 실패" });
      toast("error", t.message, t.hint);
    },
  });

  /**
   * 장면 분할 재시도. 실패한 장면 분할을 다음 투입 주기(1분)에 다시 실행하게 한다.
   * 클립이 이미 있는 영상은 다시 나누면 클립이 중복되므로 서버가 409로 막는다.
   */
  const retrySceneSplitMut = useMutation({
    mutationFn: () => retryVideoSceneSplit(project!.project_id, videoId),
    onSuccess: () => {
      toast("success", "장면 분할을 다시 요청했습니다", "1분 안에 다시 나눕니다");
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
    },
    onError: (e) => {
      const t = errorText((e as { detail?: string } | null)?.detail,
                          { message: "장면 분할 재시도 실패" });
      toast("error", t.message, t.hint);
    },
  });

  /** 준비 타임라인. 진행 중인 단계가 있을 때만 폴링한다. */
  const timeline = useQuery({
    queryKey: libraryKeys.prepTimeline(videoId),
    queryFn: () => fetchPrepTimeline(videoId),
    enabled: !!videoId,
    /*
      진행 중이거나 대기인 단계가 남아 있으면 다시 조회한다(`prepPoll`).
      대기를 빼면 분석 요청을 눌러도 화면이 바뀌지 않는다.
    */
    refetchInterval: (q) => prepPoll(q.state.data?.entries ?? []).timeline,
    // 실패는 준비 진행 위치에 그린다. 토스트를 띄우면 없는 영상일 때 본문의 `영상 없음`과 같은 내용이 두 번 뜬다.
    meta: { handled: true },
  });
  /** 준비가 아직 진행 중이면 true. 준비가 막 끝난 순간을 잡아 재생 주소를 다시 받는 데 쓴다. */
  const prepRunning = (timeline.data?.entries ?? []).some(
    (e) => e.status === "running" || e.status === "pending",
  );
  /* 영상·클립의 다시 조회 간격. 타임라인보다 길게 둔다(`prepPoll`). */
  const poll = prepPoll(timeline.data?.entries ?? []).detail;
  /*
    준비가 막 끝났으면 재생 주소를 다시 받는다. 재생 주소는 50분 주기로만 다시 받으므로,
    그대로 두면 재생본이 확정돼도 `다시 시도`를 누르기 전까지 이전 응답(409)이 남는다.
    주의: 훅 개수가 렌더마다 같아야 하므로 조기 반환보다 위에 둔다.
  */
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !prepRunning) {
      void qc.invalidateQueries({ queryKey: libraryKeys.videoSource(videoId) });
    }
    wasRunning.current = prepRunning;
  }, [prepRunning, qc, videoId]);
  /*
    단계가 넘어가면 영상·클립을 바로 다시 조회한다(`stageSignature`).
    영상·클립은 타임라인보다 간격이 길어서, 그대로 두면 타임라인은 다음 단계인데 머리 칩은 이전 단계로 남는다.
    처음 받은 값은 비교할 이전 값이 없어 건너뛴다.
  */
  const signature = stageSignature(timeline.data?.entries ?? []);
  const lastSignature = useRef<string | null>(null);
  useEffect(() => {
    if (!timeline.data) return;
    if (lastSignature.current !== null && lastSignature.current !== signature) {
      void qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      void qc.invalidateQueries({ queryKey: libraryKeys.videoClips(project?.project_id, videoId) });
    }
    lastSignature.current = signature;
  }, [signature, timeline.data, qc, videoId, project?.project_id]);

  /*
    영상·클립의 실패는 이 화면이 제자리에 표시하므로 전역 토스트를 끈다(`meta.handled`).
  */
  const video = useQuery({
    queryKey: libraryKeys.video(videoId),
    queryFn: () => fetchVideo(videoId),
    enabled: !!videoId,
    refetchInterval: poll,
    meta: { handled: true },
  });

  /* 타임라인의 규격 위반 코드(`codec_not_allowed (av1)`)를 관리자가 읽을 수 있는 문구로 바꾼다. 분류는 서버가, 문구는 화면이 정한다.
     주의: `video` 선언 아래에 둔다. 이 `.map`은 즉시 실행되며 `video.data`를 읽으므로, 위에 두면 probe 오류가 있는 영상에서만
     TDZ(`Cannot access 'video' before initialization`)로 화면 전체가 멈춘다. */
  const entries = (timeline.data?.entries ?? []).map((e) => {
    if (e.stage !== "probe" || !e.error) return e;
    const probeMeta = (video.data?.metadata as { probe?: ProbeDetail } | null)?.probe;
    if (!probeMeta) return e;
    const human = e.error
      .replace(/^규격 위반:\s*/, "")
      .split(";")
      .map((c) => violationLabel(c.trim(), probeMeta))
      .join(" · ");
    return { ...e, error: human };
  });
  // 빈 상태의 구간 추가 창이 위 플레이어의 재생 위치를 읽는다. 경계는 영상을 보면서 정한다.
  // 주의: 조기 반환보다 위에 둔다. 아래에 두면 로딩·404 반환 때문에 렌더마다 훅 개수가 달라져
  // 런타임 오류("Rendered more hooks…")가 난다. `tsc`는 이 오류를 잡지 못한다.
  const playerRef = useRef<MediaPlayerInstance>(null);

  const clips = useQuery({
    queryKey: libraryKeys.videoClips(project?.project_id, videoId),
    queryFn: () => fetchVideoClips(project!.project_id, videoId),
    enabled: !!project && !!videoId,
    refetchInterval: poll,
    meta: { handled: true },
  });
  /**
   * 분석 재시도. 실패한 분석 잡을 다시 요청한다.
   *
   * 자산이 있으면 재등록 없이 분석만 다시 요청한다(서버가 경로를 정한다).
   * 분석은 영상 길이에 비례해 과금되므로 자동으로 하지 않고, 확인 창(`analysisConfirmOpen`)이 비용을 먼저 알린다.
   * 장면 요약을 받지 못한 영상은 서버가 요약만 다시 요청하고, 결과 토스트는 서버가 처리한 방식대로 쓴다.
   */
  const retryAnalysis = useMutation({
    mutationFn: () => retryVideoAnalysis(video.data!.project_id, videoId),
    onSuccess: (r) => {
      setAnalysisConfirmOpen(false);
      qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
      qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
      const t = retryToast(r, 1);
      toast(t.tone, t.title, t.detail);
    },
    onError: (e) => {
      setAnalysisConfirmOpen(false);
      // 실패 제목은 누른 버튼의 이름을 따른다. 요약을 받지 못한 영상의 버튼은 요약 재요청이다.
      const t = failText(e, retryFailTitle(!!video.data && isSummaryBlocked(video.data)));
      toast("error", t.message, t.hint);
    },
  });

  if (projects.isLoading || video.isLoading) return <PageSkeleton panels={2} />;
  // 프로젝트가 없는 경우와 불러오지 못한 경우를 구분한다. 직접 그리면 목록 조회가 실패해도 없는 프로젝트로 표시된다.
  if (!project) return <ProjectShellState isError={projects.isError} onRetry={() => void projects.refetch()} />;
  /*
    주소의 프로젝트와 영상의 프로젝트가 다르면 이 프로젝트에 없는 영상으로 다룬다.
    영상 조회(`/videos/{id}`)는 프로젝트를 받지 않아, 두 프로젝트의 관리자에게는 다른 프로젝트의 영상도 열린다.
    그대로 그리면 머리는 지금 프로젝트인데 재생·메타·삭제는 다른 프로젝트의 영상에 적용된다.
    영상이 속한 프로젝트를 열 수 있으면 그 프로젝트로 가는 링크를 준다.
  */
  const home = video.data && video.data.project_id !== project.project_id
    ? projects.data?.find((p) => p.project_id === video.data!.project_id) ?? null
    : undefined;
  if (home !== undefined) {
    return (
      <div>
        <ErrorState message="영상 없음" description="이 프로젝트에 없는 영상입니다." />
        {home && (
          <div className="-mt-8 flex justify-center pb-12">
            <Link to={`/p/${home.slug}/videos/${videoId}`} className={buttonClasses("secondary", "sm")}>
              {home.name}에서 열기
            </Link>
          </div>
        )}
      </div>
    );
  }
  if (video.error || !video.data) {
    /* 열 수 없는 이유(삭제·권한·일시 오류)는 오류 사전이 문구로 정한다. 제목은 명사구이고 원인은 설명 줄에 둔다.
       다시 해도 같은 실패(403·404)에는 `다시 시도`를 주지 않는다(`ErrorState`의 `error`).
       없는 영상이면 콘텐츠 목록으로 가는 링크를 주고, 목록을 열 수 있는 관리자에게만 그린다. */
    const notFound = badId || (video.error as ApiError | null)?.status === 404;
    const t = failText(video.error, "영상 조회 실패");
    const known = t.message !== "영상 조회 실패";
    return (
      <div>
        <ErrorState
          message={notFound ? "영상 없음" : "영상 조회 실패"}
          description={
            notFound
              ? "삭제되었거나 주소가 잘못된 영상입니다."
              : [known ? `${t.message}.` : null, t.hint].filter(Boolean).join(" ") || undefined
          }
          error={video.error ?? undefined}
          // 형식이 틀린 주소는 조회하지 않았고 다시 해도 결과가 같아서 다시 시도를 두지 않는다.
          onRetry={badId ? undefined : () => void video.refetch()}
        />
        {notFound && canOpenContent && (
          <div className="-mt-8 flex justify-center pb-12">
            <Link to={`/p/${slug}/content`} className={buttonClasses("secondary", "sm")}>
              콘텐츠 목록
            </Link>
          </div>
        )}
      </div>
    );
  }

  const v = video.data;
  const clipRows: ClipRow[] = clips.data ?? [];
  /**
   * 재생본 준비 상태. 진행 중인 단계나 `waiting`이면 대기 표시를 그리고, `null`이면 플레이어를 그린다.
   * 타임라인을 받기 전에는 판정할 수 없어서 그동안은 문구 없이 로딩 표시만 한다.
   */
  const pendingPlayback = timeline.data ? playbackPending(entries) : null;
  const timelineLoading = !timeline.data && !timeline.isError;
  /** 비식별화 건너뛰기 창에 싣는 실패한 비식별화 줄. 서버가 건너뛸 수 있다고 연 줄만 싣는다. */
  const deidentSkipEntry = entries.find((e) => e.stage === "deident" && e.skippable) ?? null;
  /**
   * 장면 요약을 받지 못한 영상이면 true. 판정은 서버(`prep_blocked_stage`)가 한다.
   * 재요청 창과 요약 줄의 버튼이 요약 재요청 문구로 바뀐다.
   */
  const summaryOnly = isSummaryBlocked(v);
  /** 분석 재요청 창에 싣는 지난 실패. 등록·분석·요약 중 실패한 줄이고, 사유가 없으면 싣지 않는다. */
  const lastAnalysisFail = entries.find(
    (e) => (e.stage === "register" || e.stage === "analyze" || e.stage === "summarize")
      && e.status === "failed" && e.error,
  ) ?? null;
  /** 목록 구간 서식. 가장 늦은 끝으로 형식을 한 번 정한다. */
  const fmtRange = clipRangeFormatter(Math.max(0, ...clipRows.map((c) => c.end_seconds)));
  /** 머리의 준비 단계 칩. 서버가 단계를 계산하지 않았으면 `null`이다. */
  const stage = stageChip(v);
  /**
   * 규격 검사 상세. 등록 직후 ffprobe가 뽑은 값과 위반 사유·오류가 들어 있다.
   * 화면은 이 값 하나로 파일 규격과 막힌 이유를 보여 준다.
   */
  const probe = (v?.metadata?.probe ?? null) as {
    width?: number; height?: number; video_codec?: string; fps?: number;
    format_name?: string; major_brand?: string; size_bytes?: number; duration_seconds?: number;
    bit_rate?: number;
    violations?: string[]; transcode_redeemed?: string[]; error?: string;
  } | null;

  /** 파일에 딸린 사실. 서버가 `extra`에 남기는 값들이다(원래 파일 이름·중복·체크섬·썸네일). */
  const extra = (v.metadata ?? {}) as {
    original_filename?: string;
    duplicate_of?: string; duplicate_title?: string;
    checksum_error?: string; checksum_failures?: number;
    poster_failed?: string;
  };
  const failedThumbs = clipRows.filter((c) => c.thumbnail_error).length;
  const fileNotes: {
    key: string; tone: "warn" | "bad"; icon: ReactNode; label: string;
    link?: ReactNode; detail?: string; action?: ReactNode;
  }[] = [];
  if (extra.duplicate_of) {
    fileNotes.push({
      key: "dup", tone: "warn", icon: <Copy size={12} aria-hidden="true" />,
      label: "같은 내용의 영상이 이미 있습니다 ·",
      link: (
        <Link to={`/p/${slug}/videos/${extra.duplicate_of}`} className="underline-offset-2 hover:underline">
          기존 영상 보기
        </Link>
      ),
      detail: extra.duplicate_title,
    });
  }
  if (extra.checksum_error) {
    const settled = (extra.checksum_failures ?? 0) >= 3;
    fileNotes.push({
      key: "checksum", tone: settled ? "bad" : "warn", icon: null,
      label: settled ? "원본 체크섬 계산 실패" : "원본 체크섬 계산 재시도 중",
      // 서버가 관리자용 문구로 남긴 사유다(`checksum.ERROR_KEY`, `operator_text`).
      detail: extra.checksum_error,
    });
  }
  if (extra.poster_failed === "true" || failedThumbs > 0) {
    const what = [
      extra.poster_failed === "true" ? "대표 이미지" : null,
      // 클립 단위는 `개`다.
      failedThumbs > 0 ? `클립 ${failedThumbs.toLocaleString("ko-KR")}개` : null,
    ].filter(Boolean).join(" · ");
    fileNotes.push({
      key: "thumbs", tone: "bad", icon: null,
      label: `썸네일 생성 실패 · ${what}`,
      // 사유는 서버가 화면 문구로 바꿔 준 클립의 사유만 싣는다.
      // 포스터의 원문(`poster_error`)은 ffmpeg 출력일 수 있어 싣지 않고, 타임라인의 업로드 항목이 보여 준다.
      detail: clipRows.find((c) => c.thumbnail_error)?.thumbnail_error ?? undefined,
      action: isAdmin ? (
        <button
          type="button"
          disabled={retryThumbsMut.isPending}
          onClick={() => retryThumbsMut.mutate()}
          className="mt-1 inline-flex items-center gap-1 rounded-control border border-separator px-2 py-0.5 text-caption font-medium text-label-2 transition-colors hover:bg-fill disabled:opacity-50"
        >
          <RotateCw size={12} aria-hidden="true" /> 다시 만들기
        </button>
      ) : undefined,
    });
  }
  /**
   * 관리자이고 분석본 사본이 기록된 영상이면 true. 분석본 정리 버튼을 보인다.
   * 원본이 없거나 분석이 끝나지 않은 경우는 서버가 정리 요청을 거절하고 사유를 돌려준다.
   */
  const canPurgeAnalysis = isAdmin && v.upload_status === "uploaded" && v.has_analysis_copy;
  /** 사이드바 파일 묶음에 보일 값이 있으면 true. */
  const hasFileGroup = !!(probe || fileNotes.length > 0 || extra.original_filename || canPurgeAnalysis);

  /** 준비가 막힌 영상이면 true. 준비 진행을 플레이어 위로 올리고 실패 사유를 펼친 채 연다. */
  const blocked = v.prep_stage === "blocked";
  /** 준비 단계가 모두 끝났으면(완료 또는 건너뜀) true. 끝났으면 준비 진행을 한 줄로 접는다. */
  const prepDone =
    entries.length > 0 && entries.every((e) => e.status === "ok" || e.status === "skipped");
  /** 클립 목록에서 처음에 보이는 행 수. 넘으면 접고 `나머지 N개 펼치기`를 둔다. */
  const CLIP_FOLD = 12;
  const shownClips = clipsExpanded ? clipRows : clipRows.slice(0, CLIP_FOLD);
  const hiddenClips = clipRows.length - shownClips.length;

  /** 머리 아래 사실 줄. 이름 붙은 값 넷이고, 값이 없으면 `—`로 둔다. */
  const facts: [string, string | null][] = [
    [v.broadcaster && v.episode ? "방송사 · 회차" : "방송사", [v.broadcaster, v.episode].filter(Boolean).join(" ") || null],
    ["길이", v.duration_seconds ? fmtSecs(v.duration_seconds) : null],
    /* 로딩 중에는 `—`를 쓴다. 0이 잠깐 보이면 "클립이 없다"는 잘못된 정보가 된다. */
    ["클립", clips.isSuccess ? `${clipRows.length.toLocaleString("ko-KR")}개` : null],
    ["용량", probe?.size_bytes != null ? fmtBytes(probe.size_bytes) : null],
  ];

  const prepCard = timeline.isError ? (
    <ErrorState inline message="준비 진행 조회 실패" onRetry={() => void timeline.refetch()} />
  ) : prepDone && !blocked ? (
    <PrepSummary entries={entries} />
  ) : (
    <PrepTimeline
      entries={entries}
      openFailed={blocked}
      retryLabels={summaryOnly ? { summarize: SUMMARY_RETRY_LABEL } : undefined}
      /* 단계마다 다시 시도하는 요청이 다르다. `probe`는 규격 재검사, `deident`는 비식별화 재시도,
         `transcode`·`publish`는 변환 재시도(서버가 멈춘 분석본·재생본 변환을 되살린다), `scene_split`은 장면 분할 재시도다.
         `register`·`analyze`·`summarize`는 분석 재요청이고, 요약을 받지 못한 영상이면 서버가 요약만 다시 요청한다. */
      onRetry={isAdmin
        ? (stage) => {
            // 분석은 비용이 다시 들어 확인 창을 거친다. 등록에서 멈춘 영상은 같은 요청으로 서버가 등록부터 다시 한다.
            if (stage === "register" || stage === "analyze" || stage === "summarize") {
              return setAnalysisConfirmOpen(true);
            }
            if (stage === "deident") return retryDeidentMut.mutate();
            if (stage === "scene_split") return retrySceneSplitMut.mutate();
            if (stage === "transcode" || stage === "publish") return retryTranscodeMut.mutate();
            return resetProbeMut.mutate();
          }
        : undefined}
      /* 건너뛰기는 서버가 `skippable`로 허용한 줄(비식별화를 끈 프로젝트의 실패)에만 뜬다. 확인 창을 거친다. */
      onSkip={isAdmin ? () => setDeidentSkipOpen(true) : undefined}
      /* 분석 재요청도 포함한다. 처리 중에 다시 눌러 비용이 두 번 들지 않게 한다. */
      retryBusy={
        resetProbeMut.isPending || retryDeidentMut.isPending || retrySceneSplitMut.isPending
        || retryTranscodeMut.isPending || retryAnalysis.isPending || skipDeidentMut.isPending
      }
    />
  );

  /* 플레이어. 재생본이 아직 없으면 오류 상자 대신 중립적인 대기 표시를 그린다. */
  const player = v.upload_status !== "uploaded" || !videoId ? (
    <div className="flex items-center justify-center rounded-card border border-separator bg-panel py-12 text-footnote text-label-3 shadow-e1">
      업로드가 끝나면 재생합니다.
    </div>
  ) : timelineLoading ? (
    <div className="flex aspect-video w-full items-center justify-center rounded-card border border-separator bg-panel shadow-e1">
      <Loader2 size={18} className="animate-spin text-label-3" aria-hidden="true" />
    </div>
  ) : pendingPlayback ? (
    <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-card border border-separator bg-panel px-6 text-center shadow-e1">
      <Loader2 size={18} className="animate-spin text-label-3" aria-hidden="true" />
      <p className="text-callout font-medium text-label-2">재생본 준비 중</p>
      <p className="text-caption tabular-nums text-label-3">
        {pendingPlayback === "waiting"
          ? "앞 단계가 끝나면 재생본을 만듭니다."
          : `${RUN_STAGE_LABEL[pendingPlayback.stage]} ${
              pendingPlayback.progress != null
                ? `${Math.round(pendingPlayback.progress * 100)}%`
                : "진행 중"
            }`}
      </p>
    </div>
  ) : (
    <VideoPlayer videoId={videoId} playerRef={playerRef} compactError hideTitle />
  );

  return (
    <div>
      <VideoMetaModal
        open={metaOpen}
        onClose={() => setMetaOpen(false)}
        video={v}
        clipCount={clips.isSuccess ? clipRows.length : null}
        pending={metaMut.isPending}
        onSave={(body) => metaMut.mutate(body)}
      />

      {/*
        머리. 제목 옆에 준비 단계 칩, 아래에 이름 붙은 사실 넷을 둔다.
        부제에 여러 항목을 나열하면 각 값이 무엇인지 읽히지 않는다. 해상도·코덱은 사이드바 `파일` 묶음에만 둔다.
      */}
      <PageHeader
        title={v.title}
        badge={stage && (blocked ? (
          /* 막혔으면 준비 진행(#prep)으로 연결한다. 막힌 영상은 준비 진행이 바로 아래에 있다. */
          <a href="#prep" className="rounded-capsule" title="준비 진행에서 사유 보기">
            <Chip variant="dot" tone={stage.tone}>{stage.label}</Chip>
          </a>
        ) : (
          <Chip variant="dot" tone={stage.tone}>{stage.label}</Chip>
        ))}
        meta={
          <dl className="mt-2 flex flex-wrap items-stretch gap-y-2 text-label">
            {facts.map(([k, val], i) => (
              <div key={k} className={cn("pr-6", i > 0 && "border-l border-separator pl-6")}>
                <dt className="text-caption text-label-3">{k}</dt>
                <dd className="mt-0.5 text-callout font-semibold tabular-nums">{val ?? "—"}</dd>
              </div>
            ))}
          </dl>
        }
        actions={(
          <>
            {/* 준비가 끝난 영상은 폴링이 멈춰 있어 직접 새로 고치는 버튼이 필요하다. */}
            <LastUpdated
              at={video.dataUpdatedAt}
              refreshing={video.isFetching || timeline.isFetching}
              onRefresh={() => {
                qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
                qc.invalidateQueries({ queryKey: libraryKeys.prepTimeline(videoId) });
                qc.invalidateQueries({ queryKey: libraryKeys.videoClips(project.project_id, videoId) });
              }}
            />
            {isAdmin && (
              /* 페이지 안의 비가역 동작은 위험 테두리 버튼이다. 채운 위험색은 확인 창 마지막 버튼에만 쓴다. */
              <Button size="sm" variant="danger-outline" onClick={() => setDeleteOpen(true)}>
                삭제
              </Button>
            )}
          </>
        )}
      />

      {/* 두 열 레이아웃. 왼쪽은 본문, 오른쪽은 고정(sticky) 사이드바다. */}
      <div className="flex gap-6 lg:items-start">

        {/* ── 왼쪽 본문 ── */}
        <div className="min-w-0 flex-1 space-y-5">

          {/*
            막힌 영상은 멈춘 단계를 확인하려고 이 화면을 열기 때문에 준비 진행을 플레이어보다 위에 둔다.
            그 밖에는 플레이어, 준비 진행 순서다.
          */}
          {blocked && <div id="prep" className="scroll-mt-20">{prepCard}</div>}
          {player}
          {!blocked && <div id="prep" className="scroll-mt-20">{prepCard}</div>}

          {/*
            클립 목록. 12행에서 접어 본문 스크롤이 길어지지 않게 한다.
          */}
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                {/* 클립 수는 머리 사실 줄 한 곳에만 쓴다. 같은 수를 한 화면에 두 번 쓰지 않는다. */}
                <h2 className="text-callout font-semibold text-label">클립</h2>
                {/* `클립 보기`는 영상을 재생하며 클립이 나뉜 방식을 보는 화면으로 간다. */}
                {clipRows.length > 0 && (
                  <span className="flex items-center gap-3">
                    <Link
                      to={`/p/${slug}/videos/${videoId}/clips`}
                      className="inline-flex items-center gap-0.5 text-caption font-medium text-label hover:underline"
                    >
                      클립 보기
                      <ChevronRight size={12} aria-hidden="true" />
                    </Link>
                  </span>
                )}
              </div>
            </CardHeader>
            <CardBody>
              {clips.isLoading ? (
                <RowsLoading rows={5} label="클립 불러오는 중" />
              ) : clips.isError ? (
                /* 조회가 실패하면 빈 상태 대신 오류를 그린다. */
                <ErrorState inline message="클립 조회 실패" onRetry={() => void clips.refetch()} />
              ) : clipRows.length === 0 ? (
                <EmptyClips
                  projectId={project.project_id}
                  videoId={videoId!}
                  slug={slug}
                  stage={v}
                  sceneSplit={(timeline.data?.entries ?? []).find((e) => e.stage === "scene_split")}
                  playerRef={playerRef}
                  canAdd={isAdmin}
                  minSeconds={project.clip_min_seconds}
                  onAdded={() => {
                    /*
                      첫 클립이 생기면 서버가 영상 단위 분석 요청 티켓을 지우고 영상의 단계도 바뀐다.
                      그래서 클립 목록과 함께 영상·영상 목록도 다시 받는다.
                    */
                    void qc.invalidateQueries({ queryKey: libraryKeys.videoClips(project.project_id, videoId) });
                    void qc.invalidateQueries({ queryKey: libraryKeys.video(videoId) });
                    void qc.invalidateQueries({ queryKey: libraryKeys.videosAll() });
                  }}
                />
              ) : (
                <>
                  {/*
                    클립 행. 번호로 위치를 표시하고, 구간은 고정폭으로 세로 정렬한다.
                    상태 배지는 예외(비식별화 신고, 제외)인 클립에만 둔다. 정상 클립은 모두 같은 값이라 배지를 두지 않는다.
                  */}
                  <ul className="-mx-2 divide-y divide-separator text-footnote">
                    {shownClips.map((c, i) => (
                      <li
                        key={c.clip_id}
                        className="flex items-start gap-3 rounded-control px-2 py-2.5 transition-colors hover:bg-fill"
                      >
                        <span className="w-6 shrink-0 pt-0.5 text-right text-caption tabular-nums text-label-3">
                          {i + 1}
                        </span>
                        <span className="w-28 shrink-0 pt-0.5 font-mono text-caption tabular-nums text-label-3">
                          {fmtRange(c.start_seconds, c.end_seconds)}
                        </span>
                        {/*
                          한 줄 요약은 클립 보기와 같은 규칙으로 고른다(`ClipSummaryText`).
                          장면 요약은 스무 줄을 넘기도 해서 두 줄에서 자른다. 전문은 `title`과 클립 보기에서 읽는다.
                        */}
                        <span className="line-clamp-2 min-w-0 flex-1 text-label" title={clipSummary(c)?.text}>
                          <ClipSummaryText clip={c} />
                        </span>
                        <span className="w-24 shrink-0">
                          {EXCEPTION_RIGHTS.has(c.rights_status) && (
                            <StatusBadge status={c.rights_status} label={RIGHTS_LABEL[c.rights_status] ?? c.rights_status} />
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {(hiddenClips > 0 || clipsExpanded) && clipRows.length > CLIP_FOLD && (
                    <div className="-mx-4 mt-2 border-t border-separator pt-3 text-center">
                      <button
                        type="button"
                        onClick={() => setClipsExpanded((o) => !o)}
                        aria-expanded={clipsExpanded}
                        className="inline-flex items-center gap-1 rounded-control px-2 py-1 text-caption font-medium text-label-2 transition-colors hover:bg-fill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                      >
                        {clipsExpanded ? (
                          <><ChevronUp size={14} aria-hidden="true" /> 접기</>
                        ) : (
                          <><ChevronDown size={14} aria-hidden="true" /> 나머지 {hiddenClips.toLocaleString("ko-KR")}개 펼치기</>
                        )}
                      </button>
                    </div>
                  )}
                </>
              )}
            </CardBody>
          </Card>
        </div>

        {/*
          오른쪽 고정 사이드바. 상단바 높이(2.75rem) 아래에 붙고, 뷰포트보다 길면 안에서 스크롤한다.
          `sticky`만 두면 긴 사이드바의 아래쪽까지 스크롤할 수 없다.
        */}
        <aside className="hidden w-80 shrink-0 flex-col gap-4 lg:sticky lg:top-[calc(2.75rem+1rem)] lg:flex lg:max-h-[calc(100vh-2.75rem-2rem)] lg:overflow-y-auto lg:pr-1">

          {/*
            메타데이터. 값을 채우는 주체에 따라 영상 메타(사람이 입력), 등록 정보, 파일(probe가 기록) 묶음으로 나눈다.
          */}
          <div className="shrink-0 rounded-card border border-separator bg-panel shadow-e1">
            <MetaGroup
              title="영상 메타"
              action={
                canEditMeta ? (
                  <button
                    type="button"
                    onClick={() => setMetaOpen(true)}
                    aria-label="영상 메타 편집"
                    className="inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 text-caption font-medium text-label transition-colors hover:bg-fill"
                  >
                    <Pencil size={12} aria-hidden="true" /> 편집
                  </button>
                ) : undefined
              }
            >
              {/* 방송사·프로그램명·장르·방영일은 이 영상의 클립이 물려받는 네 값이다. 원문은 `title`로 남긴다. */}
              <MetaRow k="프로그램명" val={v.program_name} title={v.program_name} />
              <MetaRow k="방송사" val={v.broadcaster} title={v.broadcaster} />
              <MetaRow k="장르" val={v.genre} title={v.genre} />
              <MetaRow k="방영일" val={airedAtToInput(v.aired_at) || null} />
            </MetaGroup>

            {/* 편집 창이 고치지 않는 값은 따로 둔다. */}
            <MetaGroup title="등록 정보" last={!hasFileGroup}>
              <MetaRow k="회차" val={v.episode} title={v.episode} />
              <MetaRow k="등록일" val={airedAtToInput(v.created_at) || null} />
              {/* 납품 패키지의 `source_video_id`와 같은 값이다. 받은 JSON에서 이 영상을 찾을 때 대조한다. */}
              <MetaRow k="영상 ID" val={<IdChip id={v.video_id} label="영상 ID" className="-mr-1" />} />
            </MetaGroup>

            {/*
              파일 규격. 등록 직후 ffprobe가 뽑아 `metadata.probe`에 넣어 둔 값이다.
              보일 값이 없으면(`hasFileGroup`) 묶음을 그리지 않는다. 용량은 머리 사실 줄에 있어 되풀이하지 않는다.
            */}
            {hasFileGroup && (
              <MetaGroup title="파일" last>
                {/* 등록할 때의 파일 이름. 제목은 고칠 수 있어서 원본 파일을 찾는 기준은 이 이름이다. 긴 이름은 `title`로 원문을 남긴다. */}
                {extra.original_filename && (
                  <MetaRow k="원본 파일" val={extra.original_filename} title={extra.original_filename} />
                )}
                {probe && (
                  <>
                    <MetaRow k="해상도" val={probe.width && probe.height ? `${probe.width}×${probe.height}` : null} />
                    <MetaRow k="코덱" val={probe.video_codec} />
                    <MetaRow k="FPS" val={probe.fps != null ? `${Math.round(probe.fps * 100) / 100}` : null} />
                    {/* 측정값이 없는 기록은 서버가 용량과 재생 시간으로 계산한 평균값을 채워 보낸다. */}
                    <MetaRow k="비트레이트" val={probe.bit_rate ? fmtBitRate(probe.bit_rate) : null} />
                    {/* 컨테이너는 하나로 보여 준다. 디먹서 이름 원문은 `title`로 남긴다. */}
                    <MetaRow
                      k="컨테이너"
                      val={fmtContainer(probe.format_name, probe.major_brand)}
                      title={probe.format_name}
                    />
                    {/* 위반 사유. 타임라인과 같은 문구로 바꾼다(`violationLabel`). */}
                    {(probe.violations?.length ?? 0) > 0 && (
                      <ul className="space-y-0.5 pt-1 text-caption text-bad-text">
                        {probe.violations!.map((x) => <li key={x}>· {violationLabel(x, probe)}</li>)}
                      </ul>
                    )}
                    {/* 변환으로 해소된 원래 위반. 이 줄로 변환에 들어간 이유를 알 수 있다. */}
                    {(probe.transcode_redeemed?.length ?? 0) > 0 && (
                      <p className="pt-1 text-caption text-label-3">
                        변환으로 해소 · {probe.transcode_redeemed!.map((x) => violationLabel(x, probe)).join(", ")}
                      </p>
                    )}
                  </>
                )}
                {/* 파일에 딸린 사실(중복, 체크섬, 썸네일). 해당할 때만 줄을 만든다. */}
                {fileNotes.map((n) => (
                  <div key={n.key} className="pt-1 text-caption">
                    <p className={cn("flex flex-wrap items-center gap-x-1", n.tone === "bad" ? "text-bad-text" : "text-warn-text")}>
                      {n.icon}
                      {n.label}
                      {n.link}
                    </p>
                    {n.detail && <p className="mt-0.5 text-label-3">{n.detail}</p>}
                    {n.action}
                  </div>
                ))}
                {/* 분석본 정리. 되돌리기 어려운 동작이라 확인 창을 거친다. */}
                {canPurgeAnalysis && (
                  <div className="pt-1 text-caption">
                    <p className="text-label-3">분석본 720p 사본이 저장소에 있습니다.</p>
                    <button
                      type="button"
                      onClick={() => setPurgeOpen(true)}
                      className="mt-1 inline-flex items-center gap-1 rounded-control border border-separator px-2 py-0.5 text-caption font-medium text-label-2 transition-colors hover:bg-fill"
                    >
                      <Trash2 size={12} aria-hidden="true" /> 분석본 정리
                    </button>
                  </div>
                )}
              </MetaGroup>
            )}
          </div>
        </aside>
      </div>

      {/* 좁은 폭(lg 미만)에서는 메타데이터를 본문 아래에 둔다. */}
      <div className="mt-5 space-y-4 lg:hidden">
        <div className="space-y-3 rounded-card border border-separator bg-panel p-4 shadow-e1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-caption font-semibold text-label-3">영상 정보</h3>
            {canEditMeta && (
              <button
                type="button"
                onClick={() => setMetaOpen(true)}
                aria-label="영상 메타 편집"
                className="inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 text-caption font-medium text-label transition-colors hover:bg-fill"
              >
                <Pencil size={12} aria-hidden="true" /> 편집
              </button>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-footnote">
            <MetaCell k="프로그램명" val={v.program_name} />
            <MetaCell k="방송사" val={v.broadcaster} />
            <MetaCell k="장르" val={v.genre} />
            <MetaCell k="회차" val={v.episode} />
            <MetaCell k="방영일" val={airedAtToInput(v.aired_at) || null} />
            <MetaCell k="길이" val={v.duration_seconds ? fmtSecs(v.duration_seconds) : null} />
            <MetaCell k="등록일" val={airedAtToInput(v.created_at) || null} />
          </dl>
        </div>
      </div>

      {/*
        분석 재요청 확인 창. 비용이 드는 스택이면 영상 길이만큼 비용이 다시 든다.
        다시 요청할지 정하는 근거로 비용을 굵은 결과 줄에, 지난 실패의 사유를 회색 상자에 둔다.
        장면 요약을 받지 못한 영상이면 분석을 다시 하지 않으므로 비용 줄 없이 요약 재요청 문구로 연다.
      */}
      <Modal
        open={analysisConfirmOpen}
        onClose={() => setAnalysisConfirmOpen(false)}
        title={summaryOnly ? SUMMARY_RETRY_LABEL : "분석 재요청"}
        icon={<TriangleAlert size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setAnalysisConfirmOpen(false)}>취소</Button>
            <Button size="sm" onClick={() => retryAnalysis.mutate()} disabled={retryAnalysis.isPending}>
              {retryAnalysis.isPending ? "요청 중…" : "다시 요청"}
            </Button>
          </>
        }
      >
        {summaryOnly ? (
          <>
            <p className="font-semibold text-label">{SUMMARY_CONFIRM.lead}</p>
            <p className="mt-1 text-footnote text-label-3">{SUMMARY_CONFIRM.sub}</p>
          </>
        ) : (
          <>
            <p className="font-semibold text-label">
              {costFree
                ? "분석 비용이 들지 않습니다."
                : v.duration_seconds
                  ? `분석 비용이 영상 길이 ${fmtSpan(v.duration_seconds)}만큼 다시 듭니다.`
                  : "분석 비용이 영상 길이만큼 다시 듭니다."}
            </p>
            <p className="mt-1 text-footnote text-label-3">등록된 파일을 그대로 씁니다.</p>
          </>
        )}
        {lastAnalysisFail && (
          <div className="mt-3 rounded-control bg-canvas-2 px-3 py-2.5">
            <p className="text-caption text-label-3">지난 실패</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-footnote text-label">{lastAnalysisFail.error}</p>
          </div>
        )}
        {!summaryOnly && (
          <p className="mt-3 text-footnote text-label-2">사유가 파일 자체의 문제면 결과가 같습니다.</p>
        )}
      </Modal>

      {/*
        비식별화 건너뛰기 확인 창. 가리지 않은 원본으로 재생본과 다음 단계가 진행된다.
        판단 근거인 지난 실패의 사유를 분석 재요청 창과 같은 회색 상자에 둔다.
        비식별화를 다시 켜면 이 영상은 켜기 전에 처리된 영상으로 분류되어 비식별화 재시도로 가릴 수 있다.
      */}
      <Modal
        open={deidentSkipOpen}
        onClose={() => setDeidentSkipOpen(false)}
        title="비식별화 건너뛰기"
        icon={<SkipForward size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeidentSkipOpen(false)}>취소</Button>
            <Button size="sm" onClick={() => skipDeidentMut.mutate()} disabled={skipDeidentMut.isPending}>
              {skipDeidentMut.isPending ? "처리 중…" : "건너뛰기"}
            </Button>
          </>
        }
      >
        <p className="font-semibold text-label">비식별화하지 않은 원본으로 다음 단계가 진행됩니다.</p>
        <p className="mt-1 text-footnote text-label-3">이 프로젝트는 비식별화를 사용하지 않습니다.</p>
        {deidentSkipEntry?.error && (
          <div className="mt-3 rounded-control bg-canvas-2 px-3 py-2.5">
            <p className="text-caption text-label-3">비식별화 실패</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-footnote text-label">{deidentSkipEntry.error}</p>
          </div>
        )}
        <p className="mt-3 text-footnote text-label-2">건너뛴 사람과 시각이 기록으로 남습니다.</p>
      </Modal>

      {/*
        분석본 정리 확인 창. 원본이 남아 있으면 다시 분석할 때 원본을 쓴다.
        정리할 수 없는 상태(원본 없음, 분석 진행 중)는 서버가 거절하고 사유를 토스트로 표시한다.
      */}
      <Modal
        open={purgeOpen}
        onClose={() => setPurgeOpen(false)}
        title="분석본 정리"
        icon={<Trash2 size={16} aria-hidden="true" />}
        tone="warn"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setPurgeOpen(false)}>취소</Button>
            <Button size="sm" onClick={() => purgeMut.mutate()} disabled={purgeMut.isPending}>
              {purgeMut.isPending ? "정리 중…" : "정리"}
            </Button>
          </>
        }
      >
        <p className="font-semibold text-label">분석본 720p 사본이 저장소에서 지워집니다.</p>
        <p className="mt-1 text-footnote text-label-3">원본이 남아 있으면 다시 분석할 때 원본을 씁니다.</p>
        <p className="mt-3 text-footnote text-label-2">
          원본이 없거나 분석이 끝나지 않은 영상은 정리하지 않고 사유를 표시합니다.
        </p>
        <p className="mt-1 text-footnote text-label-2">정리한 사람과 시각이 기록으로 남습니다.</p>
      </Modal>

      {/*
        영상 삭제 확인 창. 되돌리기 어려운 동작이라 판단 근거를 같은 창에 둔다.
        영향은 이 영상에 실제로 딸린 항목만 이름과 값의 표로 쓴다.
        닫히는 작업 수는 서버가 센다(`open_work_items`). 승인된 작업은 닫히지 않고 납품에 남는다.
        서버는 soft delete이고 되살리는 화면은 없어서 영구 삭제라고 쓰지 않는다.
      */}
      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="영상 삭제"
        icon={<Trash2 size={16} aria-hidden="true" />}
        tone="danger"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDeleteOpen(false)}>취소</Button>
            <Button variant="danger" size="sm" onClick={() => deleteMut.mutate()} disabled={deleteMut.isPending}>
              {deleteMut.isPending ? "삭제 중…" : "삭제"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {/* 확인 창 의문형은 `~하시겠습니까?`로 맞춘다. */}
          <p className="font-semibold text-label">“{v.title}” 영상을 삭제하시겠습니까?</p>
          {(v.open_work_items > 0 || v.approved_clips + v.reviewing_clips > 0) && (
            <dl className="space-y-1.5 rounded-control bg-canvas-2 px-3 py-2.5 text-footnote">
              {v.open_work_items > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-label-3">진행 중인 라벨링·검수</dt>
                  <dd className="font-semibold tabular-nums text-bad-text">{v.open_work_items.toLocaleString("ko-KR")}건 종료</dd>
                </div>
              )}
              {v.approved_clips > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-label-3">승인된 작업</dt>
                  <dd className="font-semibold tabular-nums text-label">{v.approved_clips.toLocaleString("ko-KR")}건 유지 · 납품 대상</dd>
                </div>
              )}
              {v.approved_clips + v.reviewing_clips > 0 && (
                <div className="flex justify-between gap-3">
                  <dt className="text-label-3">제출된 라벨</dt>
                  <dd className="font-semibold text-label">지우지 않음</dd>
                </div>
              )}
            </dl>
          )}
          <div className="text-footnote text-label-2">
            <p>목록·통계와 진척률 계산에서 빠집니다.</p>
            {v.open_work_items > 0 && <p>종료된 작업은 보유한 사람의 목록에서도 사라집니다.</p>}
          </div>
          <p className="text-footnote font-semibold text-bad-text">삭제한 영상은 화면에서 되살릴 수 없습니다.</p>
        </div>
      </Modal>
    </div>
  );
}

/**
 * 끝난 준비 진행의 요약을 그린다. 단계는 칩 한 줄로, 확인 필요와 건너뜀 사유는 칩 아래 안내 줄로 둔다.
 *
 * 준비가 끝난 영상은 대개 클립을 보려고 열기 때문에 끝난 단계를 줄마다 펼치지 않는다.
 * 사유 문장을 칩 안에 두면 사유가 있는 칩만 넓어지므로, 칩에는 이름과 표시만 두고 문장은 안내 줄로 모은다.
 * 서버가 확인 필요(`review_needed`)로 표시한 단계는 경고색 칩과 맨 앞 경고 줄을 둔다.
 * 얼굴을 찾지 못한 비식별화는 완료여도 원본과 같아서, 요약에서 감추면 얼굴이 그대로 나가는 것을 알 수 없다.
 */
function PrepSummary({ entries }: { entries: PrepTimelineEntry[] }) {
  const notes = summaryNotes(entries);
  const review = notes.some((n) => n.tone === "warn");
  return (
    <section className="rounded-card border border-separator bg-panel px-5 py-4 shadow-e1">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-callout font-semibold text-label">준비 진행</h2>
        <p className="text-caption text-label-3">
          모두 완료{review && <span className="text-warn-text"> · 확인 필요</span>}
        </p>
      </div>
      <ol className="mt-3 flex flex-wrap gap-1.5">
        {entries.map((e) => {
          const mark = summaryMark(e);
          return (
            <li key={e.stage}>
              <Chip tone={mark === "review" ? "warn" : mark === "skipped" ? "quiet" : "neutral"}>
                {mark === "review" ? (
                  <TriangleAlert size={12} className="shrink-0" aria-hidden="true" />
                ) : mark === "skipped" ? (
                  <SkipForward size={12} className="shrink-0" aria-hidden="true" />
                ) : (
                  <Check size={12} className="shrink-0 text-ok-text" aria-hidden="true" />
                )}
                {RUN_STAGE_LABEL[e.stage]}
                {mark === "review" ? " 확인 필요" : mark === "skipped" ? " 건너뜀" : <span className="sr-only"> 완료</span>}
              </Chip>
            </li>
          );
        })}
      </ol>
      {/*
        안내 줄. 아이콘, 단계 이름, 사유 문장을 세 열 격자에 맞춰 줄마다 문장의 시작 위치를 맞춘다.
        좁은 폭에서는 세 열이면 문장 열이 몇 글자 폭으로 좁아져서 사유 문장을 단계 이름 아래 줄로 내린다.
        경고 줄만 배경을 칠하고, 모든 줄에 같은 안쪽 여백을 주어 배경 유무와 관계없이 열이 맞게 한다.
      */}
      {notes.length > 0 && (
        <ul className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-y-1 text-footnote sm:grid-cols-[auto_auto_minmax(0,1fr)]">
          {notes.map((n) => (
            <li
              key={n.stage}
              className={cn(
                "col-span-2 grid grid-cols-subgrid items-start gap-x-2 gap-y-0.5 rounded-control px-2.5 py-1.5 sm:col-span-3",
                n.tone === "warn" ? "bg-warn-muted text-warn-text" : "text-label-3",
              )}
            >
              {n.tone === "warn" ? (
                <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
              ) : (
                <SkipForward size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
              )}
              <span className={cn("font-medium", n.tone === "mute" && "text-label-2")}>{RUN_STAGE_LABEL[n.stage]}</span>
              <span className="col-start-2 sm:col-start-auto">{n.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * 사이드바의 메타 묶음을 그린다. 작은 제목과 값들을 그리고, 묶음 사이는 선으로 나눈다.
 *
 * 제목은 값을 채우는 주체를 나타낸다. 사람이 넣는 값이 비면 할 일이 남았다는 뜻이고,
 * 도구가 넣는 값이 비면 아직 읽지 못했다는 뜻이라 두 종류를 한 묶음에 섞지 않는다.
 * 설명 문구는 달지 않는다. `영상 메타` 아래의 `—`가 아직 채우지 않았다는 뜻을 전한다.
 */
function MetaGroup({
  title, last, children, action,
}: { title: string; last?: boolean; children: ReactNode; action?: ReactNode }) {
  return (
    <section className={cn("px-4 py-3.5", !last && "border-b border-separator")}>
      {/* 사람이 채우는 묶음은 설명 문구 대신 채우는 버튼을 제목 옆에 둔다(`action`). */}
      <div className="flex items-center justify-between gap-2">
        {/* 한글 제목에는 대문자 변환이 적용되지 않고 자간만 벌어지므로 둘 다 쓰지 않는다. */}
        <h3 className="text-caption font-semibold text-label-3">{title}</h3>
        {action}
      </div>
      <dl className="mt-2 space-y-2 text-footnote">{children}</dl>
    </section>
  );
}

/**
 * 영상 메타 편집 창을 그린다. 방송사·프로그램명·장르·방영일을 고친다.
 *
 * 네 값은 이 영상의 모든 클립이 물려받고 납품 메타와 자동 검수가 쓴다.
 * 그래서 라벨러가 클립마다 넣지 않고 관리자가 여기서 한 번 넣는다. 바뀐 필드만 보낸다(부분 갱신).
 *
 * 입력란을 비우고 저장하면 그 값을 지운다. 계약(`VideoUpdateRequest`)은 빈 문자열을 받지 않고
 * `null`을 지우기로 읽으므로 비운 필드는 `null`로 보낸다. 지울 값은 입력란 아래에 미리 알린다.
 * 방영일은 KST 달력 날짜로 받아 KST 자정으로 보낸다(`lib/video_meta`). UTC 자정으로 보내면 하루 밀린다.
 */
function VideoMetaModal({
  open, onClose, video, clipCount, pending, onSave,
}: {
  open: boolean;
  onClose: () => void;
  video: VideoDTO;
  /** 이 영상의 클립 수. 첫 줄에 영향 받는 수로 쓴다. 값이 없으면(조회 전·실패) `null`이다. */
  clipCount: number | null;
  pending: boolean;
  onSave: (body: Record<string, string | null>) => void;
}) {
  const initial = (): Record<VideoMetaKey, string> => ({
    broadcaster: video.broadcaster ?? "",
    program_name: video.program_name ?? "",
    genre: video.genre ?? "",
    aired_at: airedAtToInput(video.aired_at),
  });
  const [form, setForm] = useState(initial);
  // 창을 열 때마다 현재 값에서 시작한다. 그래서 닫았다 연 창에 이전 편집이 남지 않는다.
  useEffect(() => {
    if (open) setForm(initial());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const base = initial();
  const changed = VIDEO_META_FIELDS.filter((k) => form[k].trim() !== base[k]);
  const cleared = changed.filter((k) => !form[k].trim());
  // 방영일 입력란에 반영되지 않은 날짜가 있으면 저장을 막는다. 그대로 저장하면 입력한 날짜가 버려진다.
  const [airedProblem, setAiredProblem] = useState<string | null>(null);
  /** 계약상 최대 길이. 넘기면 서버가 422로 거절하므로 입력란이 먼저 막는다. */
  const maxLen = { broadcaster: 100, program_name: 200, genre: 100 } as const;

  function save() {
    const body: Record<string, string | null> = {};
    for (const k of changed) {
      if (k === "aired_at") body.aired_at = form.aired_at ? airedAtFromInput(form.aired_at) : null;
      else body[k] = form[k].trim() || null;
    }
    onSave(body);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="영상 메타 편집"
      /* 값을 고치는 중립 창이라 배지를 달지 않는다.
         입력란이 넷이라 좁은 높이에서 본문이 스크롤되므로 저장 버튼은 함께 밀려나지 않게 `footer`에 둔다. */
      footer={
        <>
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>취소</Button>
          <Button
            type="button"
            size="sm"
            onClick={save}
            disabled={pending || changed.length === 0 || !!airedProblem}
          >
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/*
          결과 한 줄과 안내 한 줄로 나눈다. 한 문장에 담으면 좁은 창에서 둘째 줄에 두 낱말만 남는다.
          첫 줄에는 영향을 받는 클립 수를 쓰고, 수를 모르면 수 없이 쓴다.
        */}
        <div>
          <p className="text-footnote text-label">
            {clipCount != null
              ? `이 영상의 클립 ${clipCount.toLocaleString("ko-KR")}개가 같은 값을 물려받습니다.`
              : "이 영상의 모든 클립이 같은 값을 물려받습니다."}
          </p>
          <p className="mt-0.5 text-caption text-label-3">납품 데이터와 자동 검수가 이 값을 씁니다.</p>
        </div>
        {/*
          비운 항목의 안내는 그 입력란 바로 아래에 두고 `aria-describedby`로 잇는다. 안내가 입력란 옆이라 항목 이름을 되풀이하지 않는다.
          바꾼 입력란은 테두리를 진하게 하고 이름 오른쪽에 원래 값을 붙여 바뀐 입력란만 저장된다는 사실을 보여 준다.
        */}
        {(["program_name", "broadcaster", "genre"] as const).map((k) => {
          const empty = cleared.includes(k);
          const dirty = changed.includes(k) && !empty;
          return (
            <div key={k} className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <Label htmlFor={`vm-${k}`}>{VIDEO_META_NAME[k]}</Label>
                {dirty && (
                  <span className="truncate text-caption text-label-3">원래 값 · {base[k] || "없음"}</span>
                )}
              </div>
              <Input
                id={`vm-${k}`}
                value={form[k]}
                maxLength={maxLen[k]}
                className={changed.includes(k) ? "border-label" : undefined}
                aria-describedby={empty ? `vm-${k}-clear` : undefined}
                onChange={(e) => {
                  const val = e.target.value;
                  setForm((f) => ({ ...f, [k]: val }));
                }}
              />
              {empty && (
                <p id={`vm-${k}-clear`} className="text-caption text-label-2">
                  저장하면 원래 값({base[k]})을 지웁니다.
                </p>
              )}
            </div>
          );
        })}
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <Label htmlFor="vm-aired_at">{VIDEO_META_NAME.aired_at}</Label>
            {changed.includes("aired_at") && form.aired_at && (
              <span className="text-caption text-label-3">원래 값 · {base.aired_at || "없음"}</span>
            )}
          </div>
          <DatePicker
            id="vm-aired_at"
            value={form.aired_at}
            onChange={(val) => setForm((f) => ({ ...f, aired_at: val }))}
            onProblem={setAiredProblem}
            placeholder="방영일 선택"
          />
          {cleared.includes("aired_at") && (
            <p className="text-caption text-label-2">저장하면 원래 값({base.aired_at})을 지웁니다.</p>
          )}
        </div>
      </div>
    </Modal>
  );
}

/**
 * 행 목록이 들어갈 위치의 로딩 골격을 그린다. 클립 목록이 쓴다.
 *
 * 여러 줄의 골격으로 미리 높이를 잡아 다 불러온 뒤 아래 내용이 밀리지 않게 한다.
 * 블록은 `aria-hidden`이라 바깥에 `role="status"`를 두어 보조기기에 로딩 중임을 전달한다(`ui/PageSkeleton`과 같은 방식).
 */
function RowsLoading({ rows, label }: { rows: number; label: string }) {
  return (
    <div className="space-y-2.5" role="status" aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={cn("h-3.5", i % 3 === 2 ? "w-1/2" : i % 2 ? "w-3/5" : "w-4/5")} />
      ))}
    </div>
  );
}

/** 사이드바의 메타 행을 그린다. 왼쪽에 이름, 오른쪽에 값을 둔다. */
function MetaRow({
  k, val, title,
}: { k: string; val: ReactNode; title?: string | null }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-label-3 shrink-0">{k}</dt>
      {/* `title`에 줄인 표기의 원문(예: 디먹서 이름 전체)을 넣어 확인할 수 있게 한다. */}
      <dd className="text-label-2 text-right truncate" title={title ?? undefined}>
        {val || <span className="text-label-4">—</span>}
      </dd>
    </div>
  );
}

/**
 * 좁은 폭 메타데이터 그리드의 셀을 그린다.
 *
 * 값을 두 줄까지 보여 주고 원문은 `title`로 남긴다. 2열 그리드라 한 셀만 길어지면
 * 같은 행의 옆 셀과 높이가 어긋난다. 편집 창으로 상한(프로그램명 200자)까지 넣을 수 있어 긴 값이 실제로 들어온다.
 */
function MetaCell({ k, val }: { k: string; val: string | null | undefined }) {
  return (
    <>
      <dt className="text-label-3">{k}</dt>
      <dd className="line-clamp-2 min-w-0 text-label-2" title={val ?? undefined}>
        {val || <span className="text-label-4">—</span>}
      </dd>
    </>
  );
}
