"""프로젝트 설정 수정 API(PATCH /projects/{id}) 테스트."""
from api_gateway.models import Project
from api_gateway.services.org.project import _CLEARABLE
from httpx import AsyncClient
from reborn_contracts import ProjectUpdateRequest
from tests._helpers import add_member, new_project


async def test_update_project_fields(client: AsyncClient, admin_token: str):
    """name과 target_hours를 부분 수정하면 응답에 반영되는지 확인한다."""
    pid = await new_project(client, admin_token, "proj-upd")
    r = await client.patch(
        f"/projects/{pid}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "수정된 프로젝트", "target_hours": 1250},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "수정된 프로젝트"
    assert body["target_hours"] == 1250


async def test_update_project_clears_description_with_empty_string(
    client: AsyncClient, admin_token: str
):
    """빈 문자열을 보내면 description이 빈 문자열로 저장되는지 확인한다.

    description은 `_CLEARABLE`에 있으므로 null을 보내도 비워진다.
    """
    pid = await new_project(client, admin_token, "proj-desc")
    await client.patch(
        f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": "초기 설명"},
    )
    r = await client.patch(
        f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": ""},
    )
    assert r.status_code == 200
    assert r.json()["description"] == ""


async def test_update_project_403_for_annotator(client: AsyncClient, admin_token: str):
    """annotator의 프로젝트 설정 수정이 403인지 확인한다."""
    pid = await new_project(client, admin_token, "proj-upd-perm")
    _, annot_tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.patch(
        f"/projects/{pid}",
        headers={"Authorization": f"Bearer {annot_tok}"},
        json={"name": "남이 못 바꿈"},
    )
    assert r.status_code == 403


async def test_update_project_404_for_unknown(client: AsyncClient, admin_token: str):
    """없는 project_id를 수정하면 404인지 확인한다."""
    missing = "00000000-0000-0000-0000-000000000000"
    r = await client.patch(
        f"/projects/{missing}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "x"},
    )
    assert r.status_code == 404


# 저장 경계: 설정 화면의 입력값이 경계에 걸리는 경우


async def test_nullable_fields_clear_with_null(client: AsyncClient, admin_token: str):
    """목표 시간과 계약 마감일이 null을 보내면 지워지는지 확인한다.

    서버가 `null`을 보내지 않은 값으로 읽어 건너뛰면 화면은 입력란을 비우고 저장 성공을 띄운다.
    그러나 다시 열면 이전 값이 그대로 있다.
    """
    pid = await new_project(client, admin_token, "edge-clear")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/projects/{pid}", headers=h,
                           json={"target_hours": 100, "contract_deadline": "2026-12-31"})
    assert r.status_code == 200 and r.json()["target_hours"] == 100, r.text

    r = await client.patch(f"/projects/{pid}", headers=h,
                           json={"target_hours": None, "contract_deadline": None})
    assert r.status_code == 200, r.text
    got = (await client.get(f"/projects/{pid}", headers=h)).json()
    assert (got["target_hours"], got["contract_deadline"]) == (None, None), got


async def test_required_knob_null_is_422_not_silent(client: AsyncClient, admin_token: str):
    """비울 수 없는 필드에 null이 오면 422인지 확인한다. 조용히 건너뛰면 저장 성공 안내가 사실과 달라진다.

    화면은 비운 입력란을 보내지 않지만, 서버도 잘못된 성공 응답을 만들지 않아야 한다.
    """
    pid = await new_project(client, admin_token, "edge-null")
    h = {"Authorization": f"Bearer {admin_token}"}
    before = (await client.get(f"/projects/{pid}", headers=h)).json()["deident_enabled"]
    r = await client.patch(f"/projects/{pid}", headers=h, json={"deident_enabled": None})
    assert r.status_code == 422, r.text
    assert [e["loc"] for e in r.json()["detail"]] == [["body", "deident_enabled"]]
    got = (await client.get(f"/projects/{pid}", headers=h)).json()
    assert got["deident_enabled"] == before


def test_clearable_fields_are_the_nullable_columns():
    """비울 수 있는 필드(`_CLEARABLE`)가 수정 요청 필드 중 NULL을 받는 Project 컬럼과 같은지 확인한다.

    컬럼의 nullable을 바꾸면 이 목록도 함께 바꿔야 한다.
    어긋나면 비울 수 있는 필드를 비웠을 때 422가 나거나, 비울 수 없는 필드에 NULL이 들어가 DB 오류가 난다.
    화면 쪽 대응 테스트는 `apps/web/src/features/projects/lib/settings_numbers.test.ts`의 null 허용 대조다.
    """
    update_keys = set(ProjectUpdateRequest.model_fields)
    nullable = {c.name for c in Project.__table__.columns if c.nullable and c.name in update_keys}
    assert nullable == _CLEARABLE


async def test_project_status_has_one_finished_value(client: AsyncClient, admin_token: str):
    """끝난 프로젝트 상태가 `completed` 하나인지 확인한다.

    `archived`는 `completed`와 같게 동작해서 수정 요청은 422로 거절하고,
    DB에 남은 `archived` 행은 응답에서 `completed`로 읽힌다.
    """
    from uuid import UUID

    from api_gateway.database import SessionLocal

    pid = await new_project(client, admin_token, "proj-status")
    h = {"Authorization": f"Bearer {admin_token}"}
    r = await client.patch(f"/projects/{pid}", headers=h, json={"project_status": "archived"})
    assert r.status_code == 422, r.text
    r = await client.patch(f"/projects/{pid}", headers=h, json={"project_status": "completed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    async with SessionLocal() as s:
        (await s.get(Project, UUID(pid))).status = "archived"
        await s.commit()
    assert (await client.get(f"/projects/{pid}", headers=h)).json()["status"] == "completed"
    listed = {p["project_id"]: p for p in (await client.get("/projects", headers=h)).json()}
    assert listed[pid]["status"] == "completed"
