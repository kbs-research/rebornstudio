"""캡션 읽기 규칙의 판정 표(`caption-shapes.json`)를 서버 구현으로 돌리는 테스트.

화면(`apps/web/src/features/workbench/lib/caption.test.ts`)도 같은 파일을 읽는다.
서버 테스트만 통과하고 화면 테스트가 실패하면 두 런타임이 캡션을 다르게 읽는 상태다.
"""
import json
import pathlib

import pytest
from api_gateway.services import caption
from api_gateway.services.caption_qc import rules, runner
from api_gateway.services.exports import export_service

VECTORS = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[4]
        / "packages/reborn-contracts/caption-shapes.json"
    ).read_text(encoding="utf-8")
)


def test_vectors_file_is_not_empty():
    """판정 표에 사례가 충분하고 캡션 모양 세 가지가 모두 들어 있는지 확인한다.

    표가 비면 아래 테스트가 사례 0건으로 통과한다.
    """
    cases = VECTORS["cases"]
    assert len(cases) >= 10
    caps = [c["payload"].get("caption") for c in cases]
    assert any(isinstance(c, str) for c in caps)
    assert any(isinstance(c, dict) and "brief_sentences" not in c for c in caps)
    assert any(isinstance(c, dict) and c.get("brief_sentences") for c in caps)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["why"][:30])
def test_sentences_of_matches_vector(case):
    """문장 목록이 표의 기대값과 같은지 확인한다. 이전 모양은 위치 id(`s0`…)로 나뉜다."""
    assert caption.sentences_of(case["payload"]) == case["sentences"], case["why"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["why"][:30])
def test_final_text_matches_vector(case):
    """최종 문자열이 표의 기대값과 같은지 확인한다. 최종 문자열은 문장 목록과 같은 캡션 모양에서 만든다."""
    assert caption.final_text(case["payload"]) == case["text"], case["why"]


#: 문자열 캡션만 있는 사례. 문장 배열이 없어서 문장을 나누는 규칙이 결과를 정한다.
_STRING_CASES = [
    c for c in VECTORS["cases"]
    if isinstance(c["payload"].get("caption"), dict)
    and isinstance(c["payload"]["caption"].get("brief"), str)
    and not c["payload"]["caption"].get("brief_sentences")
]


def test_vectors_cover_ellipsis():
    """표에 말줄임표 사례가 있는지 확인한다.

    사례가 빠지면 말줄임표에서 문장을 끊지 않는 규칙을 검사하는 곳이 없어진다.
    """
    briefs = [c["payload"]["caption"]["brief"] for c in _STRING_CASES]
    assert any("..." in b for b in briefs)
    assert any("…" in b for b in briefs)


def test_vectors_cover_quotes():
    """표에 따옴표 사례가 있는지 확인한다.

    사례가 빠지면 인용 발언 안에서 문장을 끊지 않는 규칙을 검사하는 곳이 없어진다.
    """
    briefs = [c["payload"]["caption"]["brief"] for c in _STRING_CASES]
    assert any('"' in b for b in briefs)
    assert any("\u201c" in b for b in briefs)
    assert any("'" in b for b in briefs)


@pytest.mark.parametrize("case", _STRING_CASES, ids=lambda c: c["why"][:30])
def test_export_sentence_count_splits_like_caption(case):
    """완료 목록의 문장 수가 화면과 같은 규칙으로 나뉘는지 확인한다.

    내보내기 쪽 구현이 다른 규칙으로 나누면 화면에 보이는 문장 수와 배지 수가 어긋난다.
    """
    brief = case["payload"]["caption"]["brief"]
    assert export_service._split_sentences(brief) == [s["text"] for s in case["sentences"]]


@pytest.mark.parametrize(
    "payload",
    [
        {"caption": {"brief": "앵커가 소식을 전한다. 기자가 현장을 연결한다."}},
        {"caption": {"brief": "앵커가 소식을 전한다.", "brief_sentences": []}},
        {"caption": "앵커가 소식을 전한다."},
    ],
    ids=["brief 문자열", "빈 배열 + brief", "caption 문자열"],
)
def test_qc_reads_legacy_caption(payload):
    """품질검수가 이전 모양의 캡션을 빈 캡션으로 보류하지 않는지 확인한다.

    문장 배열만 읽으면 세 경우 모두 `caption_empty`(error)가 난다.
    """
    found = rules.check_caption(runner._sentences_of(payload))
    assert "caption_empty" not in {f["check"] for f in found}


def test_qc_findings_point_at_positional_ids():
    """이전 문자열 캡션에서 나온 지적이 위치 id를 가리키는지 확인한다. 화면이 같은 id로 문장을 그린다."""
    payload = {"caption": {"brief": "짧다. 종결되지 않은 문장"}}
    found = rules.check_caption(runner._sentences_of(payload))
    assert {f.get("sentence_id") for f in found} <= {"s0", "s1"}
    assert any(f.get("sentence_id") == "s1" for f in found)
