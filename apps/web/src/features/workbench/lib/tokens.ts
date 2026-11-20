/**
 * 캡션 토큰 수를 추정한다. 브라우저에 tiktoken이 없어서 휴리스틱으로 추정한다.
 *
 * 한국어는 대략 1.5~2자/토큰이라 CJK는 글자 수, ASCII는 단어 수로 더해 추정한다.
 *
 * 서버도 같은 방식으로 세어 제출 조건을 검사한다(`services/caption.py`).
 * 두 값이 다르면 화면은 통과인데 서버가 거절해 라벨러가 고칠 곳을 찾지 못한다. 그래서 정확도보다 양쪽 일치가 중요하다.
 * 런타임이 달라 함수를 합칠 수 없으므로 판정 표(`packages/reborn-contracts/caption-gate.json`)를 양쪽 테스트가 읽는다
 * (`tokens.test.ts`·`tests/contract/test_caption_gate.py`).
 * 세는 방법을 바꾸려면 양쪽을 같은 커밋에서 바꾸고 표를 다시 계산한다.
 */
export function estimateTokens(text: string): number {
  const t = text.trim();
  if (!t) return 0;
  const cjk = (t.match(/[　-鿿가-힯]/g) ?? []).length;
  const ascii = t.replace(/[　-鿿가-힯]/g, " ").trim();
  const asciiWords = ascii ? ascii.split(/\s+/).filter(Boolean).length : 0;
  return Math.ceil(cjk / 1.5) + asciiWords;
}
