/**
 * 검수 워크벤치(`/review/:id`).
 * 제출된 라벨을 문장별로 확인하고 승인·수정 후 승인·반려를 제출한다.
 *
 * 판정은 문장 트리아지(확인·수정·삭제·지적)에서 자동으로 정해진다.
 * 판정 전까지의 표시는 검수 초안으로 서버에 저장하고, 판정이나 신고 때 서버가 비운다.
 * 검수를 점유한 사람만 판정·초안 저장·신고를 할 수 있다. 다른 사람에게는 읽기 전용으로 보인다.
 * 반납과 보유 시간은 없다. 검수자는 판정해야 다음 검수를 받는다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MediaPlayerInstance } from "@vidstack/react";
import {
  BookOpen, Check, ChevronDown, Eye, Keyboard, ShieldAlert,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useProject } from "@/features/projects";
import { ReadOnlyLabel } from "@/components/ReadOnlyLabel";
import { useUnsavedGuard } from "@/shared/hooks/useUnsavedGuard";
import { useSyncProjectContext } from "@/hooks/useSlugProject";
import {
  CAPTION_FONT_SIZE,
  WbSaveStatus,
  WbVideoMeta,
  type CaptionSentence,
  estimateTokens,
  joinSentences,
  liveSentences,
  sentencesOf,
  SentenceTriage,
  type SentenceMark,
  ReportDeidentModal,
  WB_CHIP_TIGHT,
  GuidePanel,
  WbShortcutsDialog,
  useWorkbenchSurface,
  WorkbenchHeader,
  type WbAction,
  type WbStat,
  useVideoShortcuts,
  VIDEO_SHORTCUTS,
} from "@/features/workbench";
import { useAuth } from "@/store/auth";
import { Link, useNavigate, useParams } from "react-router-dom";

import { fetchClip, libraryKeys, VideoPlayer } from "@/features/library";
import { AccessDenied } from "@/shell/AccessDenied";
import { EmptyState } from "@/shared/ui/EmptyState";
import { ErrorState } from "@/shared/ui/ErrorState";
import { WorkbenchSkeleton } from "@/shared/ui/PageSkeleton";
import { FIELD_BASE } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/Toast";
import { type ApiError } from "@/shared/api/client";
import { errorText } from "@/shared/api/error_text";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { fmtDateTime } from "@/shared/lib/time";
import type { components } from "@reborn/contracts";
import { invalidateHolding, meKeys } from "@/features/me";

import {
  decideReview,
  fetchWorkItem,
  fetchWorkLabels,
  fetchWorkQc,
  fetchWorkReviews,
  pipelineKeys,
  reportDeidentMiss,
  saveReviewDraft,
} from "../api";
import { rejectHasReason } from "../lib/reject_reason";
import { modifyReason, readReviewDraft, reviewDraftBody, withReviewDraft } from "../lib/review_draft";
import { staleDecisionText } from "../lib/stale_review";

type WorkItemDTO = components["schemas"]["WorkItemDTO"];
type Verdict = "approve" | "reject" | "modify_and_approve";

/**
 * 판정 사유 문장(`reason`)의 최대 글자 수. 계약 `ReviewDecisionRequest.reason`의 `max_length`와 같은 값이다.
 * 생성된 타입에는 글자 수 제한이 실리지 않아 이 상수로 둔다. 입력란이 막지 않으면 다 쓴 뒤 422로 거절된다.
 */
const REASON_MAX = 1000;

/**
 * 검수 화면의 검사 단계 표시. 워크벤치는 폭이 좁아 요약형으로 그린다.
 *
 * 실행 이력(`pages/qc.tsx`)은 칩을 모두 늘어놓지만, 이 표시는 판정 배너 위의 좁은 위치에 들어간다.
 * 그래서 통과한 수만 세고, 실행하지 않은 단계만 이름을 밝힌다.
 * 실행하지 않은 단계는 이 건에서 확인하지 못한 항목이라 검수자에게 알린다.
 *
 * `null`은 단계 기록 이전의 실행이다. 빈 체크리스트로 그리면 잘못 표시하게 되므로 아무것도 그리지 않는다.
 */
function QcStages({ stages }: { stages?: components["schemas"]["QcStage"][] | null }) {
  if (!stages || stages.length === 0) return null;
  /* 전체 항목 수를 먼저 쓰고 상태별 수를 잇는다. 통과만 세면 나머지(지적 등)의 내역이 보이지 않는다.
     상태 이름은 서버 `QcStage.status`(passed · flagged · noted · skipped)를 따른다. */
  const count = (st: string) => stages.filter((s) => s.status === st).length;
  const skipped = stages.filter((s) => s.status === "skipped");
  const parts = [
    `통과 ${count("passed")}`,
    count("flagged") > 0 ? `지적 ${count("flagged")}` : null,
    count("noted") > 0 ? `알림 ${count("noted")}` : null,
  ].filter(Boolean);
  return (
    <p className="mt-1.5 text-micro text-label-3">
      {stages.length}개 항목 · {parts.join(" · ")}
      {skipped.length > 0 && (
        <> · 미실행 {skipped.length}({skipped.map((s) => s.label || s.key).join(" · ")})</>
      )}
    </p>
  );
}

/**
 * 검수 워크벤치 화면을 그린다. 주소의 작업이 바뀌면 화면 전체를 새로 그린다.
 *
 * 같은 주소 틀(`review/:id`)에서 `id`만 바뀌면 React는 같은 컴포넌트를 다시 쓴다.
 * 그러면 앞 작업의 문장 표시·반려 사유·열린 창이 다음 작업으로 넘어가
 * 다음 작업의 초안 저장과 판정에 실린다. 그래서 라벨링 워크벤치처럼 `id`를 key로 걸어 새로 마운트한다.
 */
export function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  return <ReviewWorkbench key={id} />;
}

/** 한 작업의 검수 워크벤치를 그린다. 판정·초안·비식별화 신고를 처리한다. */
function ReviewWorkbench() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  /** 자동 검수 지적 카드를 편 상태. `null`이면 사용자가 아직 조작하지 않은 상태이고, 그때는 지적의 무게로 정한다. */
  const [qcOpen, setQcOpen] = useState<boolean | null>(null);
  /** 비식별화 신고 창을 연 순간의 재생 위치(클립 기준 ms). 창이 보여 주는 값과 제출하는 값이 같다. */
  const [reportAtMs, setReportAtMs] = useState<number | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const toggleGuide = useCallback(() => setGuideOpen((o) => !o), []);
  const { toast } = useToast();
  const playerRef = useRef<MediaPlayerInstance>(null);
  // 워크벤치 공용 테마(라벨링과 같은 훅). 마운트 동안 덮어쓰고 언마운트 때 되돌린다.
  // 앱 테마와 관계없이 흑연색 면이다. 모달까지 흑연색으로 그리도록 문서 루트에도 범위를 단다.
  useWorkbenchSurface();
  // 판정은 문장별 트리아지에서 자동으로 정한다(아래 `derivedVerdict`). 수동 선택은 없다.
  // 반려 사유에는 지적(flag) 문장이 자동으로 포함되고, 사유 코드와 보충 문장은 선택이다.
  const [rejectCodes, setRejectCodes] = useState<string[]>([]);
  const [rejectExtra, setRejectExtra] = useState("");
  /**
   * 코드화 이전 초안에 이름으로 저장된 칩. 초안을 잃지 않도록 목록을 받으면 코드로 바꾼다.
   */
  const [legacyTagNames, setLegacyTagNames] = useState<string[] | null>(null);

  const item = useQuery({
    queryKey: pipelineKeys.workItem(id),
    queryFn: () => fetchWorkItem(id),
    enabled: !!id });
  /* 판정 이력. 승인된 건의 승인 일자에 쓴다. */
  const reviewHistory = useQuery({
    queryKey: pipelineKeys.workReviews(id),
    queryFn: () => fetchWorkReviews(id),
    enabled: !!id && item.data?.status === "approved",
    meta: { handled: true } });
  const labels = useQuery({
    queryKey: pipelineKeys.workLabels(id),
    queryFn: () => fetchWorkLabels(id),
    // 딸린 조회는 작업을 받은 뒤에 한다.
    // `item`이 403이면 이 조회도 403이고, 화면은 이미 사유를 그렸다.
    enabled: !!id && !!item.data });
  const projectId = item.data?.project_id;
  /* 연 작업이 속한 프로젝트로 프로젝트 선택기를 맞춘다.
     맞추지 않으면 알림으로 B 프로젝트의 건을 열고 헤더의 `내 작업`을 누를 때 A 프로젝트의 큐가 뜬다. */
  useSyncProjectContext(projectId);

  // 클립의 MAIU 참고 정보(장면 요약·키워드·화자). 라벨링 워크벤치와 같은 출처라 캐시를 함께 쓴다.
  const clipId = item.data?.clip_id;
  const clip = useQuery({
    queryKey: libraryKeys.clip(projectId, clipId),
    queryFn: () => fetchClip(projectId, clipId),
    enabled: !!projectId && !!clipId });
  /* 캡션 품질검수 결과. 판정 전에 보는 근거다.
     검사는 제출 뒤 워커가 하므로 상태는 서버가 정하고 화면은 다시 계산하지 않는다(판정 규칙은 서버 `caption_qc.backlog`).
     `running`은 지금 제출본의 검사가 끝나지 않은 상태라 끝날 때까지 다시 조회한다.
     `none`은 프로젝트가 QC를 켜지 않았거나 검사 기능이 생기기 전의 작업이라 통과로 표시하지 않는다.
     조회 실패는 판정 배너 위에 다시 시도와 함께 표시하고, 겹치는 전역 토스트는 끈다.
     실패를 `null`과 같이 처리하면 아무것도 그리지 않아 지적이 있는 건을 "지적 없음"으로 보고 승인할 수 있다. */
  const qcQuery = useQuery({
    queryKey: pipelineKeys.workQc(id),
    queryFn: () => fetchWorkQc(id),
    enabled: !!id && !!item.data,
    // 검사는 보통 수십 초 안에 끝난다. 그동안만 짧게 다시 조회하고 끝나면 멈춘다.
    refetchInterval: (q) => (q.state.data?.state === "running" ? 3_000 : false),
    meta: { handled: true } });
  /** 지금 본의 최신 실행. 검사 중이거나 기록이 없으면 `null`이다. */
  const qcRun = qcQuery.data?.run ?? null;

  /*
    반려 사유 칩은 프로젝트 설정에서 관리하는 서버 목록을 `ProjectDTO`로 받아 쓴다.
    사유는 코드(`reason_codes`)로, 보충 설명은 문장(`reason`)으로 따로 보낸다. 코드로 보내야 사유별 반려 수를 셀 수 있다.
    라벨링 워크벤치와 같은 키·같은 형태라 캐시를 함께 쓴다.
  */
  const project = useProject(projectId);

  /*
    판정처럼 이 건을 끝내고 떠나는 동안에는 초안 저장을 멈춘다.
    판정 요청이 나간 사이 2초 타이머가 실행되면 서버가 판정을 먼저 끝낸 뒤 그 저장을 409로 거절한다.
    그러면 성공한 판정 뒤에 경고가 뜬다.
  */
  const finishing = useRef(false);

  const decide = useMutation<
    WorkItemDTO,
    ApiError,
    {
      decision: Verdict; reason?: string; reason_codes?: string[];
      flagged_sentences?: string[]; modified_label_data?: object; base_label_id: string;
    }
  >({
    mutationFn: (body) => decideReview(id, body),
    // 실패는 판정 배너 아래에 인라인으로 표시한다.
    // 전역 토스트까지 뜨면 같은 문장이 두 곳에 나오므로 토스트는 끈다.
    meta: { handled: true },
    onError: (e) => {
      finishing.current = false;
      // 422면 대개 그사이 사유 목록이 바뀐 경우다(꺼진 사유). 목록을 다시 받아 칩을 맞춘다.
      if (e.status === 422) void project.refetch();
      /* 판정한 제출본이 최신 제출본이 아니면 서버가 거절한다. 제출본을 다시 받는다.
         안내는 아래 배너가 표시한다. */
      if (e.detail === "review_base_mismatch") void labels.refetch();
      /* 그사이 다른 화면에서 처리된 작업이면 바뀐 상태를 알리고 화면을 새 상태로 바꾼다.
         배너로 "제출 상태가 아닙니다"만 남기면 같은 작업과 판정 버튼이 그대로 남아 사용자가 다시 누른다. */
      const stale = staleDecisionText(e.detail);
      if (stale) {
        toast("warning", stale.message, stale.hint);
        decide.reset();
        void item.refetch();
        void qc.invalidateQueries({ queryKey: pipelineKeys.reviewAll() });
        void qc.invalidateQueries({ queryKey: meKeys.reviewWaitingAll() });
      }
    },
    onSuccess: (_w, body) => {
      finishing.current = false;
      draftDirty.current = false;   // 판정이 초안을 소비했으므로 떠날 때 flush할 내용이 없다
      latestDraftFor.current = null;   // 떠날 때 캐시에 실을 초안도 없다
      void qc.invalidateQueries({ queryKey: pipelineKeys.reviewAll() });
      void qc.invalidateQueries({ queryKey: pipelineKeys.workAll() });
      // 판정하면 보유가 풀린다. `내 작업` 머리의 보유 수도 무효화한다.
      invalidateHolding(qc, "review");
      void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
      /* 결과를 알린다. 알리지 않으면 사용자가 판정이 나갔는지 다음 화면을 보고 추측해야 한다.
         내 작업으로 돌아가면 다음 검수 버튼이 서버가 고른 다음 건을 연다. */
      const done = { approve: "승인했습니다", reject: "반려했습니다", modify_and_approve: "수정 후 승인했습니다" }[body.decision];
      toast("success", done, "내 작업으로 돌아갑니다.");
      // 방금 판정한 건으로는 되돌아가지 않도록 이력에 남기지 않는다(replace).
      navigate("/my/work", { replace: true });
    } });

  // 비식별화 미흡 신고.
  // 창을 연 순간의 재생 위치를 기록하고, 클립을 비식별화 신고 상태로 바꾼 뒤 내 작업으로 간다.
  // 서버가 검수 점유를 풀고 판정 초안을 지운다(`report_deident_miss`). 창이 그 사실을 먼저 알린다.
  const openReport = () => {
    setReportAtMs(Math.round((playerRef.current?.currentTime ?? 0) * 1000));
    setReportOpen(true);
  };
  const report = useMutation({
    mutationFn: (reason: string) =>
      reportDeidentMiss(id, {
        timestamp_ms: reportAtMs ?? Math.round((playerRef.current?.currentTime ?? 0) * 1000),
        reason }),
    onSuccess: () => {
      setReportOpen(false);
      void qc.invalidateQueries({ queryKey: pipelineKeys.reviewAll() });
      void qc.invalidateQueries({ queryKey: pipelineKeys.workAll() });
      invalidateHolding(qc, "review");
      toast("success", "비식별화 미흡을 신고했습니다", "검수 작업의 보유가 풀렸습니다.");
      // 신고한 건으로는 되돌아가지 않도록 이력에 남기지 않는다.
      navigate("/my/work", { replace: true });
    } });

  const latest = labels.data?.[labels.data.length - 1];
  // 검수 문장별 트리아지. 각 캡션 문장을 확인(ok)·수정(edit)·삭제(del)·지적(flag)으로 표시한다.
  // 표시는 문장 id에 붙인다. 인덱스에 붙이면 검수자가 문장을 고쳐 종결부호가 늘거나
  // 삭제로 하나가 빠질 때, 최종본을 다시 나누면서 번호가 밀려 이력이 다른 문장을 가리킨다.
  const [marks, setMarks] = useState<Map<string, SentenceMark>>(() => new Map());

  /* 검수 초안. 판정 전까지 쌓은 표시를 서버에 둔다.
     저장은 2초 디바운스다. 문장을 누를 때마다 저장하면 한 건 검수에 수십 번 쓴다.
     초안은 판정(또는 신고) 때까지 유지되고, 그때 서버가 비운다.
     초안이 없으면 탭이 비정상 종료되거나 토큰(60분)이 만료될 때 지적 문장과 반려 사유가 모두 사라진다. */
  /* 검수를 점유한 사람만 수정할 수 있다. 쓰기(초안·판정·신고)와 읽기 전용 여부를 서버와 같은 조건 하나(`heldByMe`)로 정한다.
     `GET /work/{id}`는 reviewer 이상과 super_admin에게 열리지만(`work/service/queries.py`의 `get_one`),
     초안 저장·판정은 `review_claimed_by`가 본인이어야 한다. 조건이 다르면 관리자가 다른 사람의 건을 열 때 버튼이 활성 상태가 되고 초안 저장이 403으로 거절된다.
     보유에는 끝나는 시각이 없다. */
  const myUserId = useAuth((st) => st.user?.user_id);
  const heldByMe = !!myUserId && item.data?.review_claimed_by === myUserId;

  /* 초안 저장이 성공했고 그사이 새 편집이 없을 때(`seq`)만 "저장됨"으로 표시한다.
     실패하면 dirty를 유지해 다음 flush와 탭 닫기 경고가 편집을 계속 보호하고, 헤더는 `저장 실패 · 다시 시도`를 표시한다.
     보내자마자 dirty를 끄면 실패한 저장이 기록되지 않아 건을 넘기거나 탭을 닫을 때 지적·사유가 사라진다.
     403(점유를 잃음)·409(이미 판정됨)면 건을 다시 받아 읽기 전용으로 바꾼다. 떠난 건의 flush가 실패하면 건 ID와 함께 알린다.
     요청에는 건 ID를 싣는다. 클로저의 `id`는 건을 옮긴 뒤에는 다음 건을 가리킨다. */
  const [draftState, setDraftState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [draftAt, setDraftAt] = useState<string | null>(null);
  const currentId = useRef(id);
  currentId.current = id;
  /** 화면이 마운트돼 있으면 true. 화면을 떠난 뒤 끝난 초안 저장의 결과는 알림으로 알린다. */
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  /** 편집할 때마다 1씩 올리는 번호. 저장이 끝났을 때 그사이 새 편집이 있었는지 판정하는 데 쓴다. */
  const draftSeq = useRef(0);
  const draftTimer = useRef<number | undefined>(undefined);
  const draftMut = useMutation<WorkItemDTO, ApiError, { wid: string; body: object; seq: number }>({
    mutationFn: ({ wid, body }) => saveReviewDraft(wid, body),
    onMutate: ({ wid }) => {
      if (mounted.current && wid === currentId.current) setDraftState("saving");
    },
    onSuccess: (_w, { wid, body, seq }) => {
      if (!mounted.current || wid !== currentId.current) return;   // 떠난 건의 flush라 이 화면에는 반영하지 않는다
      serverDraft.current = JSON.stringify(body);
      if (seq === draftSeq.current) draftDirty.current = false;
      setDraftState("saved");
      // 24시간제로 쓴다. 라벨링 임시저장 시각과 같은 모양이다.
      setDraftAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }));
    },
    onError: (e, { wid }) => {
      if (finishing.current) return;                  // 판정으로 이 건을 끝내는 중
      const t = errorText(e.detail, { message: "검수 초안 저장 실패", hint: "잠시 후 다시 시도해주세요." });
      if (!mounted.current || wid !== currentId.current) {
        toast("error", `검수 초안 저장 실패 · ${wid.slice(0, 8)}`, t.message);
        return;
      }
      setDraftState("error");
      if (e.status === 403 || e.status === 409) {
        toast("warning", t.message, t.hint);
        void item.refetch();
      }
    },
  });
  /* 초안 복원은 건을 불러온 뒤 한 번만 한다. 저장할 때마다 되돌리면 방금 지운 표시가 다시 나타난다.
     초안이 없어도 복원 완료로 표시한다. 표시하지 않으면 아래 저장이 실행되지 않아 첫 초안이 저장되지 않는다.
     불러오기 전에 표시하면 빈 상태가 기존 초안을 덮으므로 `item.data`를 기다린다.
     저장 이펙트는 ref 대신 `restoredId` 상태를 본다. 복원한 값은 다음 렌더에 들어오므로, ref를 보면 복원과 같은 커밋에서
     빈 상태로 초안 본문을 만들고 그 사이 flush가 빈 초안으로 서버 초안을 덮는다. */
  const restored = useRef<string | null>(null);
  const [restoredId, setRestoredId] = useState<string | null>(null);
  /*
    서버에 있는 초안 본문(JSON). 화면 상태가 이 값과 같으면 저장하지 않는다.
    없으면 건을 열기만 해도 복원 직후 같은 내용을 저장해 `저장 완료`가 뜨고, 건드리지 않은 건이 편집한 건처럼 보인다.
  */
  const serverDraft = useRef<string | null>(null);
  useEffect(() => {
    if (!id || restored.current === id || !item.data) return;
    restored.current = id;
    setRestoredId(id);
    serverDraft.current = JSON.stringify(reviewDraftBody(readReviewDraft(item.data.review_draft ?? null)));
    if (!item.data.review_draft) return;
    const d = readReviewDraft(item.data.review_draft);
    setMarks(new Map(d.marks));
    setRejectCodes(d.codes);
    // 코드화 이전 초안에는 칩 이름이 있다. 목록을 받으면 코드로 바꾼다(아래 이펙트).
    setLegacyTagNames(d.legacyTags);
    setRejectExtra(d.extra);
  }, [id, item.data]);

  // 이전 형식 초안의 칩 이름을 코드로 바꾼다. 목록을 받은 뒤 한 번만 한다.
  // 켜진 사유만 바꾸고 목록에 없는 이름은 버린다.
  useEffect(() => {
    const catalog = project.data?.reject_reasons;
    if (!legacyTagNames || !catalog) return;
    const codes = catalog.filter((r) => r.enabled && legacyTagNames.includes(r.label)).map((r) => r.code);
    setRejectCodes((prev) => [...new Set([...prev, ...codes])]);
    setLegacyTagNames(null);
  }, [legacyTagNames, project.data?.reject_reasons]);

  // 최신 초안 페이로드와 미저장 여부.
  // 건 전환·언마운트 때 flush가 이전 값을 쓰지 않도록 ref로 둔다.
  const latestDraft = useRef<ReturnType<typeof reviewDraftBody>>({});
  /** `latestDraft`가 담은 초안의 건 ID. 판정이 초안을 소비하면 `null`이다. */
  const latestDraftFor = useRef<string | null>(null);
  const draftDirty = useRef(false);

  useEffect(() => {
    if (!id || restoredId !== id) return;   // 복원 전에는 빈 상태로 덮어쓰지 않는다
    if (!heldByMe) return;                  // 점유하지 않은 건에는 쓰지 않는다(서버가 403을 준다)
    const payload = reviewDraftBody({
      marks: [...marks.entries()], codes: rejectCodes, extra: rejectExtra });
    latestDraft.current = payload;
    latestDraftFor.current = id;
    // 서버 초안과 같으면(연 직후, 바꿨다가 되돌린 경우) 보내지 않는다. 보내는 중인 저장이 있으면 그 값이 서버 초안이 되므로 비교하지 않는다.
    if (!draftMut.isPending && JSON.stringify(payload) === serverDraft.current) {
      draftDirty.current = false;
      return;
    }
    draftDirty.current = true;
    const seq = ++draftSeq.current;
    const wid = id;
    draftTimer.current = window.setTimeout(() => {
      if (finishing.current) return;
      // dirty는 이 위치에서 내리지 않는다. 성공했을 때 `onSuccess`가 내린다.
      draftMut.mutate({ wid, body: payload, seq });
    }, 2000);
    return () => window.clearTimeout(draftTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, restoredId, heldByMe, marks, rejectCodes, rejectExtra]);

  // 건 전환·언마운트 때 아직 보내지 않은 마지막 편집을 즉시 저장한다. 결과는 `onSuccess`/`onError`가 건 ID로 구분해 받고, 실패하면 알림으로 표시한다.
  // clearTimeout만 하면 2초 디바운스 안에 다음 건으로 넘어갈 때 마지막 판정·수정이 사라진다.
  // 저장과 별개로 마지막 초안을 작업 캐시에도 싣는다. 이 건으로 돌아오면 새 화면이 캐시로 초안을 복원하므로,
  // 싣지 않으면 처음 받은 이전 초안이 복원되고 2초 뒤 자동 저장이 서버 초안을 그 값으로 덮는다.
  // 저장 응답을 기다리지 않고 떠나는 순간에 실어야 flush가 끝나기 전에 돌아와도 최신 초안을 복원한다.
  useEffect(() => {
    const flushId = id;
    return () => {
      if (!flushId || !heldByMe || finishing.current || latestDraftFor.current !== flushId) return;
      qc.setQueryData<WorkItemDTO>(pipelineKeys.workItem(flushId), (old) => withReviewDraft(old, latestDraft.current));
      if (draftDirty.current) {
        draftMut.mutate({ wid: flushId, body: latestDraft.current, seq: draftSeq.current });
        draftDirty.current = false;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, heldByMe]);

  /** 헤더의 `저장 실패 · 다시 시도` 동작. 지금 편집을 곧바로 다시 보낸다. */
  const retryDraft = () => {
    if (!id || !heldByMe) return;
    window.clearTimeout(draftTimer.current);
    draftMut.mutate({ wid: id, body: latestDraft.current, seq: draftSeq.current });
  };

  // 탭 닫기·새로고침 때도 경고한다.
  // flush는 앱 안 이동에서, 이 경고는 브라우저 이탈에서 편집을 보호한다.
  useUnsavedGuard(() => draftDirty.current);
  const setMark = (sid: string, mark: SentenceMark | null) =>
    setMarks((prev) => {
      const next = new Map(prev);
      if (mark) next.set(sid, mark);
      else next.delete(sid);
      return next;
    });

  // 문장별 트리아지 집계. 자동 판정(`derivedVerdict`)·반려 사유·수정 캡션을 만드는 데 쓴다.
  // 라벨러가 저장한 문장 배열을 그대로 받는다. 이 화면에서 다시 나누면 라벨러 쪽과 달라진다.
  // 삭제 표시된 문장은 최종 캡션에 없으므로 검수에도 보이지 않는다(`liveSentences`).
  // 캡션의 세 모양은 `sentencesOf`가 읽는다. 품질검수(`caption.sentences_of`)와 같은 id가 붙는다.
  const submittedLabel = latest?.label_data;
  // 라벨러가 저장한 문장 배열 전체(삭제 표시 포함). 표시할 때는 `liveSentences`로 거르지만,
  // 수정 후 승인은 이 배열을 기준으로 고친다. 그래야 소프트 삭제가 사라지지 않는다.
  // 판정(`marks`)이 문장 id에 붙어 있어 렌더마다 새로 만들지 않는다. id는 `toSentences`가 위치 기반으로 고정한다.
  // id가 매번 바뀌면 ✓를 눌러도 카드가 그 문장의 판정을 찾지 못한다.
  const allSentences = useMemo(() => sentencesOf(submittedLabel), [submittedLabel]);
  const captionSentences = liveSentences(allSentences);
  const markCounts = { ok: 0, edit: 0, flag: 0, del: 0 };
  marks.forEach((m) => {
    markCounts[m.status] += 1;
  });
  /* 자동 검수 지적을 문장에 붙는 것과 붙지 않는 것으로 이 위치 한 곳에서 나눈다.
     문장에 붙는 지적은 문장 카드가 그 문장 아래에 그리고, 나머지는 아래 `검수 결과` 카드가 목록으로 보여 준다.
     클립 단위 지적(`caption_empty`)과 부가 라벨 `info`에는 `sentence_id`가 없고, 가리키는 문장이 삭제된 지적도 있다.
     이런 지적을 문장 카드에만 맡기면 화면에서 사라진다. 카드와 목록이 각자 분류하면 지적이 두 번 나오거나 빠진다. */
  const qcFindings = qcRun?.findings ?? [];
  const sentenceIds = new Set(captionSentences.map((s) => s.id));
  const qcBySentence = new Map<string, components["schemas"]["QcFinding"][]>();
  for (const f of qcFindings) {
    if (!f.sentence_id || !sentenceIds.has(f.sentence_id)) continue;
    const list = qcBySentence.get(f.sentence_id);
    if (list) list.push(f);
    else qcBySentence.set(f.sentence_id, [f]);
  }
  /** 문장 카드로 올라간 지적 수. 아래 카드가 분모를 밝히는 데 쓴다. */
  const qcOnCards = [...qcBySentence.values()].reduce((n, l) => n + l.length, 0);
  /** 문장에 붙지 않는 지적. 아래 카드가 그대로 목록으로 보여 준다. */
  const qcRest = qcFindings.filter((f) => !f.sentence_id || !sentenceIds.has(f.sentence_id));
  /** 지적 카드를 펴 두면 true. 사용자가 정했으면 그 값을, 아니면 문장에 붙지 않는 `error` 지적이 있을 때만 편다. */
  const qcCardOpen = qcOpen ?? qcRest.some((f) => f.severity === "error");

  // 지적한 문장의 원문을 문장 순서대로 모은다. id에는 순서 정보가 없어서 원본 배열을 순회한다.
  const flaggedTexts = captionSentences
    .filter((s) => marks.get(s.id)?.status === "flag")
    .map((s) => s.text);
  // 지적 문장은 `flagged_sentences`, 사유 칩은 `reason_codes`, 보충 설명은 `reason`으로 따로 보낸다.
  // 한 문자열로 이으면 이력 화면이 다시 쪼개야 한다.
  const reasonNote = rejectExtra.trim();
  // 수정된 문장 배열. 라벨러 배열 전체에 검수 표시를 얹는다.
  // 삭제는 `deleted`로만 표시하고 배열에서 빼지 않는다(되돌리기).
  // 수정은 텍스트만 바꾸고 id는 유지한다.
  // 이 배열이 기준 데이터(`brief_sentences`)가 되어야 납품에 검수 수정이 반영된다.
  const editedSentences: CaptionSentence[] = allSentences.map((s) => {
    if (s.deleted) return s;
    const mk = marks.get(s.id);
    if (mk?.status === "del") return { ...s, deleted: true };
    if (mk?.status === "edit") return { ...s, text: mk.text ?? s.text };
    return s;
  });
  // 표시·비교용 최종 문자열. `joinSentences` 한 곳의 규칙으로 삭제되지 않은 문장만 잇는다.
  const editedBrief = joinSentences(editedSentences);

  /* 수정 후 승인으로 보낼 label_data를 만든다. 라벨러 배열에 문장 표시를 얹고 `brief` 문자열과 `brief_sentences` 배열을 함께 고친다.
     납품·검수 소비자는 배열이 있으면 문자열을 읽지 않는다(`features/workbench/lib/caption` 규약).
     그래서 `brief`만 덮으면 검수 수정이 납품에서 모두 원래대로 돌아간다. */
  function buildModifiedLabelData(): object {
    // `caption`이 문자열인 이전 형식이면 펼치지 않는다. 문자열을 펼치면 글자마다 키가 생긴다.
    const raw = (latest!.label_data as { caption?: unknown }).caption;
    const cap = typeof raw === "object" && raw !== null ? raw : {};
    return { ...(latest!.label_data as object), caption: { ...cap, brief: editedBrief, brief_sentences: editedSentences } };
  }
  const hasEdits = markCounts.edit > 0 || markCounts.del > 0;
  // 판정을 문장 트리아지 결과로 자동으로 정한다.
  // 지적(반려)이 있으면 반려, 수정·삭제가 있으면 수정 후 승인, 그 외(모두 확인이거나 미표시)는 승인이다.
  // 반려는 지적 문장(위치)과 함께 사유 칩이나 보충 설명(내용)이 있어야 제출된다(`lib/reject_reason`, 서버와 같은 조건).
  // 수동 판정 선택이 없으므로 반려한 채 승인하는 모순이 생기지 않는다.
  const derivedVerdict: Verdict =
    markCounts.flag > 0 ? "reject" : hasEdits ? "modify_and_approve" : "approve";
  // 검수자가 몇 번째 반려인지 알고 누르도록 반려 횟수를 미리 보여 준다.
  const rejectionCount = item.data?.rejection_count ?? 0;
  const unreviewed = captionSentences.length - marks.size;
  // 자동 판정의 이름과 보조 설명. 판정 영역은 수동 선택 없이 큰 글자 하나와 설명 한 줄로 표시만 한다.
  const verdictView = {
    approve: { l: "승인", desc: "지적·수정 없음" },
    modify_and_approve: {
      l: "수정 후 승인",
      desc: [markCounts.edit && `${markCounts.edit}개 수정`, markCounts.del && `${markCounts.del}개 삭제`]
        .filter(Boolean)
        .join(" · ") },
    reject: { l: "반려", desc: `${markCounts.flag}개 문장 지적` } }[derivedVerdict];
  /** 반려인데 사유 칩도 보충 설명도 없으면 true. 제출 버튼을 잠그고 반려 영역에서 이유를 알린다. */
  const rejectReasonMissing = derivedVerdict === "reject" && !rejectHasReason(rejectCodes, reasonNote);
  /* 수정 후 승인의 수정본도 라벨러 제출과 같은 캡션 기준(`caption_min_tokens`)을 넘어야 한다.
     서버가 같은 기준으로 거절하므로(422 caption_too_short) 화면도 같은 기준으로 제출 버튼을 잠근다.
     기준은 워크벤치와 같이 프로젝트 설정에서 읽고 설정이 없으면 50을 쓴다. */
  const captionMinTokens = project.data?.caption_min_tokens ?? 50;
  const editedTokens = estimateTokens(editedBrief);
  const editedTooShort = derivedVerdict === "modify_and_approve" && editedTokens < captionMinTokens;
  const canSubmit = heldByMe && !!latest && !decide.isPending && !rejectReasonMissing && !editedTooShort;
  function handleSubmit() {
    if (!canSubmit) return;
    // 판정이 초안을 소비하므로 대기 중인 디바운스 저장이 판정과 겹치지 않게 멈춘다.
    finishing.current = true;
    window.clearTimeout(draftTimer.current);
    // 판정의 대상은 화면이 보여 준 제출본(`latest`)이라 그 라벨 ID를 기준으로 싣는다.
    // 서버는 최신 라벨과 다르면 409로 거절한다.
    const base = latest!.label_id;
    if (derivedVerdict === "approve") decide.mutate({ decision: "approve", base_label_id: base });
    else if (derivedVerdict === "reject") {
      decide.mutate({
        decision: "reject", reason: reasonNote || undefined, reason_codes: rejectCodes,
        flagged_sentences: flaggedTexts, base_label_id: base,
      });
    }
    else {
      // 수정 후 승인은 문장 표시(`marks`)를 얹은 `brief_sentences`를 보낸다.
      decide.mutate({
        decision: "modify_and_approve",
        modified_label_data: buildModifiedLabelData(),
        reason: modifyReason(markCounts),
        base_label_id: base });
    }
  }

  // 영상 재생 단축키(공통: 재생·±초/프레임·배속).
  useVideoShortcuts(playerRef, { onToggleHelp: () => setShortcutsOpen((o) => !o) });


  /*
    보유하지 않은 사람에게 보이는 상태. 상태마다 이름과 이동할 화면이 다르다.
    내부 용어(`보유`·`건`)를 사유 문구에 쓰지 않는다.
  */
  const qcHeld = !!qcRun && qcRun.verdict !== "pass";
  const approval = [...(reviewHistory.data ?? [])]
    .filter((r) => r.verdict === "approve" || r.verdict === "modify_and_approve")
    .sort((a, b) => a.reviewed_at.localeCompare(b.reviewed_at))
    .at(-1);
  // 날짜·시각 표기는 `shared/lib/time`의 규칙을 쓴다. 화면마다 서식이 다르지 않게 한다.
  const reviewedOn = approval ? fmtDateTime(approval.reviewed_at) : null;
  const queueLink = { to: "/review", label: "검수 대기열로" };
  const readOnlyView: { title: string; note: string; link: { to: string; label: string } | null } =
    item.data?.status === "approved"
      ? {
          title: "승인된 작업입니다.",
          note: reviewedOn ? `${reviewedOn}에 승인되었습니다. 판정할 것이 없습니다.` : "판정할 것이 없습니다.",
          // 품질검수가 보류한 건은 완료 목록에 없으므로 완료 목록으로 안내하지 않는다.
          link: project.data?.slug && !qcHeld
            ? { to: `/p/${project.data.slug}/completed/${id}`, label: "완료 클립에서 보기" }
            : null,
        }
      : item.data?.status === "cancelled"
          ? { title: "종료된 작업입니다.", note: "비식별화 신고 제외나 영상 삭제로 닫혀 판정할 수 없습니다.", link: queueLink }
          : item.data?.status !== "submitted"
            ? {
                title: "검수 대기가 아닌 작업입니다.",
                note: (item.data?.rejection_count ?? 0) > 0
                  ? "반려되어 라벨링으로 돌아간 작업입니다. 다시 제출되면 검수할 수 있습니다."
                  : "아직 제출되지 않은 작업입니다. 제출되면 검수할 수 있습니다.",
                link: queueLink,
              }
            : item.data?.claimed_by && item.data.claimed_by === myUserId && !item.data.review_claimed_by
              ? {
                  /* 서버는 호출한 사용자가 라벨링한 작업의 판정을 막는다(`cannot_review_own_work`).
                     그 작업은 다음 검수로도 받을 수 없으므로 받는 방법을 안내하지 않는다. */
                  title: "직접 라벨링한 작업입니다.",
                  note: "자기가 라벨링한 작업은 검수할 수 없습니다. 다른 검수자가 판정합니다.",
                  link: queueLink,
                }
            : item.data?.review_claimed_by
              ? {
                  title: "다른 검수자가 맡은 작업입니다.",
                  note: "판정은 맡은 사람만 할 수 있습니다.",
                  link: queueLink,
                }
              : {
                  title: "아직 아무도 맡지 않은 작업입니다.",
                  note: "검수는 다음 검수 버튼으로 받은 작업만 판정합니다.",
                  link: queueLink,
                };

  if (item.isLoading) return <WorkbenchSkeleton />;
  // 오류 화면에 재시도를 준다. 일시 오류(429·5xx·타임아웃)를 영구 실패로 처리하지 않는다.
  // 403은 권한 문제이고, 사유는 서버가 알려 준다.
  // 막히는 이유는 검수 역할이 없거나(`not_reviewer`) 멤버가 아니기 때문이다.
  // 검수자·관리자는 다른 사람의 건도 읽기 전용으로 열 수 있다. 그래서 "직접 받은 검수 건만 열 수 있다"고 쓰지 않는다.
  if (item.error) {
    const err = item.error as unknown as ApiError | null;
    const why = errorText(err?.detail, { message: "이 검수 작업을 볼 권한이 없습니다" });
    // 첫 문장과 안내를 나눠 넘긴다. 이어 붙이면 결과와 할 일이 한 문장이 된다.
    // 머리 줄은 본 화면과 같은 뒤로 가기를 둔다.
    return (
      <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
        <WorkbenchHeader badge="검수" />
        <div className="flex min-h-0 flex-1 items-center justify-center">
          {err?.status === 403 ? (
            <AccessDenied message={why.message} hint={why.hint ?? null} />
          ) : (
            // 없는 작업(404)은 다시 시도해도 결과가 같다. 실패 객체를 넘기면 재시도 대신 사유를 보여 준다.
            <ErrorState message={err?.status === 404 ? "작업 없음" : "작업 조회 실패"} onRetry={() => void item.refetch()} error={err ?? undefined} />
          )}
        </div>
      </div>
    );
  }
  if (!item.data) return <WorkbenchSkeleton />;

  // 라벨링 워크벤치와 같은 표기. 60초가 넘으면 타임코드로 쓴다(기준은 `shared/lib/fmtSecs`).
  const clipRange = clip.data ? fmtClipRange(clip.data.start_seconds, clip.data.end_seconds) : null;

  /* 머리 줄 가운데 묶음. 이전 반려 횟수를 둔다. 검수자는 한 건씩 맡고 보유에는 끝나는 시각이 없어 순번과 남은 시간을 쓰지 않는다. */
  const stats: WbStat[] = item.data.rejection_count > 0
    ? [{ key: "rejected", label: "이전 반려", value: `${item.data.rejection_count}회` }]
    : [];
  const tools: WbAction[] = [
    { key: "guide", icon: <BookOpen size={16} aria-hidden="true" />, label: "가이드", onClick: toggleGuide, active: guideOpen },
    /* 신고는 검수를 보유한 사람만 낼 수 있다(서버 `report_deident_miss`의 `holds_review`).
       보유하지 않은 사람에게 열어 두면 사유를 다 적은 뒤 403으로 막힌다.
       검수의 신고는 하나뿐이라 메뉴로 접지 않는다. */
    ...(heldByMe
      ? [{ key: "report", icon: <ShieldAlert size={16} aria-hidden="true" />, label: "비식별화 신고", onClick: openReport }]
      : []),
    { key: "shortcuts", icon: <Keyboard size={16} aria-hidden="true" />, label: "단축키", onClick: () => setShortcutsOpen(true), compact: true },
  ];

  const clipLen = clip.data ? `${(clip.data.end_seconds - clip.data.start_seconds).toFixed(1)}초` : null;
  const round = labels.data?.length ? `${labels.data.length}차 제출` : null;
  /** 판정 영역 머리의 근거 수. 판정은 문장 표시에서 자동으로 정해지므로 그 근거를 함께 보여 준다. */
  const verdictBasis = [
    markCounts.flag > 0 ? `지적 ${markCounts.flag}` : null,
    markCounts.edit > 0 ? `수정 ${markCounts.edit}` : null,
    markCounts.del > 0 ? `삭제 ${markCounts.del}` : null,
    `확인 ${markCounts.ok}`,
    unreviewed > 0 ? `미검토 ${unreviewed}` : null,
  ].filter(Boolean).join(" · ");

  // 전체 화면 검수 워크벤치. 왼쪽은 영상 무대, 오른쪽은 제출 라벨과 바닥 고정 판정 영역이다.
  return (
    <div data-surface="workbench" className="fixed inset-0 z-workbench flex flex-col bg-canvas text-label">
      <WorkbenchHeader
        badge="검수"
        /* 검수 대상은 영상 제목으로 보여 준다. 라벨링 머리와 같은 순서다. */
        title={clip.data?.video_title ?? undefined}
        subtitle={[clipRange ? `클립 ${clipRange}` : null, clipLen, round].filter(Boolean).join(" · ")}
        stats={stats}
        /* 초안 저장 상태. 저장 실패를 표시하는 위치다. 검수를 점유한 사람에게만 보인다. */
        status={
          heldByMe ? (
            <WbSaveStatus state={draftState} at={draftAt} idleText="입력하면 자동 저장" onRetry={retryDraft} />
          ) : undefined
        }
        actions={tools}
        primary={
          heldByMe
            ? {
                label: decide.isPending ? "제출 중…" : `${verdictView.l} 제출`,
                onClick: handleSubmit,
                disabled: !canSubmit,
                tone: "submit",
              }
            : undefined
        }
      />


      <ReportDeidentModal
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        onSubmit={(reason) => report.mutate(reason)}
        pending={report.isPending}
        presentation="drawer"
        scope="review"
        positionMs={reportAtMs}
        clipName={clip.data?.video_title}
        clipRange={clip.data ? fmtClipRange(clip.data.start_seconds, clip.data.end_seconds) : null}
      />

      {/* ── 본문: lg 이상은 왼쪽 영상과 오른쪽 레일, lg 미만은 영상 아래에 레일을 쌓는다(라벨링과 같다) ── */}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        {/* 왼쪽: 무대. 제목·구간은 머리 줄에 이미 있으므로 칩을 올리지 않는다. */}
        <div className="flex min-w-0 flex-col gap-4 p-4 lg:flex-1">
          <div className="flex min-h-[40vh] flex-1 items-center justify-center overflow-hidden rounded-card bg-ws-stage lg:min-h-0">
            <div className="relative mx-auto flex aspect-video h-full max-h-full max-w-full items-center justify-center">
              <VideoPlayer
                hideTitle
                videoId={item.data.video_id}
                playerRef={playerRef}
                startSeconds={clip.data?.start_seconds}
                endSeconds={clip.data?.end_seconds}
                rangePending={!!item.data.clip_id && clip.isPending}
                disableBuiltinKeys
              />
            </div>
          </div>
        </div>

        {/* 오른쪽: 검수 레일 */}
        <aside className="flex w-full flex-col border-l border-separator bg-canvas-2 lg:w-[416px] lg:shrink-0 lg:overflow-hidden">

          {/* 판정 영역. 레일 바닥에 고정한다(`order-1`). 위에서 문장을 판정하고 아래에서 결과를 확인하는 순서가 라벨링의 `제출 전 확인`과 같다.
              판정은 문장 트리아지에서 자동으로 정하고 이 영역은 표시만 한다.
              판정 화면에 제출자 실적을 두지 않으므로 제출자 정보(익명 ID)도 두지 않는다.
              높이에 상한을 두어 반려 사유·보충 설명이 붙어도 검수할 캡션이 밀리지 않게 한다. */}
          <section className="order-1 shrink-0 border-t border-separator px-5 py-4 lg:max-h-[45%] lg:overflow-y-auto">
            {!heldByMe ? (
              /*
                검수를 점유하지 않은 사람에게는 판정 대신 상태를 보여 준다. 자동 도출 판정은 지금 누르면 그대로
                제출된다는 뜻이라 누를 수 없는 사람에게는 잘못된 표시가 된다.
              */
              <div className="flex items-start gap-2.5 text-label-2">
                <Eye size={16} className="mt-0.5 shrink-0 text-label-3" aria-hidden="true" />
                <div className="min-w-0">
                  <span className="sr-only">읽기 전용. </span>
                  <p className="text-footnote font-semibold text-label">{readOnlyView.title}</p>
                  <p className="mt-0.5 text-footnote text-label-2">{readOnlyView.note}</p>
                  {readOnlyView.link && (
                    <Link
                      to={readOnlyView.link.to}
                      className="mt-1.5 inline-block text-footnote font-semibold text-label underline-offset-2 hover:underline"
                    >
                      {readOnlyView.link.label}
                    </Link>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div className="mb-2 flex items-baseline justify-between gap-2">
                  <h4 className="text-caption font-semibold text-label-2">판정</h4>
                  {captionSentences.length > 0 && (
                    <span className="text-footnote tabular-nums text-label-3">{verdictBasis}</span>
                  )}
                </div>
                {/* 자동 판정을 큰 글자 하나와 설명 한 줄로 보여 준다. 반려면 몇 번째 반려인지 쓴다. */}
                <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                  <span className="flex items-center gap-2">
                    <span
                      className={cn(
                        "size-2.5 shrink-0 rounded-capsule",
                        derivedVerdict === "reject" ? "bg-bad" : derivedVerdict === "approve" ? "bg-ok" : "bg-label-2",
                      )}
                      aria-hidden="true"
                    />
                    <span className="text-headline font-bold text-label">{verdictView.l}</span>
                  </span>
                  {derivedVerdict === "reject" ? (
                    <span className="text-footnote tabular-nums text-label-2">
                      {`${markCounts.flag}개 문장 지적 · 반려 ${rejectionCount + 1}회째`}
                    </span>
                  ) : (
                    verdictView.desc && <span className="text-footnote text-label-2">{verdictView.desc}</span>
                  )}
                </div>

                {/* 반려 영역. 지적 문장은 반려에 함께 실리고, 사유 칩이나 보충 설명 중 하나가 있어야 제출된다. */}
                {derivedVerdict === "reject" && (
                  <div className="mt-3">
                {/* 사유 칩은 서버 목록이다. 꺼진 사유는 새 반려에 쓸 수 없지만, 이미 고른 칩이 그사이 꺼졌으면
                    해제할 수 있게 남긴다(서버는 꺼진 코드를 422로 거절한다).
                    목록을 불러오지 못했으면 사유가 없다고 표시하지 않고 실패로 표시한다. */}
                {project.isError && (
                  <div className="mb-1.5">
                    <ErrorState inline message="반려 사유 목록 조회 실패" onRetry={() => void project.refetch()} />
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {(project.data?.reject_reasons ?? [])
                    .filter((r) => r.enabled || rejectCodes.includes(r.code))
                    .map((r) => {
                      const on = rejectCodes.includes(r.code);
                      return (
                        <button
                          key={r.code}
                          type="button"
                          aria-pressed={on}
                          onClick={() =>
                            setRejectCodes((s) => (s.includes(r.code) ? s.filter((x) => x !== r.code) : [...s, r.code]))
                          }
                          className={
                            "inline-flex h-7 items-center gap-1 rounded-capsule border px-2.5 text-footnote transition-colors " +
                            (on ? "border-bad bg-bad-muted font-semibold text-bad-text" : "border-separator-strong text-label-2 hover:bg-fill hover:text-label")
                          }
                        >
                          {on && <Check size={12} aria-hidden="true" />}
                          {r.label}
                        </button>
                      );
                    })}
                </div>
                <textarea
                  value={rejectExtra}
                  onChange={(e) => setRejectExtra(e.target.value)}
                  rows={2}
                  maxLength={REASON_MAX}
                  placeholder="보충 설명"
                  aria-label="보충 설명"
                  aria-describedby="reject-extra-count"
                  /* 공용 `FIELD_BASE`가 포커스 때 테두리 accent와 얇은 링을 함께 준다(`ui/field`).
                     조밀한 위치라 면색·글자 크기만 덮어쓴다.
                     `focus:outline-none`만 주고 대체 표시가 없으면 키보드로 이동할 때 포커스 위치가 보이지 않는다. */
                  className={cn(
                    FIELD_BASE,
                    "mt-2 resize-none border-separator-strong bg-canvas px-3 py-2 text-footnote",
                  )}
                />
                {/* 글자 수는 한도가 가까울 때 경고색으로 둔다. 한도는 입력란이 막는다. */}
                <p
                  id="reject-extra-count"
                  className={cn(
                    "mt-1 text-right text-micro tabular-nums",
                    rejectExtra.length >= REASON_MAX * 0.9 ? "text-warn-text" : "text-label-3",
                  )}
                >
                  {rejectExtra.length.toLocaleString("ko-KR")} / {REASON_MAX.toLocaleString("ko-KR")}자
                </p>
                {/* 반려에는 수정한 문장이 실리지 않는다. 서버의 반려 분기는 `modified_label_data`를 읽지 않고, 반려된 건은 라벨러가 직접 고친다.
                    그래서 수정본을 버린다는 사실을 표시한다. 표시하지 않으면 검수자는 고친 문장이 라벨러에게 전해진다고 여긴다. */}
                {markCounts.edit + markCounts.del > 0 && (
                  <p className="mt-2 text-footnote text-warn-text">
                    수정·삭제 표시 {markCounts.edit + markCounts.del}개는 반려와 함께 저장되지 않습니다.
                    지적 문장과 사유만 전달되고 라벨러가 직접 고칩니다.
                  </p>
                )}
                {/* 사유가 모자라면 제출 버튼이 잠긴 이유를 이 위치에서 알린다. 굵은 결과와 작은 안내로 나눈다. */}
                {rejectReasonMissing ? (
                  <div role="status" className="mt-2 text-footnote">
                    <p className="font-semibold text-warn-text">반려 사유 필요</p>
                    <p className="text-label-2">사유를 고르거나 보충 설명을 적어야 제출됩니다.</p>
                  </div>
                ) : (
                  <p className="mt-2 text-footnote text-label-3">지적한 문장은 반려에 함께 실립니다.</p>
                )}
                  </div>
                )}

            {/* 확인하지 않은 문장이 남았을 때의 안내. 확인하지 않은 문장은 승인에 그대로 실리고 제출을 막지 않는다.
                `buildModifiedLabelData`가 손대지 않은 문장을 그대로 두므로 `수정 후 승인`에서도 보여 준다.
                반려하면 건이 통째로 라벨러에게 돌아가 "그대로 승인"이 사실과 다르므로 반려에서는 보여 주지 않는다. */}
            {derivedVerdict !== "reject" && unreviewed > 0 && (
              <p className="mt-2 text-footnote text-label-3">
                확인하지 않은 문장 {unreviewed}개도 그대로 승인됩니다.
              </p>
            )}
            {/* 수정본이 캡션 기준에 못 미치면 제출 버튼이 잠긴 이유를 이 위치에서 알린다. 굵은 결과와 작은 안내로 나눈다. */}
            {editedTooShort && (
              <div role="status" className="mt-2 text-footnote">
                <p className="font-semibold text-warn-text">캡션 기준 미달</p>
                <p className="text-label-2 tabular-nums">
                  수정한 캡션은 {editedTokens}토큰입니다. {captionMinTokens}토큰 이상이어야 제출됩니다.
                </p>
              </div>
            )}

            {/* 오류 문구는 공용 사전(`errorText`)이 정한다.
                화면마다 따로 사전을 두면 모르는 코드가 영문 원문 그대로 뜬다. */}
            {decide.isError && (() => {
              const err = decide.error as ApiError | null;
              /* 사전의 `caption_too_short` 안내는 워크벤치 제출 조건을 가리킨다.
                 검수 화면에서는 수정한 문장을 고치도록 안내한다. */
              const t = err?.detail === "caption_too_short"
                ? { message: "수정한 캡션이 기준보다 짧습니다", hint: "기준이 바뀌었을 수 있습니다. 삭제한 문장을 되돌리거나 문장을 보충한 뒤 다시 제출해주세요." }
                : errorText(err?.detail);
              /* 422는 서버가 틀린 필드와 이유를 알려 준다(꺼진 사유 등).
                 그래서 일반 안내 대신 그 사유를 보여 준다. */
              const why = err?.fields?.[0]?.message ?? t.hint;
              return (
                /* 굵은 결과, 사유, 안내를 줄로 나눈다. 결과와 사유를 한 문장에 잇지 않는다. */
                <div role="alert" className="mt-2 rounded-card bg-bad-muted px-2.5 py-1.5 text-bad-text">
                  <p className="text-micro font-semibold">판정 제출 실패</p>
                  <p className="mt-0.5 text-micro">{t.message}</p>
                  {why && <p className="mt-0.5 text-micro">{why}</p>}
                </div>
              );
            })()}

              </>
            )}
          </section>

          {/* 레일 본문. 읽기가 우선이라 스크롤 영역으로 둔다. */}
          <div className="flex-1 pb-5 lg:min-h-0 lg:overflow-y-auto">
              {/* 품질검수 근거. 판정 전에 자동 검사가 확인한 결과를 문장 위에 둔다. */}
              <div className="px-5 pt-5">
            {/* 불러오지 못한 경우, 실행되지 않은 경우, 실행됐는데 지적이 없는 경우를 구분한다.
                실패는 다시 시도와 함께 표시하고, `null`은 기록이 없다고 표시한다.
                비워 두면 "지적 없음"과 같아 보여 지적이 있는 건을 그대로 승인할 수 있다. */}
            {qcQuery.isError && (
              <div className="mb-2.5">
                <ErrorState inline message="품질검수 결과 조회 실패" onRetry={() => void qcQuery.refetch()} />
              </div>
            )}
            {/* 검사 중과 기록 없음을 구분한다. 제출 직후 연 화면에서 `기록 없음`이라고 하면
                검사하지 않는 프로젝트로 읽혀 검수자가 결과를 기다리지 않고 판정한다. */}
            {qcQuery.data?.state === "running" && (
              <div className="mb-2.5 rounded-card border border-dashed border-separator px-3 py-2">
                <span className="text-footnote text-label-2">품질검수 중</span>
                <p className="mt-0.5 text-micro text-label-3">결과가 나오면 이 위치에 표시합니다.</p>
              </div>
            )}
            {qcQuery.data?.state === "failed" && (
              <div className="mb-2.5 rounded-card border border-dashed border-separator px-3 py-2">
                <span className="text-footnote text-label-2">품질검수 실행 실패</span>
                <p className="mt-0.5 text-micro text-label-3">여러 번 시도했지만 검사 결과를 남기지 못했습니다.</p>
              </div>
            )}
            {qcQuery.data?.state === "none" && (
              <div className="mb-2.5 rounded-card border border-dashed border-separator px-3 py-2">
                <span className="text-footnote text-label-3">품질검수 기록 없음</span>
                <p className="mt-0.5 text-micro text-label-3">품질검수를 사용하지 않는 프로젝트이거나 검사 기능이 생기기 전의 작업입니다.</p>
              </div>
            )}
            {/* 검사가 실행됐고 지적이 없었다는 사실도 기록이다.
                지적이 있을 때만 그리면 실행되지 않은 경우와 실행됐는데 지적이 없는 경우가 똑같아 보인다. */}
            {qcRun && (qcRun.findings?.length ?? 0) === 0 && (
              <div className="mb-2.5 rounded-card border border-separator px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-footnote text-label-2">품질검수 지적 없음</span>
                  <span className="text-micro text-label-3">
                    {qcRun.model ? `모델 ${qcRun.model}` : "규칙 검사"}
                  </span>
                </div>
                {/* 통과의 근거가 된 단계를 함께 표시한다. "지적 없음"만 있으면 실행되지 않은 단계까지 통과로 읽힌다. */}
                <QcStages stages={qcRun.stages} />
              </div>
            )}

            {/* 품질검수 근거. 품질검수는 반려하지 않으므로 이 카드는 판정 전에 보는 참고 자료다.
                문장에 붙는 지적은 위 문장 카드가 그 문장 아래에 그리고, 이 카드에는 건수·단계 요약과 문장에 붙지 않는 지적만 남는다. */}
            {qcFindings.length > 0 && (
              /* `info`만 있으면 경고색을 쓰지 않는다. `info`는 판정에 반영되지 않는 지적(선택 항목 누락, `caption_qc/rules.Severity`)이라
                 노란 카드로 그리면 보류된 것처럼 읽힌다. `warn`·`error`가 하나라도 섞이면 경고색을 쓴다.
                 카드는 한 줄 요약으로 접어 두고, 문장에 붙지 않는 `error` 지적이 있을 때만 처음부터 편다.
                 항상 펴 두면 레일 높이를 많이 차지해 검수할 캡션이 밀린다. */
              <div
                className={
                  "mb-2.5 rounded-card border px-3 py-2.5 " +
                  (qcFindings.some((f) => f.severity !== "info")
                    ? "border-warn bg-warn-muted"
                    : "border-separator")
                }
              >
                <button
                  type="button"
                  onClick={() => setQcOpen(!qcCardOpen)}
                  aria-expanded={qcCardOpen}
                  className="flex w-full items-center justify-between gap-2 text-left"
                >
                  <span
                    className={
                      "text-footnote font-semibold " +
                      (qcFindings.some((f) => f.severity !== "info")
                        ? "text-warn-text"
                        : "text-label-2")
                    }
                  >
                    품질검수 지적 {qcFindings.length}건
                  </span>
                  {/* 판정한 검사 방식을 밝힌다. 규칙 검사와 모델 검사는 신뢰도가 다르다. */}
                  <span className="flex items-center gap-1 text-micro text-label-3">
                    {qcRun!.model ? `모델 ${qcRun!.model}` : "규칙 검사"}
                    <ChevronDown
                      size={12}
                      aria-hidden="true"
                      className={"transition-transform " + (qcCardOpen ? "rotate-180" : "")}
                    />
                  </span>
                </button>
                {qcCardOpen && (<>
                {/* 분모를 밝힌다. 나머지 지적은 문장 카드에 있어 이 목록이 짧다.
                    문장 카드에 있는 수를 적지 않으면 건수와 목록 길이가 맞지 않아 보인다. */}
                {qcOnCards > 0 && (
                  <p className="mt-1 text-micro text-label-3">
                    문장 지적 {qcOnCards}건은 해당 문장 카드에 있습니다
                  </p>
                )}
                {qcRest.length > 0 && (
                  <ul className="mt-1.5 space-y-1">
                    {qcRest.map((f, i) => (
                      <li key={i} className="text-caption text-label-2">
                        {/* 걸린 단계를 표시한다. 규칙 검사와 모델 검사는 신뢰도가 달라 색도 나눈다.
                            단계 이름은 서버가 준다(`check_label`). 화면에 사전을 따로 두면 검사를 더할 때 두 사전이 달라진다. */}
                        <span
                          className={
                            WB_CHIP_TIGHT + " mr-1.5 " +
                            (f.source === "model" ? "bg-accent-muted text-accent" : "bg-fill-2 text-label-3")
                          }
                        >
                          {f.check_label || f.check}
                        </span>
                        {f.message}
                        {/* 가리키는 문장이 삭제됐거나 그사이 라벨이 바뀌어 목록에 없으면 대상 문장이 없다고 표시한다. */}
                        {f.sentence_id && (
                          <span className="ml-1 text-micro text-label-3">(대상 문장 없음)</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {/* 걸린 지적 아래에 확인한 단계를 표시한다. 지적만 보면 나머지 단계를 알 수 없다. */}
                <QcStages stages={qcRun!.stages} />
                </>)}
              </div>
            )}

              </div>

              <div className="px-5 pt-3">
                <div className="mb-2.5 flex items-center justify-between gap-2">
                  {/* 제출 차수는 머리 줄 둘째 줄에 있다(`N차 제출`). */}
                  <h4 className="text-caption font-semibold text-label-2">캡션</h4>
                </div>
                {latest ? (
                  <ReadOnlyLabel
                    data={latest.label_data}
                    fontSize={CAPTION_FONT_SIZE}
                    captionTitle={false}
                    captionSlot={
                      <SentenceTriage
                        key={id}
                        sentences={captionSentences}
                        fontSize={CAPTION_FONT_SIZE}
                        marks={marks}
                        onMark={setMark}
                        findings={qcBySentence}
                        readOnly={!heldByMe}
                      />
                    }
                  />
                ) : (
                  <EmptyState dense message="제출 라벨 없음" />
                )}
              </div>
              {/* 영상 메타는 관리자가 넣은 값이다. 라벨링과 같은 부품을 쓴다. */}
              {clip.data?.video_meta && (
                <WbVideoMeta meta={clip.data.video_meta} />
              )}
          </div>
        </aside>

        {/* 작업 가이드 서랍. 배경막 없이 레일 위에 겹쳐 영상은 계속 보인다. */}
        <GuidePanel open={guideOpen} onClose={() => setGuideOpen(false)} project={project.data} />
      </div>

      <WbShortcutsDialog
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
        title="단축키 · 검수"
        columns={[[{ title: "재생", items: VIDEO_SHORTCUTS }]]}
      />
    </div>
  );
}


// `ReadOnlyLabel`은 `components/ReadOnlyLabel.tsx`에 있다(검수·내 라벨링 기록 상세가 함께 쓴다).
