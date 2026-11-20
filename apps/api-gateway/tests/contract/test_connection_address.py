"""연결 주소 변경의 시크릿 재입력 판정 표(`connection-address.json`)를 서버 구현으로 돌리는 테스트.

화면(`apps/web/src/features/platform/lib/connection_address.test.ts`)도 같은 파일을 읽는다.
서버만 통과하면 화면은 키 입력란을 비워도 저장 버튼을 열어 두고 서버가 422로 거절하는 상태가 된다.
"""
import json
import pathlib
from typing import get_args

import pytest
from api_gateway.services.stacks import connections
from reborn_contracts.connection import ConnectionProvider

VECTORS = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[4]
        / "packages/reborn-contracts/connection-address.json"
    ).read_text(encoding="utf-8")
)


def test_vectors_cover_every_provider_and_both_answers():
    """판정 표에 모든 연결 종류의 주소 키가 있고 두 답이 모두 나오는지 확인한다.

    표가 비면 아래 사례 테스트가 0건으로 통과한다.
    """
    assert len(VECTORS["cases"]) >= 10
    assert {c["required"] for c in VECTORS["cases"]} == {True, False}
    providers = set(connections.ROUTING_KEYS)
    assert providers == set(VECTORS["routing_keys"])
    assert providers == set(get_args(ConnectionProvider))


def test_routing_keys_match_the_table():
    """서버의 주소 키 표가 판정 표와 같은지 확인한다."""
    assert {k: list(v) for k, v in connections.ROUTING_KEYS.items()} == VECTORS["routing_keys"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["why"] for c in VECTORS["cases"]])
def test_secret_required_matches_the_table(case: dict):
    """판정 표의 사례마다 서버의 판정이 기대값과 같은지 확인한다."""
    got = connections.secret_required(
        case["provider"], case["before"], case["after"],
        has_secret=case["has_secret"], secret_sent=case["secret_sent"],
        clear_secret=case["clear_secret"],
    )
    assert got is case["required"]
