/**
 * 본인 라벨링 기록의 이름과 표기. `내 활동` 표와 기록 상세가 같이 쓴다.
 *
 * 상태는 서버가 정한다(`my_state`, `services/work/service._history_query`).
 * 화면은 이름과 톤만 붙이고 상태를 다시 판정하지 않는다.
 * 작업의 현재 상태를 그리면 다른 라벨러에게 넘어간 작업이 그 사람의 상태로 보인다.
 */
import type { components } from "@reborn/contracts";

import type { ChipTone } from "@/shared/ui/Chip";
import { fmtClipRange } from "@/shared/lib/fmtSecs";
import { VERDICT_LABEL } from "@/lib/vocab";

export type MyWorkHistoryRow = components["schemas"]["MyWorkHistoryRow"];
export type MyWorkState = MyWorkHistoryRow["my_state"];
export type MyWorkVerdict = NonNullable<MyWorkHistoryRow["my_verdict"]>;

/**
 * 상태 이름과 톤. 이름은 라벨러에게 보이는 문구다.
 * `submitted`는 `검수 대기`로 쓴다. 같은 화면의 지표가 제출 뒤 판정 전 상태를 그렇게 부른다.
 * `claimed`는 용어 사전의 `보유 중`이다.
 * 넘어간 작업과 대기열로 돌아간 작업은 판정 결과와 구분해 조용한 톤으로 둔다.
 */
export const MY_STATE: Record<MyWorkState, { label: string; tone: ChipTone }> = {
  claimed: { label: "보유 중", tone: "warn" },
  submitted: { label: "검수 대기", tone: "note" },
  approved: { label: "승인", tone: "ok" },
  rejected: { label: "반려", tone: "bad" },
  cancelled: { label: "종료", tone: "quiet" },
  handed_over: { label: "다른 작업자에게 넘어감", tone: "quiet" },
  returned: { label: "대기열로 돌아감", tone: "quiet" },
};

/**
 * 상태 거르기 선택지. 서버가 전체 기록에서 거르므로 불러온 행과 무관하게 전부 둔다.
 * `rejected`는 뺀다. 반려된 작업은 원 작업자에게 `보유 중`으로 돌아가고,
 * `rejected` 상태는 이전 데이터에만 있다.
 */
export const MY_STATE_FILTER: { value: MyWorkState; label: string }[] = (
  ["claimed", "submitted", "approved", "handed_over", "returned", "cancelled"] as const
).map((s) => ({ value: s, label: MY_STATE[s].label }));

/** 판정의 톤. 이름은 판정 이름의 기준(`lib/vocab` `VERDICT_LABEL`)에서 가져온다. */
export const VERDICT_TONE: Record<MyWorkVerdict, ChipTone> = {
  approve: "ok",
  modify_and_approve: "note",
  reject: "bad",
};

/** 판정 이름을 돌려준다. 사전에 없는 값은 원문을 그대로 돌려준다. 감추면 새 값이 화면에서 사라진다. */
export function verdictLabel(v: string): string {
  return VERDICT_LABEL[v] ?? v;
}

/** 행을 사람이 알아볼 수 있는 이름을 만든다. 영상 제목과 클립 구간을 쓰고, 둘 다 없으면 작업 ID 앞자리를 쓴다. */
export function historyTargetLabel(
  w: Pick<MyWorkHistoryRow, "video_title" | "clip_start_seconds" | "clip_end_seconds" | "work_item_id">,
): string {
  const parts = [
    w.video_title?.trim() || null,
    w.clip_start_seconds != null && w.clip_end_seconds != null
      ? fmtClipRange(w.clip_start_seconds, w.clip_end_seconds)
      : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : `작업 ${w.work_item_id.slice(0, 8)}`;
}

/**
 * 표 머리의 행 수 문구를 만든다. 뒤에 더 있으면 `전체`라고 쓰지 않는다.
 * 검색이나 상태 거르기가 걸려 있으면 그 조건 안의 수라서 `전체`를 붙이지 않는다.
 */
export function historyCountLabel(loaded: number, hasMore: boolean, narrowed: boolean): string {
  const n = loaded.toLocaleString("ko-KR");
  if (hasMore) return `불러온 ${n}건 · 더 있음`;
  return narrowed ? `${n}건` : `전체 ${n}건`;
}

/**
 * 기록 표의 검수 결과 열에 판정을 써야 하면 true. 상태 열에 이미 나온 내용은 되풀이하지 않는다.
 *
 * 판정이 없으면 상태 열의 `검수 대기`가 같은 사실을 보여 주므로 쓰지 않는다.
 * 상태가 승인이고 판정도 승인이면 쓰지 않는다. 수정 승인·반려와, 넘어가거나 돌아간 작업의 판정은 쓴다.
 */
export function verdictAddsInfo(w: Pick<MyWorkHistoryRow, "my_state" | "my_verdict">): boolean {
  if (!w.my_verdict) return false;
  return !(w.my_state === "approved" && w.my_verdict === "approve");
}
