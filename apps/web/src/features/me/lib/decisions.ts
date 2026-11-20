/**
 * `내 활동`의 내 판정 목록 표기. 서버가 준 판정 값에 이름과 색조만 붙인다.
 *
 * 판정의 좋고 나쁨은 도구가 판단하지 않는다. 그래서 수·비율 같은 지표를 이 파일에서 만들지 않는다.
 * 판정 이름은 `lib/vocab`의 사전을 쓴다. 화면마다 사전을 두면 같은 값이 다른 이름이 된다.
 */
import type { ChipTone } from "@/shared/ui/Chip";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { VERDICT_LABEL } from "@/lib/vocab";

import type { MyDecisionRow } from "../api";

/**
 * 판정 값의 색조. 승인은 정상, 수정해서 통과시킨 것은 진행 색, 반려는 위험색이다.
 * 사전에 없는 값은 무채색이다.
 */
const DECISION_TONE: Record<string, ChipTone> = {
  approve: "ok",
  modify_and_approve: "note",
  reject: "bad",
};

/** 판정 한 건의 이름과 색조를 돌려준다. 사전에 없는 값은 원문을 그대로 쓴다. 감추면 새 값이 화면에서 사라진다. */
export function decisionView(r: Pick<MyDecisionRow, "decision">): { label: string; tone: ChipTone } {
  return { label: VERDICT_LABEL[r.decision] ?? r.decision, tone: DECISION_TONE[r.decision] ?? "neutral" };
}

/** 판정 대상을 영상 제목과 클립 구간으로 쓴다. 클립이 없으면 영상 전체라고 쓴다. */
export function decisionTarget(
  r: Pick<MyDecisionRow, "video_title" | "clip_id" | "clip_start_seconds" | "clip_end_seconds">,
): string {
  const title = r.video_title?.trim() || "제목 없음";
  if (!r.clip_id) return `${title} · 영상 전체`;
  if (r.clip_start_seconds == null || r.clip_end_seconds == null) return title;
  return `${title} · ${fmtClipRange(r.clip_start_seconds, r.clip_end_seconds)}`;
}

/** 머리의 수 문구를 만든다. 다 받았으면 `전체 N건`, 아니면 `전체 N건 중 M건`이다. */
export function decisionCountLabel(loaded: number, total: number): string {
  const t = total.toLocaleString("ko-KR");
  return loaded >= total ? `전체 ${t}건` : `전체 ${t}건 중 ${loaded.toLocaleString("ko-KR")}건`;
}
