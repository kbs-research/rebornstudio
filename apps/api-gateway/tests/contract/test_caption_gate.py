"""캡션 제출 게이트의 순수 판정 테스트. 판정 표(`caption-gate.json`)의 사례를 그대로 돌린다.

화면(`apps/web/src/features/workbench/lib/tokens.test.ts`)도 같은 표 파일을 읽는다.
서버 테스트만 통과하고 화면 테스트가 실패하면 두 런타임의 판정이 달라진 상태다.
그러면 화면이 통과시킨 캡션을 서버가 거절한다.
"""
import json
import pathlib

import pytest
from api_gateway.services import caption

VECTORS = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[4]
        / "packages/reborn-contracts/caption-gate.json"
    ).read_text(encoding="utf-8")
)


def test_vectors_file_is_not_empty():
    """판정 표에 토큰 사례와 payload 사례가 충분히 있는지 확인한다.

    표가 비면 아래 두 테스트가 사례 0건으로 통과해 결과를 믿을 수 없다.
    """
    assert len(VECTORS["tokens"]) >= 10
    assert len(VECTORS["payloads"]) >= 8


@pytest.mark.parametrize("case", VECTORS["tokens"], ids=lambda c: c["why"][:30])
def test_estimate_tokens_matches_vector(case):
    """토큰 수가 표의 기대값과 같은지 확인한다. 표의 값은 화면의 `estimateTokens`와 맞춘 값이다."""
    assert caption.estimate_tokens(case["text"]) == case["expected"], case["why"]


@pytest.mark.parametrize("case", VECTORS["payloads"], ids=lambda c: c["why"][:30])
def test_final_text_matches_vector(case):
    """payload에서 꺼낸 최종 캡션이 표의 기대값과 같은지 확인한다. 이전 payload 모양 세 가지도 모두 읽는다."""
    assert caption.final_text(case["payload"]) == case["text"], case["why"]


def test_deleted_sentences_do_not_count():
    """삭제 표시한 문장을 게이트 통과에 세지 않는지 확인한다.

    표에도 같은 사례가 있지만 이 규칙은 테스트로 한 번 더 보장한다.
    규칙이 깨지면 라벨러가 문장을 지워 놓고 제출할 수 있고, 검수자는 빈 캡션을 받는다.
    """
    long_text = "앵커가 오늘 첫 소식으로 전국에 내린 폭우 피해를 전하고 현장 기자를 연결해 상황을 묻는다."
    payload = {"caption": {"brief_sentences": [{"id": "s0", "text": long_text, "deleted": True}]}}
    assert caption.final_text(payload) == ""
    assert caption.too_short(payload, 50) == 0


def test_too_short_returns_the_count_not_a_flag():
    """기준에 못 미치면 잰 토큰 수를, 기준을 채우면 `None`을 돌려주는지 확인한다.

    불리언 대신 토큰 수를 돌려주므로 0을 통과로 잘못 읽지 않는다.
    """
    assert caption.too_short({"caption": {"brief": ""}}, 50) == 0
    assert caption.too_short({"caption": {"brief": "짧다."}}, 50) == 3
    assert caption.too_short({"caption": {"brief": "짧다."}}, 3) is None
    # 화면도 같은 부등호를 쓴다: `briefTokens >= captionMinTokens`.
    assert caption.too_short({"caption": {"brief": "짧다."}}, 4) == 3


def test_zero_minimum_lets_anything_through():
    """기준이 0이면 빈 캡션도 통과하는지 확인한다. 화면의 `0 >= 0`과 같은 기준이다.

    주의: 서버만 더 엄하게 두면 화면과 서버의 기준이 달라진다.
    빈 제출을 항상 막으려면 설정 기본값을 올린다.
    """
    assert caption.too_short({"caption": {"brief": ""}}, 0) is None
