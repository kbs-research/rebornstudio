"""캡션 품질검수의 규칙 검사.

정확하고 비용이 들지 않는 규칙 검사를 모델 검토보다 먼저 한다.

이 모듈에는 순수 함수만 둔다. 값(문장, 길이, 이미 계산된 납품 불가 사유)을 받아 지적을 돌려주고,
DB와 다른 도메인은 import하지 않는다. 재료는 호출부(`runner`)가 모은다.
`work`·`analysis`·`export_*`를 직접 import하면 도메인 독립 계약이 깨지고,
순수 함수라서 경계값을 DB 없이 표 형태의 테스트로 검증할 수 있다.

지적(`findings`)에서 판정(`verdict`)과 게이트 값(`qc_status`)을 도출하는 일은 이 모듈에서만 한다.
화면이나 호출부가 다시 계산하면 화면은 통과인데 게이트는 막는 경우가 생긴다.
"""
from __future__ import annotations

import re
from typing import Any, Final, Literal

from api_gateway.services.caption import live_sentences

#: 지적의 심각도. `error`는 반려추천, `warn`은 사람의 재검토 대상(`needs_review`, 납품 `held`)이다.
#: `info`는 검수자 화면에 표시하지만 `verdict_of`가 세지 않아 판정은 `pass`이고 납품을 막지 않는다.
#: 선택 항목이 빈 경우에 `warn`을 주면 선택 항목이 사실상 필수가 되므로 `info`를 따로 둔다.
Severity = Literal["info", "warn", "error"]
Verdict = Literal["pass", "needs_review", "reject_suggested"]
QcStatus = Literal["pending", "passed", "held"]

#: 한 문장으로 인정할 최소 길이(글자 수). 이보다 짧으면 "과도한 짧은 문장"으로 본다.
#: 문장 배열에 남는 문장 조각("네.", "그렇다.")을 잡는 값이다.
MIN_SENTENCE_CHARS = 6

#: 한국어 문장 종결 패턴. 종결부호나 흔한 종결어미로 끝나지 않으면 문장 종결 오류로 본다.
#: 캡션 기준 문체가 `~합니다`라서 그 계열을 중심에 두고 명사형 종결(`~음`·`~함`)도 허용한다.
_ENDING = re.compile(r"([.!?…]|다|요|음|함|임|까|죠|네|군요|습니다|합니다)\s*$")

#: 캡션 초안에 남은 모델의 안내 문장을 알아보는 표현.
#: 안내 문장은 영상 내용 대신 입력 자료를 설명하는 문장이다.
#: 장면 요약 모델은 정보가 모자란 영상(컬러바 등)에서 `제공된 정보가 부족하여 … 서술하겠습니다` 같은
#: 문장을 내고, 라벨러가 지우지 않으면 그 문장이 납품 캡션에 남는다.
#: 보도 문장에도 `정보`·`입력`·`파악`이 나오므로 모델이 반복해 쓰는 표현만 둔다. 넓히면 오탐이 는다.
META_SENTENCE_PATTERNS: Final[tuple[str, ...]] = (
    r"제공된\s*정보",
    r"입력된\s*(?:메타\s*데이터|프레임)",
    r"서술하겠습니다",
    r"(?:파악|작성)하기\s*어렵",
)
_META_SENTENCE = re.compile("|".join(META_SENTENCE_PATTERNS))

#: 화면에 표시할 개인정보 종류 이름. 지적 문장에는 내부 키(`resident_id`) 대신 이 이름을 쓴다.
_PII_KO: dict[str, str] = {
    "resident_id": "주민등록번호",
    "phone": "전화번호",
    "email": "이메일",
    "card": "카드번호",
}

#: 개인정보로 보이는 형식. 형식이 정해진 번호만 규칙으로 잡는다.
#: 이름·주소는 형식이 없어 오탐이 나므로 잡지 않는다. 모델 검사에도 개인정보 항목은 없다.
#: 주의: 숫자 패턴은 `\b` 대신 앞뒤에 숫자가 없다는 조건만 건다. 파이썬 `re`의 `\w`는 한글도
#: 단어 문자로 보고 조사는 값에 바로 붙는다(`5678로`). 그래서 `\b`로 끝을 고정하면
#: `010-1234-5678로 연락 바랍니다` 같은 흔한 표기를 놓친다.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 주민등록번호는 6자리와 7자리를 하이픈으로 이은 형식만 잡는다.
    # 하이픈 없는 13자리는 다른 숫자와 구별되지 않는다.
    ("resident_id", re.compile(r"(?<!\d)\d{6}\s*-\s*[1-4]\d{6}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)01[016-9][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")),
    # 이메일은 ASCII로 한정한다. `[\w.-]`는 한글도 포함해서 `example.co.kr로`까지 값으로 잡는다.
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("card", re.compile(r"(?<!\d)(?:\d{4}[-\s]){3}\d{4}(?!\d)")),
)


def _finding(check: str, severity: Severity, message: str, sentence_id: str | None = None) -> dict:
    """지적 하나를 만든다. 저장 형식(`caption_qc_runs.findings`)과 같다.

    `sentence_id`는 문장 단위 지적일 때만 붙인다. 값이 없으면 빈 문자열 대신 키를 뺀다.
    """
    f: dict[str, Any] = {"check": check, "severity": severity, "message": message}
    if sentence_id:
        f["sentence_id"] = sentence_id
    return f


def check_caption(sentences: list[dict]) -> list[dict]:
    """캡션 문장 규칙을 검사한다.

    빈 캡션, 과도하게 짧은 문장, 종결 오류를 본다.
    모델의 안내 문장(`META_SENTENCE_PATTERNS`)은 문장 번호와 함께 참고(`info`)로 낸다.
    지울지는 사람이 정하므로 판정(`verdict_of`)에 넣지 않는다.
    "부적절 문장"은 문맥 판단이 필요해 모델 검사가 맡는다.

    Args:
        sentences: `[{id, text, deleted?}]`. 삭제 표시된 문장은 최종 캡션에 없으므로 건너뛴다.
            제출 게이트와 검사가 같은 문장을 보도록 이 규약은 `services.caption.live_sentences`
            한 곳에서 정한다.
            화면의 `features/workbench/lib/caption.ts` `liveSentences`도 같은 규약이다.
    """
    live = live_sentences(sentences)
    if not live:
        # 캡션은 유일한 제출 게이트다. 비어 있으면 납품할 수 없으므로 가장 높은 심각도를 준다.
        return [_finding("caption_empty", "error", "캡션이 비어 있습니다")]

    out: list[dict] = []
    for n, s in enumerate(live, start=1):
        text = s["text"].strip()
        sid = s.get("id")
        if _META_SENTENCE.search(text):
            out.append(_finding(
                "meta_sentence", "info",
                f"{n}번째 문장은 영상 내용이 아니라 모델의 안내 문장으로 보입니다. "
                "지울지 확인합니다", sid,
            ))
        if len(text) < MIN_SENTENCE_CHARS:
            out.append(_finding(
                "sentence_too_short", "warn",
                f"문장이 너무 짧습니다({len(text)}자). 미완성 문장이 남았는지 확인합니다", sid,
            ))
        if not _ENDING.search(text):
            out.append(_finding(
                "sentence_ending", "warn", "문장이 종결되지 않았습니다", sid,
            ))
    return out


#: 영상 메타데이터 네 항목 `(키, 화면 이름)`. 운영자가 영상 상세에서 입력하고 클립이 물려받는다.
#: 키는 영상 컬럼·납품 스키마와 같다. 운영자 입력란(`services/video_meta.FIELDS`), 화면
#: (`lib/video_meta.ts`)과 같은 집합이어야 하며 테스트가 셋을 비교한다.
#: 네 항목은 납품 선택 항목이라(`ClipMetadata`가 빈 값을 받는다)
#: 지적 문구에 `필수값`이라고 쓰지 않는다.
_VIDEO_META: Final[tuple[tuple[str, str], ...]] = (
    ("broadcaster", "방송사"),
    ("program_name", "프로그램명"),
    ("genre", "장르"),
    ("aired_at", "방영일"),
)


def _i_ga(word: str) -> str:
    """지적 문장의 조사를 고른다. 받침이 있으면 `이`, 없으면 `가`다(`장르가` · `방영일이`)."""
    ch = word[-1:] if word else ""
    if "가" <= ch <= "힣":
        return "이" if (ord(ch) - 0xAC00) % 28 else "가"
    return "이"


def _blank(v: Any) -> bool:
    """값이 비었으면 True를 돌려준다. `None`, 빈 문자열, 공백뿐인 문자열, 빈 컬렉션이 빈 값이다."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) == 0
    return False


def check_metadata(video_meta: dict | None) -> list[dict]:
    """영상 메타데이터 누락을 검사한다. 방송사·프로그램명·장르·방영일을 본다.

    네 항목은 운영자가 영상 상세에서 입력하는 영상 단위 값이다. 라벨러는 이 값을 고칠 수 없으므로
    심각도는 `info`이고 판정에 포함하지 않는다. 문구는 고칠 곳(영상 메타데이터)을 가리킨다.
    납품 계약(`ClipMetadata`)은 네 항목 모두 `None`을 허용해 비어 있으면 빈 값으로 납품된다.

    Args:
        video_meta: `{broadcaster, program_name, genre, aired_at}`.
            `services/video_meta.of_video`가 만든 값이다.
    """
    meta = video_meta or {}
    missing = [ko for key, ko in _VIDEO_META if _blank(meta.get(key))]
    if not missing:
        return []
    return [_finding(
        "metadata_missing", "info",
        f"영상 메타에 {' · '.join(missing)}{_i_ga(missing[-1])} 없습니다",
    )]


def check_profanity(sentences: list[dict], vocabulary: list[str]) -> list[dict]:
    """비속어·부적절 표현을 검사한다. 사전 규칙이라 모델 없이 실행된다.

    보도영상 캡션은 발언을 인용하므로 비속어가 정당하게 들어갈 수 있다.
    그래서 심각도는 `warn`이고 사람이 맥락을 보고 정한다. 자동 검사는 반려하지 않는다.
    사전은 프로젝트마다 기준이 달라 인자로 받는다.
    """
    if not vocabulary:
        return []
    out: list[dict] = []
    for s in sentences:
        if s.get("deleted"):
            continue
        text = s.get("text") or ""
        hits = sorted({w for w in vocabulary if w and w in text})
        if hits:
            out.append(_finding(
                "profanity", "warn",
                f"부적절할 수 있는 표현: {' · '.join(hits)}", s.get("id"),
            ))
    return out


def check_pii(sentences: list[dict]) -> list[dict]:
    """캡션의 개인정보 텍스트를 검사한다. 형식이 정해진 것만 정규식으로 잡는다.

    금칙어는 `check_profanity`가 사전으로 검사한다.
    화면 속 전화번호처럼 정당하게 적힌 값도 납품물에서 빠져야 하므로 `error`(반려추천)로 올린다.
    최종 판단은 사람이 한다.
    """
    out: list[dict] = []
    for s in sentences:
        if s.get("deleted"):
            continue
        text = s.get("text") or ""
        for kind, pat in _PII_PATTERNS:
            if pat.search(text):
                out.append(_finding(
                    "pii", "error",
                    f"개인정보로 보이는 값이 있습니다({_PII_KO.get(kind, kind)})",
                    s.get("id"),
                ))
    return out


def check_clip_length(
    duration_seconds: float | None, min_seconds: int | None, max_seconds: int | None
) -> list[dict]:
    """클립 길이 위반을 검사한다. 기준을 벗어나면 리뷰어 확인 대상으로 표시한다.

    심각도는 `warn`이다. 기준값이 없는 프로젝트(분석 기본값을 따르는 프로젝트)는 검사하지 않는다.
    """
    if duration_seconds is None:
        return []
    out: list[dict] = []
    if min_seconds is not None and duration_seconds < min_seconds:
        out.append(_finding(
            "clip_too_short", "warn",
            f"클립이 기준보다 짧습니다({duration_seconds:.1f}초 · 기준 {min_seconds}초 이상)",
        ))
    if max_seconds is not None and duration_seconds > max_seconds:
        out.append(_finding(
            "clip_too_long", "warn",
            f"클립이 기준보다 깁니다({duration_seconds:.1f}초 · 기준 {max_seconds}초 이하)",
        ))
    return out


def check_deident(report: dict | None, reported: bool) -> list[dict]:
    """비식별화 누락 의심을 검사한다. GPU 없이 실행되는 부분만 본다.

    비식별화 실행 기록과 신고 내역을 함께 참조한다.

    - 신고 내역: 라벨러의 비식별화 미흡 신고가 `domain_events`에 남는다.
      사람이 직접 보고 올린 것이라
      가장 강한 신호다.
    - 실행 기록: 비식별화가 실행되면 `videos.metadata["deident"]`에
      `review_needed`·`detection_rate`가
      기록된다.

    `detection_rate`가 0이면 얼굴이 없는 경우와 모델이 찾지 못한 경우를 구분할 수 없으므로
    확인 대상으로 올린다(`review_needed`). 통과시키면 비식별화에 실패한 영상이 납품된다.

    비식별화가 실행되지 않았으면(`report`가 `None`) 실행 기록 지적은 내지 않는다. 결과 없음을
    누락 의심으로 지적하면 비식별화를 쓰지 않는 프로젝트의 모든 작업이 보류된다.
    신고 내역은 `report`와 관계없이 지적한다.
    """
    out: list[dict] = []
    if reported:
        # 사람이 신고한 것은 기계 판단보다 강한 신호다.
        out.append(_finding(
            "deident_reported", "error",
            "비식별화 미흡으로 신고된 클립입니다. 처리 결과를 확인해주세요",
        ))
    if not report:
        return out
    if report.get("review_needed"):
        rate = report.get("detection_rate")
        detail = f"(검출률 {rate:.1%})" if isinstance(rate, int | float) else ""
        out.append(_finding(
            "deident_review", "warn",
            f"비식별화 결과 확인이 필요합니다 {detail}".strip(),
        ))
    if report.get("masked") is False:
        # 가린 얼굴이 하나도 없는 경우. 실제로 얼굴이 없을 수도 있고 찾지 못했을 수도 있다.
        out.append(_finding(
            "deident_unmasked", "warn",
            "비식별화가 가린 얼굴이 없습니다. 실제로 얼굴이 없는지 확인이 필요합니다",
        ))
    return out


def from_delivery_blockers(blockers: list[str]) -> list[dict]:
    """납품 관문이 이미 낸 사유를 지적으로 옮긴다.

    필수값·JSON Schema 규칙은 `export_package.check_row`의 결과를 그대로 쓴다. 두 곳에 구현하면
    판정이 서로 달라져 납품은 되는데 QC는 막는 경우가 생긴다.
    여기에 걸린 작업은 납품할 수 없으므로 심각도는 `error`(반려추천)다.
    """
    return [_finding("delivery_blocker", "error", b) for b in blockers]


#: 검사 이름을 화면에 표시할 단계 이름으로 바꾸는 사전.
#: 검사 이름을 만드는 이 모듈에 둔다. 화면에도 따로 두면 검사를 추가할 때 한쪽만 고쳐진다.
#: 이름은 저장하지 않고 읽을 때 붙인다. 저장하면 문구를 고쳐도 지난 행에는 이전 이름이 남는다.
_CHECK_KO: Final[dict[str, str]] = {
    # 단계 이름(체크리스트가 쓰는 이름, 0077). 단계는 지적 이름의 상위 분류다.
    # `clip_too_short`·`clip_too_long`은 `clip_length` 단계에서,
    # `deident_*` 세 가지는 `deident` 단계에서 나온다.
    # 단계 이름을 빠뜨리면 체크리스트에 `clip_length`처럼 영문 키가 그대로 표시된다.
    "caption": "캡션",
    "clip_length": "클립 길이",
    "deident": "비식별화",
    "delivery": "납품 규격",
    # 세부 묘사 단계는 입력란이 없어 실행하지 않는다.
    # 지난 실행 기록을 이름으로 표시하려고 이름만 남긴다.
    "labels": "세부 묘사",
    # 고칠 곳이 라벨러 제출본이 아니라 영상의 메타데이터임을 이름으로 드러낸다.
    "metadata": "영상 메타",
    # 규칙 검사(모델 없이).
    # 지적의 화면 이름은 그 지적이 나온 단계 이름이고, 무엇이 잘못인지는 지적 문장에 쓴다.
    # 지적마다 이름을 따로 지으면 `캡션 없음 · 캡션이 비어 있습니다`처럼 같은 내용이 반복된다.
    "caption_empty": "캡션",
    "sentence_too_short": "캡션",
    "sentence_ending": "캡션",
    "meta_sentence": "캡션",
    "profanity": "비속어",
    "pii": "개인정보",
    "clip_too_short": "클립 길이",
    "clip_too_long": "클립 길이",
    "deident_reported": "비식별화",
    "deident_review": "비식별화",
    "deident_unmasked": "비식별화",
    "delivery_blocker": "납품 규격",
    "labels_missing": "세부 묘사",
    "speaker_unnamed": "화자명",
    "metadata_missing": "영상 메타",
    # 모델 검사.
    "spelling": "맞춤법",
    "tone": "문체",
    "inappropriate": "부적절 표현",
    "glossary": "용어집",
}


def label_of(check: str) -> str:
    """검사 이름을 단계 이름으로 바꾼다. 사전에 없는 이름은 그대로 돌려준다.

    빈 문자열 대신 검사 이름을 돌려주어 사전에 무엇을 추가해야 하는지 화면에서 알 수 있게 한다.
    """
    return _CHECK_KO.get(check, check)


def source_of(check: str) -> str:
    """검사의 출처(`rules`·`model`)를 지적 단위로 돌려준다.

    `info` 지적만 있으면 규칙 판정이 `pass`라서 러너가 모델도 호출한다. 그래서 한 실행에 규칙 지적과
    모델 지적이 함께 있을 수 있고, 실행 단위 표시(`adapter_key`)만으로는 출처를 알 수 없다.
    """
    return "model" if check in _MODEL_CHECKS else "rules"


#: 모델이 낼 수 있는 검사. `adapters._ALLOWED_CHECKS`와 같은 집합이며 테스트가 이를 보장한다.
_MODEL_CHECKS: Final[frozenset[str]] = frozenset({"spelling", "tone", "inappropriate", "glossary"})


def verdict_of(findings: list[dict]) -> Verdict:
    """지적 목록으로 3분류 판정을 낸다. 판정은 이 함수에서만 도출한다.

    `error`가 하나라도 있으면 반려추천이다.
    `info`가 아닌 지적(`warn`)이 있으면 확인필요다.
    `info`만 있거나 지적이 없으면 통과다.
    """
    if any(f.get("severity") == "error" for f in findings):
        return "reject_suggested"
    # `info`(선택 항목 누락 등)는 표시만 하는 지적이라 세지 않는다.
    if any(f.get("severity") != "info" for f in findings):
        return "needs_review"
    return "pass"


def qc_status_of(verdict: Verdict) -> QcStatus:
    """3분류 판정을 납품 게이트 값으로 바꾼다.

    통과만 `passed`이고 나머지는 `held`다. 자동 검사는 반려하지 않는다.
    `held`는 사람이 다시 검토할 대상이라는 뜻이다. 반려는 리뷰어의 판정으로만 생긴다.
    """
    return "passed" if verdict == "pass" else "held"
