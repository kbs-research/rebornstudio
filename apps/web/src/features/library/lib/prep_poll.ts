/**
 * 영상 상세의 다시 조회 간격.
 *
 * 준비가 진행 중이면 다시 조회하고 모두 끝나면 멈춘다. 끝난 영상은 사람이 조작하기 전까지 값이 바뀌지 않는다.
 * 업로드·규격 검증은 초 단위로 끝나므로 짧은 간격으로 조회한다. 10초 간격이면 2초 만에 끝난 검증이
 * 최대 10초(머리 칩·클립은 15초) 늦게 반영되어 멈춘 것처럼 보인다.
 * 그 뒤 단계(변환·비식별화·분석)는 분 단위라 긴 간격으로 조회한다.
 */
import type { PrepTimelineEntry } from "@/features/ingest";

/** 초 단위로 끝나는 단계. 진행 중이거나 대기 중이면 짧은 간격으로 조회한다. */
const FAST_STAGES = new Set<PrepTimelineEntry["stage"]>(["upload", "probe"]);

/** 준비 타임라인과 나머지 상세 조회(영상·클립)의 다시 조회 간격(ms). 멈추면 `false`. */
export interface PrepPoll {
  timeline: number | false;
  detail: number | false;
}

/**
 * 타임라인 줄로 다시 조회 간격을 정한다.
 *
 * 진행 중(`running`)과 대기(`pending`)를 함께 본다. 대기도 곧 진행될 단계다.
 * 상세 조회는 타임라인보다 느리게 둔다. 단계 전이는 타임라인이 먼저 보여 주고 나머지는 뒤따라 갱신된다.
 *
 * @param entries 준비 타임라인 줄. 아직 받지 않았으면 빈 배열
 */
export function prepPoll(entries: Pick<PrepTimelineEntry, "stage" | "status">[]): PrepPoll {
  const live = entries.filter((e) => e.status === "running" || e.status === "pending");
  if (live.length === 0) return { timeline: false, detail: false };
  if (live.some((e) => FAST_STAGES.has(e.stage))) return { timeline: 3_000, detail: 5_000 };
  return { timeline: 10_000, detail: 15_000 };
}

/**
 * 단계별 상태를 한 문자열로 만든다. 값이 바뀌면 단계가 넘어갔다는 뜻이다.
 *
 * 상세 조회는 타임라인보다 간격이 길다. 단계가 넘어간 순간 상세를 한 번 바로 다시 조회해야
 * 머리 칩(`검증 대기` 같은 단계 이름)과 타임라인이 같은 단계를 표시한다.
 * 간격이 바뀌면 타이머도 새로 시작하므로, 바로 조회하지 않으면 새 간격만큼 더 늦어진다.
 */
export function stageSignature(entries: Pick<PrepTimelineEntry, "stage" | "status">[]): string {
  return entries.map((e) => `${e.stage}:${e.status}`).join("|");
}
