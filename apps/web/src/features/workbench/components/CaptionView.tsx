/**
 * 캡션·장면 요약처럼 긴 한국어 텍스트를 읽기 쉽게 그리는 공용 부품.
 *
 * 검수(제출 캡션)와 라벨링 읽기 뷰가 함께 쓴다. 문장 단위로 나눠 빽빽한 한 문단을 훑어보기 쉬운 줄로 바꾼다.
 */

/** 문장 경계. 서버 `services/caption.py`의 `_SENTENCE_BREAK`와 같다. */
const SENTENCE_BREAK = /(?<=[.?!。])(?<!\.\.)\s+/g;

/** 짝을 이루는 따옴표(키는 여는 따옴표, 값은 닫는 따옴표). 서버 `_QUOTE_PAIRS`와 같다. */
const QUOTE_PAIRS: Record<string, string> = {
  '"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "「": "」", "『": "』",
};

/** 아포스트로피 앞에 오는 영문자·숫자. 이 글자 뒤의 `'`(`It's`)는 아포스트로피로 보고 여는 따옴표로 세지 않는다. */
const APOSTROPHE_BEFORE = /[A-Za-z0-9]/;

/**
 * 닫힌 따옴표 묶음의 [여는 위치, 닫는 위치] 목록을 돌려준다.
 * 닫히지 않은 따옴표는 묶음으로 보지 않는다. 묶으면 따옴표 하나 때문에 그 뒤 전체가 한 문장이 된다.
 */
function quotedSpans(text: string): [number, number][] {
  const spans: [number, number][] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    const close = QUOTE_PAIRS[ch];
    if (close && !(ch === "'" && i > 0 && APOSTROPHE_BEFORE.test(text[i - 1]))) {
      const j = text.indexOf(close, i + 1);
      if (j !== -1) {
        spans.push([i, j]);
        i = j + 1;
        continue;
      }
    }
    i += 1;
  }
  return spans;
}

/**
 * 문장 끝 부호(. ? ! 。) 뒤의 공백에서 텍스트를 나눈다.
 * 말줄임표(... … ⋯) 뒤에서는 나누지 않는다. 보도 캡션은 말줄임표를 문장 안에서 쓴다.
 * 닫힌 따옴표 안에서도 나누지 않는다. 인용 발언이 여러 문장으로 나뉘면 문장 단위 구간·검수가 깨진다.
 * 나눌 곳이 없으면 전체를 한 문장으로 돌려준다.
 * 서버 `services/caption.py`의 `split_sentences`와 같은 규칙이고, `caption-shapes.json` 표로 맞춘다.
 */
export function splitSentences(text: string): string[] {
  const t = text.trim();
  if (!t) return [];
  const spans = quotedSpans(t);
  const parts: string[] = [];
  let last = 0;
  for (const m of t.matchAll(SENTENCE_BREAK)) {
    const at = m.index ?? 0;
    if (spans.some(([a, b]) => a < at && at < b)) continue;
    parts.push(t.slice(last, at).trim());
    last = at + m[0].length;
  }
  parts.push(t.slice(last).trim());
  const kept = parts.filter(Boolean);
  return kept.length ? kept : [t];
}

/**
 * 캡션 본문을 문장 단위로 나눠 번호가 붙은 카드로 읽기 전용으로 그린다.
 * 검수 워크벤치의 문장별 판정(확인, 수정, 반려)은 SentenceTriage가 맡는다.
 * `sentences`를 주면 나누지 않고 그 목록을 그린다. 저장된 문장 배열을 이었다가 다시 나누면
 * 종결 부호가 없는 문장끼리 합쳐지므로 배열을 그대로 넘긴다.
 */
export function CaptionBody({
  text,
  sentences: given,
  fontSize,
}: {
  text?: string;
  sentences?: string[];
  fontSize: number;
}) {
  const sentences = given ?? splitSentences(text ?? "");
  // `— 없음`처럼 대시를 붙이지 않는다. 값 위치의 `—`와 겹쳐 기호가 둘이 된다.
  if (!sentences.length) return <p className="text-caption text-label-3">없음</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <div key={i} className="flex items-start gap-2 rounded-card border border-separator bg-panel px-2.5 py-2 shadow-xs">
          <span className="mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-capsule bg-fill-2 text-micro font-medium text-label-3">
            {i + 1}
          </span>
          <span className="flex-1 text-label" style={{ fontSize: `${fontSize}px`, lineHeight: 1.55 }}>
            {s}
          </span>
        </div>
      ))}
    </div>
  );
}
