/**
 * 영상의 준비 단계와 막힌 이유를 화면 문구로 바꾼다. 영상 상세의 머리 칩과 클립 빈 상태가 함께 쓴다.
 *
 * 판정은 서버가 한다(`prep_stage`·`prep_blocked_stage`, `services/media/video._prep_stage_case`).
 * 이 모듈은 이름과 다음 할 일만 붙인다. 단계 이름은 준비 화면과 같은 사전(`features/ingest`의 `lib/vocab`)을 쓴다.
 * 빈 상태가 `scene_split` 단계만 보고 문구를 고르면 변환이 실패한 영상이 장면 분할 대기로 표시된다.
 */
import type { ChipTone } from "@/shared/ui/Chip";
import {
  blockedAtLabel, STAGE_LABEL, STAGE_TONE, TONE_CHIP_TONE, type PrepBlockedStage, type PrepStage,
} from "@/features/ingest";

/**
 * 머리에 둘 단계 칩을 고른다. 막혔으면 위치를 붙인다(`문제 · 분석본 변환`).
 *
 * 서버가 단계를 계산하지 않은 응답이면 임의의 단계를 만들지 않고 `null`을 돌려준다.
 */
export function stageChip(v: {
  prep_stage?: PrepStage | null;
  prep_blocked_stage?: PrepBlockedStage | null;
}): { label: string; tone: ChipTone } | null {
  if (!v.prep_stage) return null;
  if (v.prep_stage === "blocked") {
    return {
      label: v.prep_blocked_stage ? `문제 · ${blockedAtLabel(v.prep_blocked_stage)}` : STAGE_LABEL.blocked,
      tone: "bad",
    };
  }
  // 기다림(`분석 요청 대기`)은 중립색이다. 주의 색은 지금 볼 항목에만 쓴다(`TONE_CHIP_TONE`).
  return { label: STAGE_LABEL[v.prep_stage], tone: TONE_CHIP_TONE[STAGE_TONE[v.prep_stage]] };
}

/** 빈 상태가 가리키는 다음 할 일. `prep`은 이 화면의 준비 진행, `ingest`는 영상 등록 화면이다. */
export type NextStep = "prep" | "ingest" | null;

/**
 * `구간 추가` 버튼을 두는 방식.
 * `primary`는 사람이 직접 넣어야만 클립이 생길 때, `secondary`는 기다리면 생기지만 직접 넣을 수도 있을 때,
 * `hidden`은 넣어도 쓸 수 없을 때다(재생본이 없는 영상).
 */
export type AddMode = "primary" | "secondary" | "hidden";

/** 막힌 위치별 빈 상태. 문장은 결과와 원인 하나씩이다. */
const BLOCKED_EMPTY: Record<PrepBlockedStage, { text: string; detail?: string; next: NextStep; add: AddMode }> = {
  upload: { text: "업로드가 끝나지 않아 분석을 시작하지 못했습니다.", next: null, add: "hidden" },
  probe: { text: "원본 규격 검증을 통과하지 못해 분석을 시작하지 못했습니다.", next: "prep", add: "hidden" },
  transcode: { text: "분석본 변환이 실패해 분석을 시작하지 못했습니다.", next: "prep", add: "hidden" },
  deident: { text: "비식별화가 실패해 분석을 시작하지 못했습니다.", next: "prep", add: "hidden" },
  deident_unmasked: {
    text: "비식별화를 켜기 전에 처리된 영상이라 분석을 멈췄습니다.",
    next: "prep",
    add: "hidden",
  },
  publish: { text: "재생본 변환이 실패해 분석을 시작하지 못했습니다.", next: "prep", add: "hidden" },
  analysis: { text: "분석이 실패해 클립을 만들지 못했습니다.", next: "prep", add: "secondary" },
  // 다시 분석해도 결과가 같으므로 사람이 구간을 직접 넣어야 한다.
  no_clips: {
    text: "분석은 끝났지만 장면이 검출되지 않았습니다.",
    detail: "다시 분석해도 결과가 같습니다.",
    next: null,
    add: "primary",
  },
  // 클립이 있는 영상이라 빈 상태로 보이는 일은 드물다. 요약 재요청은 준비 진행의 요약 줄에 있다.
  summary: { text: "장면 요약을 받지 못했습니다.", next: "prep", add: "secondary" },
  ticket: { text: "분석 요청 항목이 없어 분석을 요청할 수 없습니다.", next: "ingest", add: "hidden" },
};

/**
 * 다음 할 일의 안내 문장을 고른다. 직접 조치할 수 없는 사람에게는 알릴 대상을 안내하고, 관리자에게는 문장을 쓰지 않는다.
 * 분석 요청·복구·재시도는 서버가 관리자(super_admin·project_admin)에게만 허용한다.
 */
function nextHint(next: NextStep, isAdmin: boolean): string | undefined {
  if (!next) return undefined;
  if (!isAdmin) return "프로젝트 관리자에게 알려 주세요.";
  // 관리자에게는 준비 진행이 빈 상태 바로 위에 펼쳐져 있고 영상 등록은 버튼으로 연결된다.
  return undefined;
}

/**
 * 준비 단계가 빈 상태를 정하는 경우의 문구를 고른다. 단계가 정하지 않으면 `null`이고,
 * 그때 빈 상태는 장면 분할 단계(`scene_split`)를 본다.
 *
 * Returns:
 *   `text`는 결과 한 줄, `detail`은 원인이나 할 일 한 줄, `next`는 버튼으로 이을 곳, `add`는 `구간 추가`를 어떻게 둘지다.
 */
export function stageEmpty(
  v: { prep_stage?: PrepStage | null; prep_blocked_stage?: PrepBlockedStage | null },
  isAdmin: boolean,
): { text: string; detail?: string; next: NextStep; add: AddMode } | null {
  if (v.prep_stage === "blocked" && v.prep_blocked_stage) {
    const b = BLOCKED_EMPTY[v.prep_blocked_stage];
    // 서버를 먼저 배포하면 표에 없는 위치 값이 올 수 있다. 그때는 단계가 빈 상태를 정하지 않는다.
    if (!b) return null;
    return { ...b, detail: b.detail ?? nextHint(b.next, isAdmin), next: isAdmin ? b.next : null };
  }
  if (v.prep_stage === "awaiting_analysis") {
    return {
      text: "분석 요청 전입니다.",
      detail: isAdmin ? "영상 등록에서 분석을 요청하면 장면을 나눕니다." : "관리자가 분석을 요청하면 장면을 나눕니다.",
      next: isAdmin ? "ingest" : null,
      add: "secondary",
    };
  }
  return null;
}
