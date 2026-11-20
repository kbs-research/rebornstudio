/**
 * 클립 한 건의 상태를 화면 문구로 바꾼다. 클립 보기 목록·콘텐츠 클립 표·영상 상세 클립 행이 함께 쓴다.
 *
 * 판정은 서버가 한다. 작업 상태는 `work_status`, 티켓이 없을 때는 `rights_status`다.
 * 이 모듈은 이름과 색조만 붙인다. 세 화면이 각자 표를 두면 같은 클립이 화면마다 다른 이름으로 표시된다.
 */
import type { ChipTone } from "@/shared/ui/Chip";
import type { ClipDTO } from "@/shared/api/client";

/** 상태 칩 하나. */
export type ClipStatusView = { text: string; tone: ChipTone };

/**
 * 라벨링 작업 상태의 이름. 클립 상태는 이 표에 없다.
 *
 * `null`(작업 티켓 없음)은 아직 작업 상태가 생기지 않았다는 뜻이라 이 표에 넣지 않는다.
 * `null`을 `대기`로 쓰면 신고로 멈췄거나 제외된 클립이 곧 라벨링될 클립으로 읽힌다.
 */
export const WORK_LABEL: Record<NonNullable<ClipDTO["work_status"]>, ClipStatusView> = {
  available: { text: "라벨링 대기", tone: "quiet" },
  claimed: { text: "라벨링 중", tone: "note" },
  submitted: { text: "검수 대기", tone: "note" },
  approved: { text: "승인", tone: "ok" },
  // 반려된 작업은 재작업을 기다린다.
  rejected: { text: "재작업 대기", tone: "bad" },
  cancelled: { text: "종료", tone: "quiet" },
};

/**
 * 작업 티켓이 없는 클립의 상태 문구. 클립 상태로 표시한다.
 * 사유는 싣지 않고 상태만 쓴다. 비식별화 신고 사유는 라벨링 단계의 신고 카드가 보여 준다.
 */
const RIGHTS_WAIT_LABEL: Record<string, ClipStatusView> = {
  legal_review: { text: "비식별화 신고", tone: "note" },
  rejected: { text: "제외", tone: "bad" },
};

/**
 * 클립 행의 상태 칩을 고른다.
 *
 * 작업 상태가 있으면 작업 상태를, 없으면 클립 상태를 쓴다.
 * 둘 다 해당하지 않으면(정상 클립에 티켓이 생기기 전의 짧은 시간) 임의의 상태를 만들지 않고 `null`을 돌려준다.
 */
export function clipStatusView(c: Pick<ClipDTO, "work_status" | "rights_status">): ClipStatusView | null {
  if (c.work_status) return WORK_LABEL[c.work_status];
  return RIGHTS_WAIT_LABEL[c.rights_status] ?? null;
}
