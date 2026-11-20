"""사람별 실적 표의 승인율·반려율 판정 테스트. 판정 표(`labeler-rates.json`)의 사례를 그대로 돌린다.

화면(`apps/web/src/features/insights/lib/rates.test.ts`)도 같은 표 파일을 읽는다.
서버는 표의 `counts`로 `row`를 내고 화면은 `row`로 표시를 그린다.
한쪽만 통과하면 비율과 괄호의 수가 서로 다른 값을 가리키는 상태다.
"""
import json
import pathlib

import pytest
from api_gateway.services.analytics.dashboard import _rate_fields

VECTORS = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[4]
        / "packages/reborn-contracts/labeler-rates.json"
    ).read_text(encoding="utf-8")
)


def test_vectors_file_is_not_empty():
    """판정 표에 사례가 충분히 있는지 확인한다. 표가 비면 아래 테스트가 사례 0건으로 통과한다."""
    assert len(VECTORS["cases"]) >= 7


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["why"][:30])
def test_rate_fields_match_vector(case):
    """제출·승인·반려 수로 낸 비율과 기준 수가 표의 기대값과 같은지 확인한다."""
    got = _rate_fields(**case["counts"])
    want = case["row"]
    assert got["approval_base"] == want["approval_base"], case["why"]
    assert got["rejection_base"] == want["rejection_base"], case["why"]
    for key in ("approval_rate", "rejection_rate"):
        if want[key] is None:
            assert got[key] is None, case["why"]
        else:
            assert got[key] == pytest.approx(want[key]), case["why"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["why"][:30])
def test_rejection_rate_is_its_own_fraction(case):
    """반려율이 표에 싣는 반려 수와 기준 수의 몫과 같은지 확인한다. 괄호가 비율과 다른 수를 쓰지 않게 한다."""
    got = _rate_fields(**case["counts"])
    if got["rejection_base"] == 0:
        assert got["rejection_rate"] is None
    else:
        assert got["rejection_rate"] == pytest.approx(case["counts"]["rejected"] / got["rejection_base"])
