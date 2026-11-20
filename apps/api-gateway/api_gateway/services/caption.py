"""캡션 payload 읽기와 제출 기준 판정. 문장 목록·최종 문자열·토큰 수를 구한다.

화면 쪽 대응 구현은 `apps/web/src/features/workbench/lib/caption.ts`(`sentencesOf`·`finalText`·
`liveSentences`·`joinSentences`)와 `apps/web/src/features/workbench/lib/tokens.ts`
(`estimateTokens`)다. 한쪽을 고칠 때 다른 쪽을 찾을 수 있게 이름을 맞춘다.

서버도 제출 기준을 검사한다. 빈 제출은 검수 대기열에 들어가 검수자의 시간을 쓴다.
제출량은 작업자를 측정하는 객관 지표다. API를 직접 호출해 기준을 우회하면 빈 제출이 제출량에 잡힌다.

제출 판정의 기준은 서버다. 다만 판정 기준선은 화면과 같아야 한다. 규칙은 다음 한 식이다.

    제출 가능 ⟺ estimate_tokens(final_text(payload)) >= caption_min_tokens

화면의 `briefTokens >= captionMinTokens`와 같은 부등호다. 서버가 화면보다 엄하면 화면에서
통과한 제출을 서버가 거절하고, 라벨러는 무엇을 고쳐야 할지 알 수 없다. 그래서 이 모듈에 규칙을
더하지 않는다. 예를 들어 "기준이 0이어도 빈 캡션은 막는다"를 더하면 화면은 `0 >= 0`으로
통과시키므로 두 기준선이 달라진다. 빈 제출을 항상 막으려면 설정 기본값을 올린다.

파이썬은 API 서버에서, TS는 브라우저에서 돌아 두 구현을 합칠 수 없다. 그래서 판정의 입력과
출력을 `packages/reborn-contracts/caption-gate.json`에 표로 두고 양쪽 테스트가 그 표를 읽는다
(`tests/contract/test_caption_gate.py` · `apps/web/src/features/workbench/lib/tokens.test.ts`).
한쪽이 달라지면 그쪽 테스트가 실패한다.

삭제 표시(`deleted`) 문장을 빼는 서버 쪽 필터는 `live_sentences` 한 곳이다.
품질검수(`caption_qc.rules.check_caption`)도 이 함수를 부른다.

캡션을 읽는 규칙도 이 모듈에만 둔다. 제출 캡션은 모양이 세 가지다. 문장 배열
(`caption.brief_sentences`), 옛 문자열(`caption.brief`), 더 오래된 `caption` 문자열이다.
서버에서 세 모양을 읽는 함수는 `sentences_of`와 `final_text`뿐이다. 화면 쪽 짝은
`features/workbench/lib/caption.ts`의 `sentencesOf`·`finalText`다. 두 런타임의 테스트가
`packages/reborn-contracts/caption-shapes.json` 표를 함께 읽는다
(`tests/contract/test_caption_shapes.py` · `apps/web/src/features/workbench/lib/caption.test.ts`).
문장 배열만 읽으면 옛 모양의 캡션이 "캡션이 비어 있습니다"로 보류된다.
"""
from __future__ import annotations

import math
import re

__all__ = [
    "DEFAULT_MIN_TOKENS", "estimate_tokens", "final_text", "live_sentences", "sentences_of",
    "split_sentences", "too_short",
]

#: 프로젝트를 읽지 못했을 때의 최소 토큰 수. 화면의 `project.data?.caption_min_tokens ?? 50`과 같다.
#: `projects.caption_min_tokens`가 `NOT NULL DEFAULT 50`이라 정상 경로에서는 쓰이지 않는다.
DEFAULT_MIN_TOKENS = 50

#: CJK·한글 글자. `tokens.ts`의 `[　-鿿가-힯]`와 같은 구간이다(U+3000~U+9FFF · U+AC00~U+D7AF).
#: `caption-gate.json` 표의 값으로 두 구간이 같은지 확인한다.
_CJK = re.compile(r"[　-鿿가-힯]")


def live_sentences(sentences: list[dict]) -> list[dict]:
    """삭제 표시한 문장과 빈 문장을 뺀 목록을 돌려준다.

    라벨러가 삭제 표시한 문장은 최종 캡션에서 빠진다. 그래서 제출 게이트·품질검수·내보내기도
    이 목록을 쓴다. 화면의 `features/workbench/lib/caption.ts` `liveSentences`와 같은 규칙이다.
    서버에서는 이 함수만 이 규칙을 구현한다(`check_caption`도 이 함수를 부른다).
    `text`가 문자열이 아닌 문장은 빈 문장으로 본다. 예상 밖의 값 하나 때문에 검사가 500으로
    실패하지 않게 한다.
    """
    return [
        s for s in sentences
        if not s.get("deleted") and isinstance(s.get("text"), str) and s["text"].strip()
    ]


#: 문장 경계. 화면 `CaptionView.splitSentences`의 정규식과 같다.
#: 점이 둘 이상 이어진 말줄임표(`...`) 뒤에서는 끊지 않는다.
#: `…`·`⋯`는 종결 부호가 아니므로 그 뒤에서도 끊지 않는다.
_SENTENCE_BREAK = re.compile(r"(?<=[.?!。])(?<!\.\.)\s+")

#: 여는 따옴표별 닫는 따옴표. 화면 `CaptionView`의 `QUOTE_PAIRS`와 같다.
_QUOTE_PAIRS = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "「": "」", "『": "』"}

#: 아포스트로피 앞에 오는 영문자·숫자. 이 글자 뒤의 `'`(`It's`)는 아포스트로피로 본다.
_APOSTROPHE_BEFORE = re.compile(r"[A-Za-z0-9]")


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """닫힌 따옴표 묶음의 (여는 위치, 닫는 위치) 목록을 돌려준다.

    여는 따옴표마다 같은 짝의 첫 닫는 따옴표를 찾는다. 닫힌 따옴표만 묶음으로 본다.
    닫히지 않은 따옴표까지 묶으면 따옴표 하나 때문에 그 뒤 캡션 전체가 한 문장이 된다.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        close = _QUOTE_PAIRS.get(ch)
        if close and not (ch == "'" and i > 0 and _APOSTROPHE_BEFORE.match(text[i - 1])):
            j = text.find(close, i + 1)
            if j != -1:
                spans.append((i, j))
                i = j + 1
                continue
        i += 1
    return spans


def split_sentences(text: str | None) -> list[str]:
    """문자열 캡션을 문장으로 나눈다. 화면 `CaptionView.splitSentences`와 같은 규칙이다.

    종결 부호(`.` `?` `!` `。`) 뒤의 공백에서 끊는다. 끊을 위치가 없으면 전체가 한 문장이다.
    말줄임표(`...` `…` `⋯`) 뒤에서는 끊지 않는다. 보도 캡션은 말줄임표를 문장 안에서 쓰므로
    끊으면 `소토 3타점...`과 `기선 제압`처럼 한 문장이 둘로 나뉜다.
    닫힌 따옴표 안에서도 끊지 않는다. 인용 발언(`"왔다. 갔다."고 말했다`)을 끊으면 캡션에 깨진
    문장이 생긴다. 구간 지정과 검수·품질검수는 모두 문장 단위로 동작한다.
    문자열 캡션에만 쓴다. 문장 배열이 있는 payload는 배열을 기준으로 쓴다.
    두 런타임의 결과는 `packages/reborn-contracts/caption-shapes.json` 표로 맞춘다.
    """
    t = (text or "").strip()
    if not t:
        return []
    spans = _quoted_spans(t)
    parts: list[str] = []
    last = 0
    for m in _SENTENCE_BREAK.finditer(t):
        if any(a < m.start() < b for a, b in spans):
            continue
        parts.append(t[last:m.start()].strip())
        last = m.end()
    parts.append(t[last:].strip())
    parts = [p for p in parts if p]
    return parts or [t]


def _saved_sentences(cap: dict) -> list[dict] | None:
    """저장된 문장 배열에서 개체만 남긴다. 개체가 하나도 없으면 `None`을 돌려준다.

    `None`이면 부르는 쪽이 문자열 모양을 읽는다. 배열이 있으면 문자열은 파생값이라 읽지 않는다.
    """
    sents = cap.get("brief_sentences")
    if not isinstance(sents, list):
        return None
    kept = [s for s in sents if isinstance(s, dict)]
    return kept or None


def sentences_of(payload: dict | None) -> list[dict]:
    """제출 payload의 캡션 문장 목록을 꺼낸다. 세 모양을 모두 읽는다.

    1. `{"caption": {"brief_sentences": [...]}}`: 지금 화면이 보내는 모양.
       배열을 그대로 돌려준다. 삭제 표시한 문장도 들어 있고, 그 문장은 `live_sentences`가 뺀다.
    2. `{"caption": {"brief": "…"}}`: 배열이 없거나 개체가 없는 옛 payload.
    3. `{"caption": "…"}`: `caption`이 문자열인 가장 오래된 모양.

    2와 3은 `split_sentences`로 나누고 id를 위치로 붙인다(`s0`, `s1`, …).
    화면 `toSentences`가 같은 id를 붙이므로 품질검수 지적이 검수 화면의 같은 문장에 붙는다.
    1만 읽으면 품질검수가 2와 3의 캡션을 "캡션이 비어 있습니다"로 보류한다.
    """
    cap = (payload or {}).get("caption")
    if isinstance(cap, str):
        text: str | None = cap
    elif isinstance(cap, dict):
        saved = _saved_sentences(cap)
        if saved is not None:
            return saved
        brief = cap.get("brief")
        text = brief if isinstance(brief, str) else None
    else:
        return []
    return [{"id": f"s{i}", "text": t} for i, t in enumerate(split_sentences(text))]


def final_text(payload: dict | None) -> str:
    """제출 payload에서 최종 캡션 문자열을 읽는다. 세 가지 payload 모양을 모두 읽는다.

    모양을 고르는 규칙은 `sentences_of`와 같다. 문장 배열을 우선하고, 없으면 문자열을 쓴다.

    1. `{"caption": {"brief_sentences": [...]}}`: 지금 화면이 보내는 모양.
       삭제되지 않은 문장만 공백 하나로 잇는다(`joinSentences`와 같은 규칙).
       모든 문장을 지운 배열은 빈 캡션이다. 남아 있는 `brief`는 파생값이라 쓰지 않는다.
    2. `{"caption": {"brief": "…"}}`: 문장 배열이 없거나 개체가 없는 옛 payload. `brief`를 쓴다.
    3. `{"caption": "…"}`: `caption`이 문자열인 가장 오래된 payload(`_caption_of` 참고).

    2와 3은 양끝 공백만 제거하고 그대로 쓴다. 납품에 실리는 옛 캡션의 줄바꿈을 바꾸지 않는다.

    DB에는 2와 3 모양의 옛 payload가 남아 있다. 옛 제출을 받아 다시 내는 경로에서 배열만
    읽으면 정상 캡션이 0토큰이 되어 반려된 작업을 다시 제출할 수 없다.
    """
    cap = (payload or {}).get("caption")
    if isinstance(cap, str):
        return cap.strip()
    if not isinstance(cap, dict):
        return ""
    saved = _saved_sentences(cap)
    if saved is not None:
        return " ".join(s["text"].strip() for s in live_sentences(saved))
    brief = cap.get("brief")
    return brief.strip() if isinstance(brief, str) else ""


def estimate_tokens(text: str) -> int:
    """캡션 토큰 수를 근사한다. 화면의 `estimateTokens`와 같은 값을 낸다.

    화면 쪽 구현은 `apps/web/src/features/workbench/lib/tokens.ts`의 `estimateTokens`다.
    한국어는 대략 1.5~2자가 한 토큰이다. 그래서 CJK 글자 수를 1.5로 나눠 올림하고
    ASCII 낱말 수를 더한다. 이 함수는 정확한 값보다 화면과 같은 값을 우선한다.
    더 정확한 방법(tiktoken 등)으로 바꾸려면 양쪽을 같은 커밋에서 바꾸고
    `caption-gate.json`의 표도 다시 계산한다.

    알려진 차이가 하나 있다. JS `trim()`은 U+FEFF(BOM)를 지우지만 파이썬 `strip()`은 지우지
    않는다. BOM은 CJK 구간 밖이라 앞 낱말에 붙는다. 낱말 수는 바뀌지 않으므로 결과는 같다.
    """
    t = text.strip()
    if not t:
        return 0
    cjk = len(_CJK.findall(t))
    ascii_part = _CJK.sub(" ", t).strip()
    ascii_words = len(ascii_part.split()) if ascii_part else 0
    # 나눗셈은 JS `Math.ceil(cjk / 1.5)`와 같은 부동소수 연산으로 둔다.
    # 두 런타임이 같은 연산을 해야 같은 값이 나온다. 정수 연산으로 바꾸지 않는다.
    return math.ceil(cjk / 1.5) + ascii_words


def too_short(payload: dict | None, min_tokens: int) -> int | None:
    """제출 기준 미달이면 잰 토큰 수를, 충족하면 `None`을 돌려준다. 제출 게이트의 유일한 판정이다.

    불리언 대신 토큰 수를 돌려준다. 빈 캡션의 결과는 0이다. 0을 거짓 값으로 읽으면 빈 캡션이
    통과하므로 호출부는 `is not None`으로 판정한다.

    Args:
        payload: 제출 payload (`WorkSubmitRequest.payload`).
        min_tokens: 프로젝트 설정 `caption_min_tokens`.

    Returns:
        미달이면 잰 토큰 수, 아니면 `None`.
    """
    tokens = estimate_tokens(final_text(payload))
    return tokens if tokens < min_tokens else None
