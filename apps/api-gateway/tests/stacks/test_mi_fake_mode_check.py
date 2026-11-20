"""분석 연동이 꺼진 서버의 MI 점검이 외부에 접속하지 않는지 확인하는 테스트.

연동이 꺼진 서버는 가짜 클라이언트가 분석 요청을 처리한다.
연결 점검이 실제 주소에 접속하면 개발용 주소에서 실패가 기록되고, 그 연결을 분석 대상으로 고를 수 없다.
분석 대상 점검도 분석 대상을 고르지 않은 상태를 실패로 표시하지 않는다.
연동이 켜진 서버의 점검은 그대로 실제 클라이언트로 접속한다.
"""
from __future__ import annotations

import uuid

import pytest
from api_gateway.config import settings
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import naver_client
from httpx import AsyncClient
from tests._helpers import new_project


def _h(token: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    """실제 MI 클라이언트의 워크스페이스 조회를 기록하고 접속 실패로 끝낸다."""
    seen: list[str] = []

    async def _ws(self):
        """조회 시도를 기록하고 접속 실패를 흉내 낸다."""
        seen.append(self.workspace)
        raise RuntimeError("MAIU GET /workspaces/ws → 연결 실패")

    monkeypatch.setattr(naver_client.NaverMaiuClient, "get_workspace", _ws)
    return seen


async def _mi_connection(client: AsyncClient, token: str) -> dict:
    """개발용 주소를 가리키는 MI 연결을 만들고 저장 응답(점검 결과 포함)을 돌려준다."""
    r = await client.post("/connections", headers=_h(token), json={
        "name": f"mi-{uuid.uuid4().hex[:8]}", "provider": "maiu",
        "config": {"base_url": "https://mi.example", "workspace_name": "ws-dev"},
        "access_key": "dev-key", "secret_key": "dev-secret",
    })
    assert r.status_code == 201, r.text
    return r.json()


async def test_connection_check_skips_network_when_disabled(
    client: AsyncClient, admin_token: str, calls: list[str], monkeypatch,
):
    """연동이 꺼져 있으면 연결 점검이 접속하지 않고 정상으로 끝나 분석 대상으로 고를 수 있다."""
    monkeypatch.setattr(settings, "maiu_enabled", False)
    saved = await _mi_connection(client, admin_token)
    cid = saved["id"]

    assert saved["check"]["status"] == "ok"
    assert saved["check"]["detail"] == maiu_client.FAKE_MODE_DETAIL
    assert calls == [], "연동이 꺼진 서버가 실제 MI에 접속했다"

    pid = await new_project(client, admin_token, "fake-mode")
    choices = (await client.get(f"/projects/{pid}/analysis-choices",
                                headers=_h(admin_token))).json()
    row = next(c for c in choices["connections"] if c["id"] == cid)
    assert row["selectable"] is True and row["last_status"] == "ok"


async def test_connection_check_still_connects_when_enabled(
    client: AsyncClient, admin_token: str, calls: list[str], monkeypatch,
):
    """연동이 켜져 있으면 연결 점검이 지금처럼 실제 클라이언트로 접속하고 실패를 기록한다."""
    monkeypatch.setattr(settings, "maiu_enabled", True)
    saved = await _mi_connection(client, admin_token)

    assert saved["check"]["status"] == "fail"
    assert saved["last_status"] == "fail"
    assert calls and calls[-1] == "ws-dev"


async def test_analysis_check_without_target_is_ok_when_disabled(
    client: AsyncClient, admin_token: str, calls: list[str], monkeypatch,
):
    """연동이 꺼진 서버에서 분석 대상을 고르지 않았으면 점검이 연동이 꺼져 있다는 정상 항목 하나를 돌려준다."""
    monkeypatch.setattr(settings, "maiu_enabled", False)
    pid = await new_project(client, admin_token, "fake-check")

    r = await client.post(f"/projects/{pid}/analysis-stack/check", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == 0
    assert [(c["key"], c["status"], c["detail"]) for c in body["checks"]] == [
        ("maiu_enabled", "ok", maiu_client.FAKE_MODE_DETAIL),
    ]
    assert calls == []


async def test_analysis_check_without_target_fails_when_enabled(
    client: AsyncClient, admin_token: str, calls: list[str], monkeypatch,
):
    """연동이 켜진 서버에서는 분석 대상을 고르지 않은 상태가 지금처럼 첫 항목 실패다.

    연동이 켜진 서버는 키가 있는 MI 연결이 하나도 없으면 프로젝트 생성을 400
    `analysis_preset_unavailable`로 거절한다. 새 설치 DB에는 시드 연결이 없으므로 이 테스트가 연결을 먼저 만든다.
    앞선 테스트가 만든 연결에 기대면 실행 순서와 병렬 배분에 따라 생성이 실패한다.
    """
    monkeypatch.setattr(settings, "maiu_enabled", True)
    await _mi_connection(client, admin_token)
    # 연결 저장 때 도는 점검의 조회는 세지 않는다. 아래에서 보는 것은 분석 대상 점검이다.
    calls.clear()
    pid = await new_project(client, admin_token, "real-check")

    r = await client.post(f"/projects/{pid}/analysis-stack/check", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks[0]["key"] == "maiu_workspace" and checks[0]["status"] == "fail"
    assert calls == []
