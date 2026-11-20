/**
 * 끝난 준비 진행 요약의 표시 규칙. 단계 칩의 표시와 칩 아래 안내 줄의 순서를 정한다.
 *
 * 판정은 서버가 한다. 상태(`status`), 확인 필요(`review_needed`)와 두 사유 문장은 서버 값을 그대로 쓰고,
 * 이 파일은 칩 모양과 안내 줄의 순서만 고른다.
 */
import type { PrepTimelineEntry } from "@/features/ingest";

/** 요약 칩 하나의 표시. 확인 필요, 건너뜀, 완료 순으로 고른다. */
export type SummaryMark = "review" | "skipped" | "ok";

/** 칩 아래 안내 한 줄. 단계와 서버가 준 사유 문장을 담는다. */
export interface SummaryNote {
  stage: PrepTimelineEntry["stage"];
  /** 경고(`warn`)는 확인 필요, 보조(`mute`)는 건너뜀 사유다. */
  tone: "warn" | "mute";
  text: string;
}

/** 서버가 확인 필요 사유를 비워 보낸 경우의 문장. */
export const REVIEW_FALLBACK = "비식별화 결과를 확인해야 합니다.";

type Entry = Pick<PrepTimelineEntry, "stage" | "status">
  & Partial<Pick<PrepTimelineEntry, "skipped_reason" | "review_needed" | "review_note">>;

/**
 * 단계 칩의 표시를 고른다.
 *
 * 확인 필요를 건너뜀보다 먼저 본다. 확인 필요는 얼굴이 가려지지 않은 채 나갈 수 있다는 경고라 다른 표시보다 앞선다.
 */
export function summaryMark(e: Entry): SummaryMark {
  if (e.review_needed) return "review";
  if (e.status === "skipped") return "skipped";
  return "ok";
}

/**
 * 칩 아래 안내 줄을 만든다. 확인 필요를 모두 먼저 두고 건너뜀 사유를 뒤에 둔다.
 *
 * 같은 묶음 안에서는 서버가 준 단계 순서를 지킨다. 사유가 없는 건너뜀은 칩만 두고 줄을 만들지 않는다.
 * 줄을 만들면 단계 이름만 있고 설명이 없는 줄이 된다.
 */
export function summaryNotes(entries: Entry[]): SummaryNote[] {
  const warn: SummaryNote[] = [];
  const mute: SummaryNote[] = [];
  for (const e of entries) {
    const mark = summaryMark(e);
    if (mark === "review") {
      warn.push({ stage: e.stage, tone: "warn", text: e.review_note || REVIEW_FALLBACK });
    } else if (mark === "skipped" && e.skipped_reason) {
      mute.push({ stage: e.stage, tone: "mute", text: e.skipped_reason });
    }
  }
  return [...warn, ...mute];
}
