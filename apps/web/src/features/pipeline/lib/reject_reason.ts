/**
 * 검수 반려에 사유가 있는지 정한다.
 *
 * 지적 문장은 틀린 위치만 가리킨다. 사유 칩이나 보충 설명이 없으면 라벨러는 재작업 화면에서 고칠 내용을 알 수 없다.
 * 그런 반려도 반려율과 반려 횟수에는 그대로 들어간다.
 * 서버 `reject_reasons.validate_decision`이 같은 조건으로 422를 돌려준다.
 * 이 판정만 따로 테스트하려고 컴포넌트 밖에 둔다.
 */

/** 반려 사유 코드(`codes`)나 보충 설명(`note`)이 있으면 true. 공백만 있는 보충 설명은 없는 것으로 본다. */
export function rejectHasReason(codes: readonly string[], note: string): boolean {
  return codes.length > 0 || note.trim() !== "";
}
