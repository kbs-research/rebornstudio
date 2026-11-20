"""저장소 등록 경로가 권한을 저장소 상태보다 먼저 확인하는지 보는 테스트.

권한 확인 전에 저장소 대상을 읽으면 관리자가 아닌 멤버가 409 응답으로 프로젝트 저장소 설정
여부를 알아내고, 연결을 읽는 DB 조회와 자격증명 적재를 일으킨다.
"""
import uuid

from httpx import AsyncClient
from tests._helpers import add_member, new_project


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def test_non_admin_gets_403_before_storage_state(client: AsyncClient, admin_token: str):
    """저장소 대상이 없는 프로젝트에서도 관리자가 아닌 멤버의 등록 요청은 403인지 확인한다."""
    pid = await new_project(client, admin_token, "reg-perm", storage=False)
    _uid, tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(f"/projects/{pid}/storage/register", headers=_h(tok),
                          json={"keys": ["sample/a.mp4"], "copyright_policy": "pre_cleared"})
    assert r.status_code == 403, r.text
    r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=_h(tok),
                          json={"prefix": "sample/", "copyright_policy": "pre_cleared",
                                "upload_batch_id": str(uuid.uuid4())})
    assert r.status_code == 403, r.text
