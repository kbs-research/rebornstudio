"""납품 패키지 다운로드 링크를 그 프로젝트 관리자만 받는지 보는 테스트.

다운로드 링크는 라벨링 결과물 전체를 내려받는 presigned URL이다. 검수자·라벨러나 다른 프로젝트의
관리자가 받으면 권한이 없는 사람에게 결과물이 나간다.
"""
from api_gateway.database import SessionLocal
from api_gateway.services.exports.export_service import process_pending
from httpx import AsyncClient
from tests._helpers import add_member, new_project


async def test_download_is_project_admin_only(client: AsyncClient, admin_token: str):
    """reviewer·annotator·다른 프로젝트 project_admin은 403이고 그 프로젝트 관리자는 200인지 확인한다."""
    pid = await new_project(client, admin_token, "dl-authz")
    other = await new_project(client, admin_token, "dl-other")
    eid = (await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"},
    )).json()["export_id"]
    async with SessionLocal() as s:
        await process_pending(s, limit=10)

    _, reviewer = await add_member(client, admin_token, pid, "reviewer")
    _, annotator = await add_member(client, admin_token, pid, "annotator")
    _, other_admin = await add_member(client, admin_token, other, "project_admin")
    _, own_admin = await add_member(client, admin_token, pid, "project_admin")
    for who, tok in (
        ("reviewer", reviewer), ("annotator", annotator), ("other_admin", other_admin),
    ):
        r = await client.get(f"/exports/{eid}/download", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403, (who, r.text)
        assert r.json()["detail"] == "not_project_admin", who
    r = await client.get(f"/exports/{eid}/download", headers={"Authorization": f"Bearer {own_admin}"})
    assert r.status_code == 200, r.text
