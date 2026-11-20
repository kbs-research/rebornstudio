/**
 * 한국어 조사를 앞말의 받침에 맞춰 고른다.
 *
 * 이름이 들어가는 문장마다 두 형태를 괄호로 나열하면(`김라벨을(를) 제거합니다`) 제품이 덜 다듬어져 보인다.
 * 받침은 코드로 판정할 수 있으므로 받침에 맞는 조사 하나를 고른다.
 *
 * 한글이 아닌 끝글자(영문·숫자)는 읽는 소리로 받침을 정한다.
 * `Admin`은 `민`으로 읽혀 받침이 있고, `2`는 `이`라서 받침이 없다.
 * 판정할 수 없으면(빈 문자열·기호로 끝남) `을`·`이`·`은`이 더 자주 맞으므로 받침이 있는 쪽으로 둔다.
 */

/**
 * 받침이 남는 영문 끝소리(l·m·n, 그리고 `ng`).
 *
 * 나머지 자음은 한국어로 읽을 때 `으`가 붙어 받침이 사라진다.
 * `Project`는 `프로젝트`, `server`는 `서버`, `box`는 `박스`다.
 * 그래서 `Default Project`는 `…를`이고 `Super Admin`은 `…을`이다.
 */
const LATIN_WITH_FINAL = new Set(["l", "m", "n"]);
/** 한국어로 읽을 때 끝소리에 받침이 있는 숫자. 0(영)·1(일)·3(삼)·6(육)·7(칠)·8(팔). */
const DIGIT_WITH_FINAL = new Set(["0", "1", "3", "6", "7", "8"]);

/**
 * 마지막 글자에 받침이 있으면 true를 돌려준다. 판정할 수 없으면 true(받침 있음)로 둔다.
 *
 * 끝의 따옴표·괄호·문장부호는 빼고 판정한다.
 * 화면에서 이름을 `"…"`로 감싸는 곳이 있다(프로젝트 삭제 확인).
 * 닫는 따옴표를 보고 판정하면 `"Default Project"을`이 된다.
 */
export function hasFinalConsonant(word: string | null | undefined): boolean {
  const s = (word ?? "").trim().replace(/[\s"'`’”»)\]}.,!?…·:;-]+$/u, "");
  if (!s) return true;
  const ch = s[s.length - 1];
  const code = ch.charCodeAt(0);
  // 한글 음절 영역. (코드 - 0xAC00) % 28이 0이면 받침이 없다.
  if (code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28 !== 0;
  if (/[0-9]/.test(ch)) return DIGIT_WITH_FINAL.has(ch);
  if (/[a-zA-Z]/.test(ch)) {
    // `-ng`는 ㅇ 받침으로 읽힌다(`marketing`은 마케팅). 그 밖의 g는 `그`로 읽혀 받침이 없다.
    if (ch.toLowerCase() === "g") return s.slice(-2).toLowerCase() === "ng";
    return LATIN_WITH_FINAL.has(ch.toLowerCase());
  }
  return true;
}

/**
 * 이름 뒤에 붙일 조사를 골라 `이름+조사`로 잇는다.
 *
 * @example withJosa("김라벨", "을", "를") // "김라벨을"
 * @example withJosa("정신입", "이", "가") // "정신입이"
 * @example withJosa("최저작", "은", "는") // "최저작은"
 */
export function withJosa(word: string | null | undefined, withFinal: string, withoutFinal: string): string {
  const s = (word ?? "").trim();
  return s + (hasFinalConsonant(s) ? withFinal : withoutFinal);
}

/** 목적격 조사(`…을` / `…를`)를 붙인다. 화면에서 가장 자주 쓰는 조사다. */
export function eulReul(word: string | null | undefined): string {
  return withJosa(word, "을", "를");
}
