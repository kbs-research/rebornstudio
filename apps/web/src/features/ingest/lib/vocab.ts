/**
 * 준비 과정 화면의 문구, 색, 표시 형식.
 *
 * 단계와 분류의 판정은 서버 SQL CASE(`_prep_stage_case`, `_ingest_bucket_case`)가 한다.
 * 이 파일은 서버 값에 붙일 문구와 색만 정한다. 서버 값(`blocked`)은 그대로 두고 문구만 이 파일에서 바꾼다.
 * 화면마다 문구를 따로 쓰면 같은 상태가 화면마다 다른 이름으로 뜬다.
 *
 * 용어:
 * - Job: 한 번의 등록 단위. 시작·진행·끝·실패 상태가 있다.
 * - 이슈: 사용자가 처리할 항목. "막힘"은 시스템이 멈춘 것으로 읽혀 쓰지 않는다.
 * - 상세: 목록에 대응하는 상세 보기.
 *
 * 단계 이름에는 벤더 이름을 쓰지 않는다. 분석 스택은 프로젝트마다 다르고,
 * `ncp-shots`를 쓰는 프로젝트에는 MI가 없어 `MI 등록` 같은 이름은 틀린 표시가 된다.
 * 서버 단계 이름(`register`, `analyze`, `summarize`, `scene_split`)도 중립적이다.
 * MI를 직접 다루는 곳(비용, 호출 한도, 연동 진단)에서만 벤더 이름을 쓴다.
 */
import type { components } from "@reborn/contracts";

import { fmtSpan } from "@/shared/lib/fmtSecs";
import type { ChipTone } from "@/shared/ui/Chip";

type VideoDTO = components["schemas"]["VideoDTO"];

export type PrepStage = NonNullable<VideoDTO["prep_stage"]>;
export type PrepBlockedStage = NonNullable<VideoDTO["prep_blocked_stage"]>;
export type PrepStageCounts = components["schemas"]["PrepStageCounts"];
export type PrepTimelineEntry = components["schemas"]["PrepTimelineEntry"];
export type RunStage = PrepTimelineEntry["stage"];
export type RunStatus = PrepTimelineEntry["status"];

/**
 * 상태 톤. 색은 토큰 변수만 쓴다.
 * `act`는 사람이 실행해야 넘어가는 상태다. 지금 봐야 할 것에만 쓰는 호박색을 쓴다.
 */
export type Tone = "ok" | "run" | "wait" | "bad" | "mute" | "act";

/** 톤별 글자 색 클래스. */
export const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ok-text",
  run: "text-accent",
  wait: "text-label-3",
  bad: "text-bad-text",
  mute: "text-label-4",
  act: "text-amber-text",
};

/** 톤별 면 색 클래스. 막대와 세그먼트 조각에 쓴다. */
export const TONE_FILL: Record<Tone, string> = {
  ok: "bg-ok-text",
  run: "bg-accent",
  wait: "bg-separator-strong",
  bad: "bg-bad-text",
  mute: "bg-fill",
  act: "bg-amber",
};

/** 배지 안의 점 색. 라벨보다 먼저 읽히는 신호다(감사 로그 심각도 점과 같은 방식). */
export const TONE_DOT: Record<Tone, string> = {
  ok: "bg-ok",
  run: "bg-accent",
  wait: "bg-separator-strong",
  bad: "bg-bad",
  mute: "bg-label-4/50",
  act: "bg-amber",
};

/**
 * 톤별 칩 색조. 상태 칩(`Chip variant="dot"`)이 쓴다. 기다림은 중립, 진행은 파랑이다.
 * 영상 단계 칩(`features/library`의 `video_stage`)도 이 표를 쓴다.
 */
export const TONE_CHIP_TONE: Record<Tone, ChipTone> = {
  ok: "ok",
  run: "note",
  wait: "neutral",
  bad: "bad",
  mute: "quiet",
  act: "amber",
};

/** 준비 단계 문구. 키는 서버 `prep_stage` 값을 그대로 쓴다. */
export const STAGE_LABEL: Record<PrepStage, string> = {
  uploading: "업로드 중",
  probing: "검증 대기",
  transcoding: "변환 중",
  awaiting_analysis: "분석 요청 대기",
  registering: "분석 등록 중",
  analyzing: "분석 중",
  summarizing: "요약 중",
  ready_for_work: "준비 완료",
  blocked: "문제",
};

/** 준비 단계별 톤. */
export const STAGE_TONE: Record<PrepStage, Tone> = {
  uploading: "run",
  probing: "run",
  transcoding: "run",
  awaiting_analysis: "wait",
  registering: "run",
  analyzing: "run",
  summarizing: "run",
  ready_for_work: "ok",
  blocked: "bad",
};

/**
 * 이슈가 난 위치 문구. "변환에서 발생" 같은 문구에 쓴다(서버 `prep_blocked_stage`).
 * 표에 없는 값은 `blockedAtLabel`의 대체 문구로 그린다.
 */
export const BLOCKED_AT: Record<PrepBlockedStage, string> = {
  upload: "업로드",
  probe: "검증",
  transcode: "분석본 변환",
  deident: "비식별화",
  // `deident`는 시도했다가 실패한 경우, 이 값은 시도한 적이 없는 경우다.
  // 같은 라벨을 쓰면 다시 시도하라는 뜻으로 읽혀 라벨을 나눈다. 기능 이름은 `비식별화` 하나로 쓴다.
  deident_unmasked: "비식별화 전",
  publish: "재생본 변환",
  analysis: "분석",
  // 분석은 끝났지만 클립이 0개인 경우. 다시 실행해도 결과가 같아서 `analysis`(실패)와 값을 나눈다.
  no_clips: "장면 없음",
  // 분석은 끝나 클립은 있지만 장면 요약을 받지 못한 경우다. 요약만 다시 요청하므로 `analysis`와 나눈다.
  summary: "장면 요약",
  // 분석 요청 직전 단계에서 멈춘 경우. 내부 용어(`티켓`, `요청 항목`)는 화면에 정의가 없어 쓰지 않는다.
  ticket: "분석 요청",
};

/**
 * 고른 영상을 분석 요청하지 않은 사유 문구(서버 `MaiuDispatchResult.held_reasons`의 키).
 * 판정은 서버 준비 판정(`_analysis_hold_case`)이 하고 여기서는 이름만 붙인다.
 */
const HOLD_REASON_LABEL: Record<string, string> = {
  not_probed: "규격 검증 전",
  probe_failed: "규격 검증 실패",
  prep_failed: "준비 실패",
  preparing: "준비 중",
  unmasked: "비식별화 전",
  no_duration: "길이 모름",
  not_waiting: "요청 대기 아님",
};

/** 사유별 수를 `규격 검증 전 1개 · 준비 중 2개`처럼 한 줄로 만든다. 모르는 코드는 `기타`로 센다. */
export function holdReasonText(reasons: Record<string, number> | undefined): string {
  return Object.entries(reasons ?? {})
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${HOLD_REASON_LABEL[k] ?? "기타"} ${n.toLocaleString("ko-KR")}개`)
    .join(" · ");
}

/**
 * 위치 라벨을 돌려준다. 모르는 값이 와도 `undefined`를 그리지 않는다.
 *
 * 서버와 화면은 따로 배포된다. 서버가 값을 먼저 늘리면 그동안 화면의 표에는 그 값이 없고,
 * 런타임 값이라 타입 검사로도 잡을 수 없다. 대체 문구가 없으면 `이슈 undefined 1건`이 표시된다.
 */
export function blockedAtLabel(stage: string): string {
  return BLOCKED_AT[stage as PrepBlockedStage] ?? "기타";
}

/** 위치별 한 줄 설명을 돌려준다. 같은 이유로 대체 문구를 둔다(`HINT`는 `JobIssueBanner`에 있다). */
export function blockedHintOr(hint: Partial<Record<string, string>>, stage: string): string {
  return hint[stage] ?? "알 수 없는 단계입니다. 각 행의 사유를 확인하세요.";
}

/**
 * 타임라인 단계 문구. 서버 `stage_runs.STAGE_ORDER`와 같은 어휘를 쓴다.
 *
 * 변환이 두 번이라는 사실을 이름으로 드러낸다. 앞의 변환은 720p 분석본만 만든다.
 * 뒤의 재생본 변환도 인코딩 비용이 들어서 산출물 이름만 쓰면 그 비용이 보이지 않는다.
 * 순서는 `분석본`, `비식별화`, `재생본`이다. 재생본을 비식별화된 분석본에서 만들어야 마스킹이 적용된다.
 */
export const RUN_STAGE_LABEL: Record<RunStage, string> = {
  upload: "업로드",
  probe: "규격 검증",
  transcode: "분석본 변환",
  deident: "비식별화",
  publish: "재생본 변환",
  register: "분석 등록",
  // MI 스택에서는 MI가 분석 중에 클립을 나누므로 이 단계가 따로 보이지 않는다.
  // 다른 스택에서는 이 단계를 따로 보여 클립을 나누는 주체를 드러낸다. 낱말은 `장면 요약`과 같은 `장면`을 쓴다.
  scene_split: "장면 분할",
  analyze: "분석",
  summarize: "장면 요약",
};

/** 타임라인 실행 상태 문구. */
export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  ok: "완료",
  running: "진행 중",
  failed: "실패",
  skipped: "건너뜀",
  pending: "대기",
  // 단계 이름과 이어 읽힌다(`비식별화 필요`).
  needed: "필요",
};

/** 타임라인 실행 상태별 톤. */
export const RUN_STATUS_TONE: Record<RunStatus, Tone> = {
  ok: "ok",
  running: "run",
  failed: "bad",
  skipped: "mute",
  pending: "wait",
  needed: "act",
};

/**
 * 준비 단계 그룹. 단계 칩과 세그먼트가 쓰는 표시용 묶음 단위다. 판정에는 쓰지 않는다.
 * `awaiting_analysis`(사람 또는 예약 대기)와 `ready_for_work`(종료)는 그룹에 넣지 않는다.
 * 대기는 요청 대기 칩이, 완료는 세그먼트가 보여 준다.
 */
export const PREP_GROUPS = [
  { key: "register", label: "등록", stages: ["uploading", "probing"] },
  { key: "transcode", label: "변환", stages: ["transcoding"] },
  // 키는 `mi`로 둔다. `?prep=mi` 링크와 북마크가 그 값을 쓰므로 라벨만 바꾸고 키는 그대로 둔다.
  { key: "mi", label: "분석 등록", stages: ["registering"] },
  { key: "analyze", label: "분석", stages: ["analyzing", "summarizing"] },
] as const satisfies readonly { key: string; label: string; stages: readonly PrepStage[] }[];

/**
 * 영상이 들어온 방식의 라벨. 키는 서버 `source` 값(`upload`, `ncp`)을 그대로 쓴다.
 *
 * 라벨은 등록 방식(파일 전송인지, 객체 스토리지 키 연결인지)을 쓴다.
 * 스토리지는 프리셋이 정하므로(`storage_key`) 벤더 이름을 고정하면 다른 스택에서 틀린 표시가 된다.
 * 값은 서버 값이고 필터 쿼리도 그 값을 쓰므로 그대로 둔다. 화면마다 라벨을 따로 만들지 않고 이 표를 쓴다.
 */
export const SOURCE_LABEL: Record<string, string> = { upload: "파일 업로드", ncp: "저장소" };

/**
 * 등록 묶음 이름에 붙는 방식 라벨. 예: `9월 2일 10:28 파일 업로드`.
 * 목록·출처 필터·등록 창 탭(`SOURCE_LABEL`)과 같은 말을 써서 같은 출처를 한 이름으로 부른다.
 */
export const SOURCE_REGISTER_LABEL: Record<string, string> = {
  upload: "파일 업로드",
  ncp: "저장소 등록",
};

/** 그룹 키별 라벨. 칩, 세그먼트, 레일이 라벨을 각자 정의하지 않고 이 표를 쓴다(추가 키는 `PrepChipKey`). */
export const GROUP_LABEL: Record<string, string> = {
  ...Object.fromEntries(PREP_GROUPS.map((g) => [g.key, g.label])),
  awaiting: "요청 대기",
  done: "완료",
  blocked: "문제",
};

/** 준비 단계 그룹 키. */
export type PrepGroupKey = (typeof PREP_GROUPS)[number]["key"];
/** Job 상세 칩의 확장 키. 그룹 키에 대기, 완료, 이슈를 더한다. */
export type PrepChipKey = PrepGroupKey | "awaiting" | "done" | "blocked";

/** 서버 `prep_stage` 값 전체. 단계 필터가 넣은 값이 이 집합에 있는지 확인할 때 쓴다. */
const ALL_STAGES: readonly PrepStage[] = [
  "uploading", "probing", "transcoding", "awaiting_analysis",
  "registering", "analyzing", "summarizing", "ready_for_work", "blocked",
];

/**
 * 필터 값을 서버 단계 목록으로 바꾼다.
 *
 * `?prep=`에는 칩이 넣은 그룹 키(`done`, `analyze`)와 단계 필터가 넣은 서버 값(`ready_for_work`, `analyzing`)이 온다.
 *
 * 표에 없는 값은 빈 목록으로 돌려줘 다른 단계의 데이터가 섞이지 않게 한다.
 * 대체값을 `["blocked"]`로 두면 "단계: 준비 완료"라고 쓰면서 이슈 행을 보여 준다.
 */
export function stagesOf(key: PrepChipKey | PrepStage): readonly PrepStage[] {
  const g = PREP_GROUPS.find((x) => x.key === key);
  if (g) return g.stages;
  if (key === "awaiting") return ["awaiting_analysis"];
  if (key === "done") return ["ready_for_work"];
  if (ALL_STAGES.includes(key as PrepStage)) return [key as PrepStage];
  return [];
}

/** 칩·그룹 키나 서버 단계 값에 해당하는 영상 수를 더한다. */
export function groupCount(c: PrepStageCounts, key: PrepChipKey | PrepStage): number {
  return stagesOf(key).reduce((s, st) => s + (c[st] ?? 0), 0);
}

/**
 * 소요 시간 표기 함수. `shared/lib/fmtSecs.fmtSpan`의 별칭이다.
 * 같은 등록 흐름에서 같은 길이를 두 형식(`4m 14s`와 `1시간 31분`)으로 보여 주지 않게 한다.
 */
export const fmtElapsed = fmtSpan;
