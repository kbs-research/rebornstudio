/**
 * 사람 이름을 한 줄로 표기한다. 이름, 이메일, `이름 없음` 순서로 고른다.
 *
 * id 8자 조각으로는 사람도 DB도 대조할 수 없으므로 id 조각을 쓰지 않는다.
 * 이름도 이메일도 없으면 내부 값(해시 조각) 대신 `이름 없음`으로 표시한다.
 * 같은 사람이 화면마다 다른 규칙으로 표시되지 않게 모든 화면이 이 함수를 쓴다.
 */
export const NO_NAME = "이름 없음";

/**
 * 표시할 이름을 고른다. 이름이 있으면 이름, 없으면 이메일, 둘 다 없으면 `이름 없음`이다.
 * 빈 문자열은 값이 없는 것으로 본다.
 */
export function personLabel(name?: string | null, email?: string | null): string {
  return name?.trim() || email?.trim() || NO_NAME;
}
