/**
 * 판정을 보내는 사이 작업 상태가 바뀌었을 때 바뀐 내용을 문구로 만든다. 검수 워크벤치가 판정 실패에 쓴다.
 *
 * 서버는 제출 상태가 아닌 작업의 판정을 `not_submitted_{상태}`로 막는다. 오류 사전 문구에는 상태가 없어
 * 다른 탭에서 이미 승인한 사람도 "제출 상태가 아닙니다"만 본다. 그래서 코드에 붙은 상태로 바뀐 결과를 쓴다.
 * 이 판정만 따로 테스트하려고 컴포넌트 밖에 둔다.
 */
import type { ErrorText } from "@/shared/api/error_text";

/** 상태별 결과 문장. 화면에 쓰는 상태 이름과 같은 말이다. */
const STALE: Record<string, string> = {
  approved: "이미 승인된 작업입니다",
  rejected: "이미 반려된 작업입니다",
  cancelled: "종료된 작업입니다",
  claimed: "라벨링 중인 작업입니다",
  available: "라벨링 대기열로 돌아간 작업입니다",
};

/**
 * 판정 실패 코드가 상태 변경이면 바뀐 결과를 `{ message, hint }`로 돌려준다. 아니면 `null`이다.
 * 화면은 이 문구를 띄운 뒤 작업을 다시 받아 새 상태로 그린다.
 */
export function staleDecisionText(detail: unknown): ErrorText | null {
  if (typeof detail !== "string" || !detail.startsWith("not_submitted_")) return null;
  const status = detail.slice("not_submitted_".length);
  return {
    message: STALE[status] ?? "판정할 수 없는 상태입니다",
    hint: "다른 화면에서 먼저 처리됐습니다. 화면을 지금 상태로 바꿨습니다.",
  };
}
