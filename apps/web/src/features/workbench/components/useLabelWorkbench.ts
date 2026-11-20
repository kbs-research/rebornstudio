/**
 * 라벨링 워크벤치의 로직 훅. 상태·쿼리·뮤테이션·이펙트·핸들러를 한 곳에 모은다.
 *
 * 뷰(`LabelWorkbench`)는 이 훅의 값을 받아 그리기만 한다. 편집 잠금 조건(`locked`)과 제출 확인도 이 훅이 정한다.
 * 뷰가 조건을 다시 쓰면 같은 규칙이 두 곳에 생긴다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MediaPlayerInstance } from "@vidstack/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useToast } from "@/shared/ui/Toast";
import { useSyncProjectContext } from "@/hooks/useSlugProject";
import { useUnsavedGuard } from "@/shared/hooks/useUnsavedGuard";
import type { ApiError } from "@/shared/api/client";
import { fetchClip, libraryKeys } from "@/features/library";
import { invalidateHolding } from "@/features/me";
import { pipelineKeys } from "@/features/pipeline";
import { useProject } from "@/features/projects";
import { errorText } from "@/shared/api/error_text";
import { HOLD_LOST_SAVE, isHoldLost, lostWhileOpen } from "../lib/hold_lost";
import { hit, LABEL_SHORTCUTS } from "../lib/shortcuts";
import { ownsActivationKey, useVideoShortcuts } from "../lib/useVideoShortcuts";
import { useAuth } from "@/store/auth";
import { hasProjectRole, MANAGER_ROLES } from "@/lib/roles";
import {
  type CaptionSentence, joinSentences, sentencesOf, toSentences,
} from "../lib/caption";
import { afterReleaseHint, labelWorkbenchHome } from "../lib/home";
import { pickInitialContent, withSavedDraft } from "../lib/initial_content";
import { estimateTokens } from "../lib/tokens";
import type { components } from "@reborn/contracts";

import {
  fetchWorkItem,
  fetchWorkLabels,
  fetchWorkReviews,
  reportDeidentMiss,
  saveWorkDraft,
  submitWork,
  workbenchKeys,
} from "../api";

export type WorkItemDTO = components["schemas"]["WorkItemDTO"];
export type LabelDTO = components["schemas"]["LabelDTO"];
export type ReviewDTO = components["schemas"]["ReviewDTO"];
export type ClipDTO = components["schemas"]["ClipDTO"];

/**
 * 이전 형식 클립 메타 필드 다섯 개(방송사·프로그램·장르·방송일·주제)의 상태. 화면에는 입력란이 없다.
 *
 * 영상 단위 메타는 관리자가 영상 상세에서 입력하고, 화면은 클립 조회(`clip.video_meta`)에서 읽기만 한다.
 * 이 필드로 저장된 이전 제출본을 다시 열어 제출할 때 값을 그대로 실으려고 상태를 둔다.
 * 싣지 않으면 다시 여는 것만으로 다른 사람의 입력이 지워진다. 훅 밖으로는 내보내지 않는다.
 */
export interface MetaState {
  broadcaster: string; program: string; genre: string; air_date: string; topic: string;
}
const EMPTY_META: MetaState = { broadcaster: "", program: "", genre: "", air_date: "", topic: "" };

/** 임시저장 초안과 제출 라벨에서 워크벤치가 읽는 모양. */
type SavedContent = {
  caption?: { brief?: string; brief_sentences?: CaptionSentence[] };
  meta?: Partial<MetaState>;
};

/** 라벨링 워크벤치의 상태와 로직. work_item 로딩부터 캡션 작성·검증·제출까지 다룬다. */
export function useLabelWorkbench() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const playerRef = useRef<MediaPlayerInstance>(null);

  const item = useQuery({
    queryKey: workbenchKeys.item(id),
    queryFn: () => fetchWorkItem(id),
    enabled: !!id,
    // 실패는 화면이 직접 그린다(접근 권한 없음·다시 시도). 전역 토스트를 겹쳐 띄우지 않는다.
    meta: { handled: true },
  });
  /*
    딸린 조회는 작업을 받은 뒤에 보낸다. `item`이 403이면 딸린 조회도 403이다.
    화면은 이미 "접근 권한 없음"을 그리고 있어서, 전역 토스트가 조회 수만큼 겹쳐 뜨면 상황과 맞지 않는 안내가 여러 번 나간다.
  */
  const labels = useQuery({
    queryKey: workbenchKeys.labels(id),
    queryFn: () => fetchWorkLabels(id),
    enabled: !!id && !!item.data,
    // 실패는 캡션 위에 화면이 직접 그린다(`captionSourceFailed`). 전역 토스트를 겹쳐 띄우지 않는다.
    meta: { handled: true },
  });
  const reviews = useQuery({
    queryKey: workbenchKeys.reviews(id),
    queryFn: () => fetchWorkReviews(id),
    enabled: !!id && !!item.data,
  });
  const clipId = item.data?.clip_id;
  const clip = useQuery({
    queryKey: libraryKeys.clip(item.data?.project_id, clipId),
    queryFn: () => fetchClip(item.data!.project_id, clipId),
    enabled: !!clipId && !!item.data,
  });
  /* 연 작업이 속한 프로젝트로 전환기를 맞춘다.
     맞추지 않으면 받은 링크로 B 프로젝트 작업을 열고 헤더의 `내 작업`을 누를 때 A의 큐가 뜬다. */
  useSyncProjectContext(item.data?.project_id);

  // 프로젝트 설정(caption_min_tokens 등). 캡션 제출 조건의 기준값을 이 설정에서 읽는다.
  const project = useProject(item.data?.project_id);

  // ---- 편집 상태 ----
  /**
   * 캡션 상태. 문장 배열이 원본이다.
   *
   * 검수·내보내기·토큰 수 계산이 읽는 문자열(`brief`)은 `buildPayload`가 `joinSentences`로 만들어 함께 저장하는 파생값이다.
   */
  const [caption, setCaption] = useState<{ brief: CaptionSentence[] }>({ brief: [] });
  const [meta, setMeta] = useState<MetaState>(EMPTY_META);
  const dirty = useRef(false);
  /** 이 화면에서 마지막으로 저장에 성공한 초안 본문. 작업을 놓으면 `null`이다. */
  const savedDraft = useRef<object | null>(null);
  /** 화면이 떠 있으면 true. 떠난 뒤 끝난 임시저장은 캐시에 직접 싣는다. */
  const mounted = useRef(true);
  /*
    떠날 때 마지막으로 저장한 초안을 작업 캐시에 싣는다.
    이 작업으로 돌아오면 새 화면이 캐시의 초안을 바로 복원하는데, 임시저장은 캐시를 고치지 않는다.
    싣지 않으면 캐시가 유효한 동안 이전 초안이 복원되고, 그 위에 이어 쓴 편집이 저장한 초안을 덮는다.
    저장하지 않은 편집은 싣지 않는다. 실으면 캐시에 서버에 없는 값이 생긴다.
  */
  useEffect(() => {
    mounted.current = true;
    const wid = id;
    return () => {
      mounted.current = false;
      const body = savedDraft.current;
      if (wid && body) qc.setQueryData<WorkItemDTO>(workbenchKeys.item(wid), (old) => withSavedDraft(old, body));
    };
  }, [id, qc]);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  /** 비식별화 신고 창을 연 순간의 재생 위치(클립 기준 ms). 창이 보여 주는 값과 제출하는 값이 같다. */
  const [reportAtMs, setReportAtMs] = useState<number | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  /** 제출 확인 창. 제출은 되돌릴 수 없어서 한 번 더 확인한다. */
  const [submitConfirmOpen, setSubmitConfirmOpen] = useState(false);
  const { toast } = useToast();
  // 빈 캡션에 채운 AI 초안의 원문. 값이 있으면 AI 초안으로 채웠다고 안내한다.
  const [seedSource, setSeedSource] = useState<string | null>(null);

  /* 작업을 보유한 사람만 편집한다. 화면과 서버의 판정이 달라지지 않게 서버와 같은 기준으로 판정한다.
     읽기와 쓰기는 조건이 다르다. `GET /work/{id}`는 본인 claim·reviewer 이상·super_admin에게 열리고(`_require_work_viewer`),
     저장·제출은 본인 claim 또는 super_admin이면서 상태가 `claimed`여야 한다(`_load_working_claim`).
     이 구분이 없으면 다른 사람의 작업을 연 검수자·프로젝트 관리자에게 편집 가능한 화면이 뜨고 저장할 때마다 403으로 실패한다.
     서버가 함께 보는 참여 여부(`require_participation`)는 `WorkItemDTO`에 없어 화면에서 확인할 수 없다.
     멤버에서 빠진 사람의 보유는 서버가 풀고, 그 사이 열려 있던 화면의 저장 실패는 토스트 문구로 이유를 알린다. */
  const myUserId = useAuth((st) => st.user?.user_id);
  const isSuperAdmin = useAuth((st) => st.user?.global_role === "super_admin");
  const me = useAuth((st) => st.user);
  /** 관리자(최고관리자 포함)이면 true. 돌아갈 곳과 읽기 전용 문장이 이 값에 따라 달라진다. */
  const manages = hasProjectRole(me, project.data, MANAGER_ROLES);
  const home = labelWorkbenchHome(manages, project.data?.slug);
  /** 보던 작업의 보유가 끝나 다시 받기가 403이면 true. 화면은 `접근 권한 없음` 대신 보유가 끝났다고 그린다. */
  const holdLost =
    item.isError && lostWhileOpen((item.error as unknown as ApiError | null)?.status, item.data?.claimed_by, myUserId);
  const canEdit =
    !!item.data &&
    item.data.status === "claimed" &&
    (isSuperAdmin || (!!myUserId && item.data.claimed_by === myUserId));
  /**
   * 편집이 잠겼으면 true. 보유하지 않은 사람의 화면은 읽기 전용이다. 보유에는 끝나는 시각이 없다.
   *
   * 뷰의 컨트롤과 이 훅의 핸들러가 모두 이 값을 본다. 뷰는 `fieldset disabled`로 막지만 키보드 단축키는 fieldset의 영향을 받지 않는다.
   * 핸들러가 이 값을 보지 않으면 읽기 전용으로 연 검수자가 단축키로 캡션을 바꿔 dirty가 켜지고, 고친 것이 없는데 탭 닫기 경고가 뜬다.
   */
  const locked = !canEdit;

  /**
   * 제출·신고로 이 작업을 놓은 뒤 돌아갈 화면(작업자는 내 작업)으로 간다. 다음 작업은 그 화면의 버튼 하나가 연다.
   *
   * 작업을 끝내고 떠나는 화면은 방문 기록에 남기지 않는다(`replace`).
   * 남기면 뒤로 가기로 돌아온 화면이 이미 풀린 보유의 제출 조건까지 그리지만 저장은 409로 막힌다.
   *
   * @param title 토스트의 굵은 결과(`작업 1건 제출` 등). 이동할 곳은 작은 줄로 따로 쓴다.
   */
  const leaveWork = useCallback(
    (title: string) => {
      savedDraft.current = null;   // 작업을 놓았다. 떠날 때 캐시에 초안을 싣지 않는다
      void qc.invalidateQueries({ queryKey: workbenchKeys.all() });
      toast("success", title, afterReleaseHint(home.label));
      navigate(home.to, { replace: true });
    },
    [qc, toast, navigate, home.to, home.label],
  );

  // 클립별 리셋: work_item id가 바뀌면 이전 클립의 내용과 AI 초안 흔적을 먼저 비워 다음 클립에 딸려 오지 않게 한다.
  // 실제 내용(draft·직전 label 복원 또는 AI 초안)은 데이터 로드 후 아래 effect가 채운다.
  // 리셋 effect가 채우는 effect보다 먼저 선언되어야 같은 렌더에서 채운 내용을 덮지 않는다.
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    setSeedSource(null);
    setCaption({ brief: [] });
    setMeta(EMPTY_META);
    // AI 초안 가드(`seededFor`)도 캡션과 함께 비운다. 캡션은 비었는데 가드가 남으면 AI 초안이 채워지지 않는다.
    // StrictMode 이중 마운트와 캐시가 남은 재진입에서는 1차 마운트가 남긴 가드가 2차 마운트를 막을 수 있다.
    seededFor.current = null;
    // 새 클립은 아직 편집 전이다. dirty를 내려야 아래 복원 effect의 dirty 가드가 열려
    // draft·AI 초안이 실린다. 내리지 않으면 이전 클립의 dirty가 남아 새 클립이 빈 채로 뜬다.
    dirty.current = false;
  }, [id]);

  // 진입할 때 내용을 정한다. draft·직전 label 복원이 우선이고, 복원할 것이 없으면 AI 요약(scene_summary)을 초안으로 채운다.
  // 라벨러는 영상을 보고 AI 초안의 틀린 부분을 고쳐 제출한다. `seedSource`가 있으면 AI 초안으로 채웠다고 안내한다.
  // AI 초안은 클립당 한 번만 채워 이후 편집한 내용을 덮지 않는다.
  useEffect(() => {
    // 편집을 시작했으면 복원과 AI 초안이 덮지 않는다.
    // 조회 도착 순서가 정해져 있지 않아 '한 번만' 가드로는 draft가 AI보다 우선하는 순서를 지킬 수 없다.
    // 그래서 dirty로 조건을 건다. 편집 전에는 다시 실행돼도 draft가 AI보다 우선하고, 편집 후에는 아무것도 덮지 않는다.
    // `labels.data`가 새 참조가 되면(이력 드로어 등) 이 effect가 다시 실행되므로, 가드가 없으면 편집 중인 내용이 이전 draft로 돌아간다.
    if (dirty.current) return;
    const next = pickInitialContent({
      draft: item.data?.draft as SavedContent | null | undefined,
      labels: labels.data as Array<{ label_data?: SavedContent | null }> | undefined,
      aiSummary: clip.data?.scene_summary,
    });
    if (next.kind === "restore") {
      const src = next.src;
      // `sentencesOf`가 문장 배열, 이전 형식 문자열, `caption`이 문자열인 더 오래된 모양을 모두 읽는다.
      setCaption({ brief: sentencesOf(src) });
      setMeta({ ...EMPTY_META, ...src.meta });
      seededFor.current = id ?? null;  // 복원한 내용(draft·재작업)에는 AI 초안을 다시 채우지 않는다
      return;
    }
    if (next.kind === "seed" && id && seededFor.current !== id) {
      seededFor.current = id;
      setCaption({ brief: toSentences(null, next.text) });
      setSeedSource(next.text);
    }
  }, [id, item.data?.draft, labels.data, clip.data?.scene_summary]);

  /** 초안이 없는데 라벨 이력 조회가 실패해 저장된 캡션이 있는지 확인할 수 없으면 true. */
  const captionSourceFailed = pickInitialContent({
    draft: item.data?.draft,
    labels: labels.data,
    labelsFailed: labels.isError,
  }).kind === "failed";

  const buildPayload = useCallback(
    () => ({
      // 문장 배열이 원본이고 문자열은 검수·내보내기가 읽는 파생값이다. 파생값은 `joinSentences`로만 만든다.
      caption: {
        brief: joinSentences(caption.brief),
        brief_sentences: caption.brief,
      },
      meta,
    }),
    [caption, meta],
  );

  // ---- 자동저장(dirty일 때 30초 debounce) ----
  const draftMut = useMutation<WorkItemDTO, ApiError>({
    mutationFn: () => {
      const body = buildPayload();
      return saveWorkDraft(id, body).then((w) => {
        savedDraft.current = body;
        return w;
      });
    },
    onMutate: () => setSaveState("saving"),
    onSuccess: () => {
      // 떠난 뒤 끝난 저장이다. 언마운트 때 실은 캐시가 이 저장보다 낡았으므로 다시 싣는다.
      if (!mounted.current) {
        const body = savedDraft.current;
        if (id && body) qc.setQueryData<WorkItemDTO>(workbenchKeys.item(id), (old) => withSavedDraft(old, body));
        return;
      }
      // 저장에 성공하면 dirty를 내린다. 내리지 않으면 자동저장 effect가 다시 실행될 때마다
      // dirty가 참이라 30초마다 같은 payload를 계속 PATCH한다.
      dirty.current = false;
      setSaveState("saved");
      // 헤더 한 줄에 들어가도록 시·분만 적는다. 초까지 적으면 1280 폭에서 헤더가 넘친다.
      // 24시간제로 쓴다. 검수 초안 저장 시각과 같은 모양이다(`오후 06:42` 대신 `18:42`).
      setSavedAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }));
    },
    /* 실패하면 헤더가 `저장 실패 · 다시 시도`를 보여 주고 dirty는 남긴다.
       다음 자동저장과 탭 닫기 경고가 계속 편집을 보호한다.
       보유를 잃었거나(403) 작업 상태가 바뀌었으면(409) 다시 눌러도 저장되지 않는다.
       그래서 원인을 알리고 작업을 다시 받아 잠금을 서버 상태에 맞춘다. */
    onError: (e) => {
      setSaveState("error");
      if (e.status === 403 || e.status === 409) {
        const t = isHoldLost(e.detail) ? HOLD_LOST_SAVE : errorText(e.detail, { message: "임시저장 실패", hint: "잠시 후 다시 시도해주세요." });
        toast("warning", t.message, t.hint);
        void item.refetch();
      }
    },
  });
  /* 저장을 막는 조건은 이 함수 한 곳에 둔다. `saveDraft`를 부르는 곳은 30초 자동저장·`Cmd+S`·임시저장 버튼·이탈 저장이다.
     부르는 곳마다 조건을 쓰면 하나를 빠뜨린다. 편집 권한이 없는 사람의 저장 요청은 이 함수가 무시한다. */
  /* 의존성에는 `mutate`만 넣는다. `useMutation`의 결과 객체는 렌더마다 새로 만들어지므로
     객체를 넣으면 다시 그릴 때마다 아래 30초 타이머가 지워져 자동 저장이 돌지 않는다. */
  const mutateDraft = draftMut.mutate;
  const saveDraft = useCallback(() => {
    if (id && !locked) mutateDraft();
  }, [id, locked, mutateDraft]);

  useEffect(() => {
    if (!dirty.current || !canEdit) return; // 미보유면 자동저장을 건너뛴다
    const t = setTimeout(saveDraft, 30_000);
    return () => clearTimeout(t);
    // `buildPayload`는 편집할 때마다 새로 만들어지므로 편집 뒤 타이머를 다시 건다
  }, [buildPayload, saveDraft, canEdit]);

  // 저장하지 않은 편집이 있으면 탭 닫기·새로고침 때 경고한다. dirty는 이벤트 시점에 읽는다.
  // 경고가 없으면 30초 debounce 사이에 창을 닫을 때 마지막 편집이 조용히 사라진다.
  useUnsavedGuard(() => dirty.current);

  /** 상태 setter를 감싸 편집할 때 dirty를 켜는 핸들러를 만든다. 잠겼으면 값을 바꾸지 않는다. */
  function edited<T>(setter: (v: T) => void) {
    return (v: T) => {
      if (locked) return;
      dirty.current = true;
      setter(v);
    };
  }

  const submit = useMutation<WorkItemDTO, ApiError>({
    mutationFn: () => submitWork(id, buildPayload()),
    /* 실패하면 확인 창을 닫고 원인을 알린다.
       보유를 잃었거나(403) 작업 상태가 바뀌었으면(409) 화면은 아직 편집 가능하게 그려져 있다.
       그래서 작업을 다시 받아 잠금을 서버 상태에 맞춘다. */
    onError: (e) => {
      setSubmitConfirmOpen(false);
      const t = isHoldLost(e.detail) ? HOLD_LOST_SAVE : errorText(e.detail, { message: "제출 실패", hint: "잠시 후 다시 시도해주세요." });
      toast("error", t.message, t.hint);
      if (e.status === 403 || e.status === 409) void item.refetch();
      /* 이 오류는 화면을 연 뒤 서버의 `caption_min_tokens`가 바뀌어 이 탭에 이전 기준값이 남은 경우다.
         토스트와 옆 배지(`제출 조건 충족`)의 안내가 달라지므로 새로고침 없이 기준값을 다시 받아 배지를 맞춘다. */
      if (e.detail === "caption_too_short") void project.refetch();
    },
    onSuccess: () => {
      setSubmitConfirmOpen(false);
      dirty.current = false;   // 제출로 임시저장이 반영됐다. 화면을 떠날 때 경고할 것이 없다
      // 제출하면 보유가 풀린다. `내 작업` 머리의 보유 수도 다시 받는다.
      invalidateHolding(qc, "work");
      void qc.invalidateQueries({ queryKey: pipelineKeys.summaryAll() });
      /* 제출 결과를 토스트로 알리고 돌아갈 화면으로 간다. 다음 작업은 그 화면의 버튼이 연다. */
      leaveWork("작업 1건 제출");
    },
  });

  /*
    신고 실패를 처리한다.
    보유를 잃었거나(403) 이미 처리된 작업이면(409) 다시 눌러도 같으므로 서랍을 닫고 작업을 다시 받아 잠금을 맞춘다.
    일시 오류(5xx·연결 실패)면 서랍을 열어 둔다. 적은 내용이 남아 있어 그대로 다시 보낼 수 있고, 실패 문장은 서랍 안에 뜬다.
  */
  const onReportError = useCallback(
    (e: ApiError, close: (open: boolean) => void) => {
      if (e.status !== 403 && e.status !== 409) return;
      close(false);
      const t = errorText(e.detail, { message: "신고 실패" });
      toast("warning", t.message, t.hint);
      void item.refetch();
    },
    [toast, item],
  );

  // 비식별화 신고. 신고하면 작업을 놓고 돌아갈 화면으로 간다.
  // 서버가 그 클립 상태를 바꿔 더는 작업하지 못하게 하고 보유도 푼다.
  // 서랍만 닫으면 제출만 409로 막히는 빈 화면이 남는다.
  /* 창을 여는 순간의 재생 위치를 잡는다. 적는 동안 영상이 흘러도 기록은 문제를 본 위치다. */
  const openReport = useCallback(() => {
    setReportAtMs(Math.round((playerRef.current?.currentTime ?? 0) * 1000));
    setReportOpen(true);
  }, []);
  const reportMut = useMutation<unknown, ApiError, string>({
    mutationFn: (reason: string) =>
      reportDeidentMiss(id, {
        timestamp_ms: reportAtMs ?? Math.round((playerRef.current?.currentTime ?? 0) * 1000),
        reason,
      }),
    onSuccess: () => {
      setReportOpen(false);
      invalidateHolding(qc, "work");
      leaveWork("비식별화 미흡 신고");
    },
    onError: (e) => onReportError(e, setReportOpen),
  });

  /* 서랍을 닫으면 지난 실패를 지운다. 남기면 다시 열 때 보내기 전부터 실패 문장이 보인다. */
  const resetReport = reportMut.reset;
  useEffect(() => { if (!reportOpen) resetReport(); }, [reportOpen, resetReport]);


  // ---- 검증 ----
  // 토큰은 최종 캡션으로 센다. 삭제 표시한 문장은 제출 조건에 세지 않는다.
  const briefText = joinSentences(caption.brief);
  const briefTokens = estimateTokens(briefText);
  // 제출 조건은 캡션 하나가 프로젝트 설정 `caption_min_tokens` 이상인지뿐이다. 설정이 없으면 50을 쓴다.
  const captionMinTokens = project.data?.caption_min_tokens ?? 50;
  const briefOk = briefTokens >= captionMinTokens;
  /* 제출을 막는 항목 목록. 머리 줄 표시와 `canSubmit`이 이 배열을 쓴다.
     항목마다 문구가 둘이다. 머리 줄은 상태(`short`)만 보여 주고 수치는 레일 위 배지 하나가 보여 준다. `detail`은 툴팁용이다.
     같은 제출 조건을 여러 곳에 수치로 표시하지 않는다. */
  const submitBlockers: { short: string; detail: string }[] = [];
  if (briefTokens < captionMinTokens) {
    submitBlockers.push({
      short: "캡션 길이 부족",
      detail: `캡션 길이 최소 ${captionMinTokens} (현재 ${briefTokens})`,
    });
  }
  const canSubmit = canEdit && submitBlockers.length === 0;

  /**
   * 제출을 요청한다. 바로 보내지 않고 확인 창을 연다. payload는 제출을 확정할 때 만든다.
   */
  const requestSubmit = useCallback(() => {
    if (!canSubmit || locked || submit.isPending) return;
    setSubmitConfirmOpen(true);
  }, [canSubmit, locked, submit.isPending]);
  const mutateSubmit = submit.mutate;
  const submitPending = submit.isPending;
  const confirmSubmit = useCallback(() => {
    if (!canSubmit || locked || submitPending) return;
    mutateSubmit();
  }, [canSubmit, locked, submitPending, mutateSubmit]);

  // ---- 단축키. 키 표는 `lib/shortcuts.ts`의 `LABEL_SHORTCUTS`다 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      // 포커스된 버튼의 Space/Enter는 브라우저 기본 동작에 맡긴다.
      // 전역 핸들러가 Space를 가로채면 키보드로 버튼을 누를 수 없다.
      // 판정은 공용 함수 `ownsActivationKey`가 한다.
      if ((e.key === " " || e.key === "Enter") && ownsActivationKey(e.target)) return;
      // `?`(도움말)는 공용 훅이 처리한다. 워크벤치들이 같은 키로 같은 표를 연다.
      // ⌘S는 창이 열려 있어도 받는다. 받지 않으면 브라우저의 '페이지 저장'이 뜬다.
      if (hit(LABEL_SHORTCUTS, "save", e)) {
        e.preventDefault();
        // 막는 조건(미보유)은 `saveDraft` 한 곳에서 본다
        saveDraft();
        return;
      }
      // 대화상자(확인 창·신고·단축키)가 열려 있으면 워크벤치 단축키는 동작하지 않는다.
      // 열린 창의 키가 우선이다. 제출 확인 창의 ⌘Enter는 창이 직접 받는다(`WorkbenchConfirm`).
      // 그러지 않으면 신고 서랍의 사유 입력란에서 누른 ⌘Enter가 작업 전체를 제출한다.
      if (document.querySelector('[role="dialog"]')) return;
      if (hit(LABEL_SHORTCUTS, "submit", e)) {
        e.preventDefault();
        // 바로 제출하지 않고 확인 창을 연다. 막는 조건은 `requestSubmit` 한 곳에 있다.
        requestSubmit();
        return;
      }
      if (typing) return;
      // 재생 키(Space·J/K/L·,/.·[/]·−/=·0)는 워크벤치들이 함께 쓰는 `useVideoShortcuts`가
      // `PLAYBACK_SHORTCUTS`를 보고 처리한다.
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [requestSubmit, saveDraft]);

  const toggleHelp = useCallback(() => setHelpOpen((o) => !o), []);
  useVideoShortcuts(playerRef, { onToggleHelp: toggleHelp });

  // AI 초안은 빈 캡션에만 채워 라벨러가 쓴 캡션을 덮거나 판단을 이끌지 않게 한다.
  const maiuSummary = clip.data?.scene_summary?.trim() ?? "";  // AI 원본. 문장 카드의 교정됨 표시 기준이다

  return {
    id,
    // queries
    item, labels, reviews, clip, clipId, project, manages, home,
    // refs
    playerRef,
    // state
    caption, setCaption,
    saveState, savedAt,
    reportOpen, setReportOpen, openReport, reportAtMs,
    helpOpen, setHelpOpen,
    // AI 초안. `seedSource`가 있으면 AI 초안으로 채웠다고 안내한다
    seedSource, maiuSummary, captionSourceFailed,
    // derived
    canEdit, locked, holdLost,
    // 제출 확인. 창의 열림 상태와 실행 함수를 뷰에 넘긴다
    submitConfirmOpen, setSubmitConfirmOpen, requestSubmit, confirmSubmit,
    briefTokens, captionMinTokens,
    // 파생 문자열. diff·빈 값 판정에 쓴다. 뷰가 문장 잇기를 다시 구현하면 규칙이 두 곳으로 나뉜다.
    briefText,
    briefOk, canSubmit, submitBlockers,
    // handlers / mutations
    saveDraft, edited,
    draftMut, submit, reportMut,
  };
}
