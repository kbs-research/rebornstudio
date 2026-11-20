/**
 * 납품 관문이 낸 사유 한 줄을 항목 이름과 사유로 나눈다.
 *
 * 서버(`export_package`)는 사유를 `{항목 이름}: {사유}` 형식으로 낸다.
 * 예: `캡션: 10자 이상 필요`, `비식별화 신고: 처리 대기`, `타임코드: 원본 축 보정값 없음`.
 * 항목 이름은 서버 사전(`FIELD_LABEL`) 한 곳이 정한다.
 *
 * 번역 사전이 화면에 또 있으면 서버 사전과 달라지므로 이 함수는 이름을 그대로 나누기만 한다.
 * 어떻게 그릴지는 부르는 쪽이 정한다.
 *
 * 완료 목록과 완료 상세가 같은 문자열을 각자 파싱하면 결과가 달라질 수 있어서 규칙을 한 곳에 둔다.
 * 사유 문장 안의 콜론을 이름으로 오인하면 문장이 잘리므로, 이름 조각은 짧은 명사구(콜론 없음, 16자 이하)만 인정한다.
 *
 * @example splitBlocker("캡션: 10자 이상 필요") // { field: "캡션", reason: "10자 이상 필요" }
 * @example splitBlocker("화면 텍스트: 형식 오류") // { field: "화면 텍스트", reason: "형식 오류" }
 * @example splitBlocker("이름 없는 사유") // { field: null, reason: "이름 없는 사유" }
 */
export function splitBlocker(blocker: string): { field: string | null; reason: string } {
  const m = /^([^:\n]{1,16}): (.+)$/s.exec(blocker);
  return m ? { field: m[1], reason: m[2] } : { field: null, reason: blocker };
}
