/**
 * 캡션 문장 유틸. 문장이 저장 단위다.
 *
 * 문장을 개체로 두는 이유: 캡션이 문자열 하나면 문장마다 고유한 식별자가 없다.
 * 마침표 하나를 지워 두 문장이 합쳐지면 뒤 번호가 모두 밀린다.
 * 그러면 인덱스에 붙여 둔 값(검수 판정)이 다른 문장을 가리키고, 아무 경고도 나오지 않는다.
 * `id`에 붙이면 편집·삽입·순서 변경에도 안전하다.
 *
 * 검수·내보내기·토큰 수 계산이 `caption.brief` 문자열을 읽으므로 문자열도 함께 저장한다.
 * 이 문자열은 삭제되지 않은 문장을 이어 붙인 파생값이다. 규칙이 하나로 유지되도록 `joinSentences`에서만 만든다.
 *
 * 문자열만 있는 이전 형식 payload는 마이그레이션 없이 `toSentences`가 읽을 때 배열로 만든다.
 * 다음 저장부터 배열도 함께 저장된다.
 */
import { splitSentences } from "../components/CaptionView";

/**
 * 캡션 문장 하나.
 *
 * `deleted`는 검수와 같은 규약이다. 배열에서 바로 빼지 않고 표시만 해서 되돌릴 수 있게 두고,
 * 최종 문자열에서만 뺀다.
 * 이전 payload에 남은 다른 키(문장 구간 등)는 읽지 않고, 펼쳐 저장할 때 그대로 따라간다.
 */
export type CaptionSentence = {
  id: string;
  text: string;
  deleted?: boolean;
};

/**
 * 새 문장 id를 만든다. id는 한 payload 안에서만 구분되면 되고 전역에서 유일할 필요는 없다.
 *
 * id는 클라이언트가 만든다.
 * 초안은 30초마다 자동저장되고 오프라인 편집이 섞인다.
 * 문장 하나를 추가할 때마다 서버를 왕복하면 이 흐름이 깨진다.
 */
export function newSentenceId(): string {
  return "s" + Math.random().toString(36).slice(2, 10);
}

/**
 * 저장된 배열에서 개체만 남긴다. 개체가 하나도 없으면 `null`이다(서버 `_saved_sentences`와 같다).
 *
 * `null`, 문자열, 숫자 같은 개체가 아닌 원소를 펼치면 `text`가 없는 문장이 생겨 화면이 깨진다.
 */
function savedSentences(saved: unknown): CaptionSentence[] | null {
  if (!Array.isArray(saved)) return null;
  const kept = saved.filter(
    (s): s is CaptionSentence => typeof s === "object" && s !== null && !Array.isArray(s),
  );
  return kept.length ? kept : null;
}

/**
 * 저장된 문장 배열을 쓰고, 배열이 없으면 문자열을 나눠 문장 배열을 만든다.
 *
 * 이전 형식 payload(문자열만)의 읽기 호환은 이 함수에서만 처리한다.
 * 배열이 있으면 파생값인 문자열은 읽지 않는다.
 * payload 전체를 받을 때는 `caption`이 문자열인 모양까지 읽는 `sentencesOf`를 쓴다.
 */
export function toSentences(
  saved: CaptionSentence[] | undefined | null,
  text: string | undefined | null,
): CaptionSentence[] {
  const kept = savedSentences(saved);
  if (kept) {
    // 저장된 문장에 id가 없을 수 있다(손으로 만든 payload·이전 버전 실험). id를 채워서 쓴다.
    return kept.map((s) => ({ ...s, id: s.id || newSentenceId() }));
  }
  /* 문자열만 있는 이전 형식 payload는 id를 위치(`s0`…`sN`)로 정해 다시 열어도 같게 한다.
     검수는 문장별 판정을 id에 붙여 `review_draft`에 저장하고 다음 방문 때 되살린다.
     id가 열 때마다 바뀌면 되살린 판정이 어느 문장에도 붙지 않는다.
     배열이 없는 payload는 이미 제출되어 바뀌지 않으므로 위치를 id로 써도 된다.
     라벨러가 새로 더하는 문장은 `newSentenceId()`(8자 랜덤)를 쓰므로 위치 id와 겹치지 않는다. */
  return splitSentences(text ?? "").map((t, i) => ({ id: `s${i}`, text: t }));
}

/** `label_data`나 payload에서 `caption`을 꺼낸다. 알려진 모양이 아니면 `undefined`다. */
function captionOf(data: unknown): unknown {
  return typeof data === "object" && data !== null
    ? (data as { caption?: unknown }).caption
    : undefined;
}

/**
 * 제출 payload(`label_data`)의 캡션 문장 목록을 꺼낸다. 캡션의 세 모양을 모두 읽는다.
 *
 * 모양은 문장 배열(`caption.brief_sentences`), 이전 형식 문자열(`caption.brief`), 그보다 오래된
 * `caption` 문자열이다. 배열에 개체가 있으면 그 배열을 쓰고, 없으면 문자열을 나눠 위치 id를 붙인다.
 * 서버 쪽 짝은 `services/caption.py`의 `sentences_of`다. 두 구현은
 * `packages/reborn-contracts/caption-shapes.json`을 함께 읽어 테스트한다(`caption.test.ts`).
 * 읽는 곳마다 한 모양만 보면 있는 캡션이 "캡션 없음"으로 보인다.
 */
export function sentencesOf(data: unknown): CaptionSentence[] {
  const cap = captionOf(data);
  if (typeof cap === "string") return toSentences(null, cap);
  if (typeof cap !== "object" || cap === null) return [];
  const c = cap as { brief?: unknown; brief_sentences?: unknown };
  return toSentences(
    savedSentences(c.brief_sentences),
    typeof c.brief === "string" ? c.brief : null,
  );
}

/**
 * 제출 payload(`label_data`)의 최종 캡션 문자열을 만든다. 서버 `final_text`와 같은 값을 낸다.
 *
 * 문장 배열이면 유효한 문장을 잇는다(`joinSentences`). 모든 문장을 지운 배열은 빈 캡션이다.
 * 문자열이면 양끝 공백만 지우고 그대로 쓴다.
 */
export function finalText(data: unknown): string {
  const cap = captionOf(data);
  if (typeof cap === "string") return cap.trim();
  if (typeof cap !== "object" || cap === null) return "";
  const c = cap as { brief?: unknown; brief_sentences?: unknown };
  const saved = savedSentences(c.brief_sentences);
  if (saved) return joinSentences(saved);
  return typeof c.brief === "string" ? c.brief.trim() : "";
}

/**
 * 유효한 문장만 고른다. 삭제 표시한 문장과 빈 문장은 뺀다. 다음 단계는 이 결과를 읽는다.
 *
 * 라벨러가 삭제 표시한 문장은 최종 캡션에 없으므로 검수·내보내기에서도 뺀다.
 * 이 필터가 두 곳에 있으면 화면마다 문장 수가 달라질 수 있다.
 * `text`가 문자열이 아닌 문장은 빈 문장으로 본다(서버 `live_sentences`와 같다).
 */
export function liveSentences(sentences: CaptionSentence[]): CaptionSentence[] {
  return sentences.filter(
    (s) => !s.deleted && typeof s.text === "string" && s.text.trim() !== "",
  );
}

/**
 * 최종 캡션 문자열을 만든다. 삭제 표시가 없는 문장만 이어 붙인다.
 *
 * 화면에 보인 값과 저장·내보내기 값이 같도록 이 함수에서만 만든다.
 * 문장마다 양끝 공백을 지우고 공백 하나로 잇는다(서버 `final_text`와 같다).
 */
export function joinSentences(sentences: CaptionSentence[]): string {
  return liveSentences(sentences).map((s) => s.text.trim()).join(" ");
}
