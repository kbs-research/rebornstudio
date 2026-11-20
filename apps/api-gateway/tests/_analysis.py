"""테스트 MI 연결. 분석 대상을 저장하는 테스트가 고를 MI 연결을 만든다.

분석 대상은 연결·워크스페이스·MI 프로젝트를 함께 골라야 저장된다. 연결은 점검 결과가 정상이어야 고를 수 있다.
연결을 저장하면 같은 요청에서 점검이 돈다. 그래서 점검의 워크스페이스 조회만 가짜로 바꾸고 나머지 점검 경로는
실제 코드로 돌린다. 외부(NCP)는 호출하지 않는다.
"""
from __future__ import annotations

import uuid

import pytest
from api_gateway.services.maiu import naver_client
from httpx import AsyncClient
from tests import _storage


async def mi_connection(
    client: AsyncClient, token: str, *, access: str = "conn-key", workspace: str = "ws-conn",
    base_url: str = "https://mi.example",
) -> str:
    """점검이 정상인 MI 연결을 만들고 id를 돌려준다."""
    async def _ws(self):
        """연동 저장소가 테스트 저장소인 워크스페이스를 돌려준다."""
        return {"result": {"workspaceName": self.workspace, "bucketName": _storage.BUCKET}}

    headers = {"Authorization": f"Bearer {token}"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(naver_client.NaverMaiuClient, "get_workspace", _ws)
        r = await client.post("/connections", headers=headers, json={
            "name": f"mi-{uuid.uuid4().hex[:8]}", "provider": "maiu",
            "config": {"base_url": base_url, "workspace_name": workspace},
            "access_key": access, "secret_key": "conn-secret",
        })
        assert r.status_code == 201, r.text
        assert r.json()["check"]["status"] == "ok", r.json()
    return r.json()["id"]
