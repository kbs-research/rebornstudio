"""프로젝트 응답의 설정 값을 역할별로 가리는지 보는 테스트.

`settings`에는 품질검수 프롬프트와 비속어 사전·분석 스택 식별자가 있다.
관리자·최고관리자만 그대로 받고, 나머지 역할은 공개 키만 받는다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Project
from httpx import AsyncClient
from tests._helpers import add_member, new_project

#: 시드하는 내부 키. 관리자 응답에는 모두 있어야 한다.
_SEEDED = {
    "_daily_stats": {"2026-10-01": {"videos_by_status": {"rejected": 1, "pending": 9}}},
    "stack": {"maiu_workspace": "ws-secret", "maiu_project_id": "mi-secret"},
    "transform_options": {"paragraph_count": 2},
    "caption_qc": {
        "enabled": True, "adapter_key": "llm", "connection_name": "qc-conn",
        "prompts": {"grammar": "비공개 프롬프트"}, "glossary": {"AI": "에이아이"},
        "profanity": ["비속어표본"],
    },
}

#: 그 밖의 역할이 받는 `settings` 키. 서버 `_SETTINGS_PUBLIC_KEYS`와 같아야 한다.
_PUBLIC: set[str] = set()


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _both(client: AsyncClient, tok: str, pid: str) -> list[dict]:
    """목록(`GET /projects`)의 그 프로젝트 행과 상세(`GET /projects/{id}`) 응답을 돌려준다."""
    rows = await client.get("/projects", headers=_h(tok))
    assert rows.status_code == 200, rows.text
    row = next(p for p in rows.json() if p["project_id"] == pid)
    one = await client.get(f"/projects/{pid}", headers=_h(tok))
    assert one.status_code == 200, one.text
    return [row, one.json()]


async def test_project_settings_redacted_by_role(client: AsyncClient, admin_token: str):
    """관리자·최고관리자는 설정 전부를, 그 밖의 역할은 공개 키와 품질검수 켜짐 여부만 받는지 확인한다."""
    pid = await new_project(client, admin_token, "setrole")
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        p.settings = {**(p.settings or {}), **_SEEDED}
        await s.commit()

    toks = {"super_admin": admin_token}
    for role in ("project_admin", "reviewer", "annotator"):
        toks[role] = (await add_member(client, admin_token, pid, role))[1]

    for role in ("super_admin", "project_admin"):
        for dto in await _both(client, toks[role], pid):
            assert set(_SEEDED) <= set(dto["settings"]), (role, sorted(dto["settings"]))
            assert dto["caption_qc"]["profanity"] == ["비속어표본"], role
            assert dto["caption_qc"]["prompts"] == {"grammar": "비공개 프롬프트"}, role
            assert dto["caption_qc"]["connection_name"] == "qc-conn", role

    for role in ("reviewer", "annotator"):
        for dto in await _both(client, toks[role], pid):
            assert set(dto["settings"]) == _PUBLIC, (role, sorted(dto["settings"]))
            qc = dto["caption_qc"]
            # 켜짐 여부는 완료 화면이 쓴다. 세부 설정은 비운다.
            assert qc["enabled"] is True, role
            assert qc["profanity"] == [] and qc["prompts"] == {} and qc["glossary"] == {}, role
            assert qc["connection_name"] is None, role
            # 화면이 쓰는 최상위 필드는 그대로 남는다.
            assert dto["my_role"] == role
            assert dto["reject_reasons"], role
            assert dto["clip_min_seconds"], role
