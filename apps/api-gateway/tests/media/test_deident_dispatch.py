"""비식별화 잡 투입과 원격 서버 연결 제거 테스트.

비식별화 모델은 워커 안에서 돌므로 잡에는 원격 서버 설정이 없다. 네 가지를 확인한다.

  1. 잡에는 `remote`·`staged`가 없고 얼굴 좌표 JSON의 업로드 주소가 늘 실린다.
  2. 동시 처리 상한은 전역 하나(`deident_max_inflight`)이고 앞 영상이 도는 동안 다음 영상은 기다린다.
  3. 서비스 `deid` 연결과 프로젝트의 비식별화 서버 선택은 API가 422로 거절한다.
  4. 마이그레이션 0105가 서비스 `deid` 연결 행과 프로젝트 설정의 `deident` 키를 지운다.
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from api_gateway.database import SessionLocal
from api_gateway.models import Connection, Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project, upload_video

_MIGRATION = (Path(__file__).resolve().parents[2]
              / "alembic" / "versions" / "0105_drop_deid_connections.py")


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


@pytest.fixture
def puts(monkeypatch) -> list[dict]:
    """`build_jobs`가 요청한 presigned PUT의 인자를 모은다. 실제 저장소를 부르지 않는다."""
    calls: list[dict] = []

    def _fake(**kw):
        """인자를 기록하고 키가 드러나는 가짜 URL을 돌려준다."""
        calls.append(kw)
        return f"https://store/{kw['key']}?sig=x"

    monkeypatch.setattr("api_gateway.services.media.deident.storage.presign_put_for", _fake)
    return calls


async def _pending_video(client: AsyncClient, token: str, name: str) -> uuid.UUID:
    """비식별화를 켠 프로젝트에 영상을 올려 비식별화 대기로 둔다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = True
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
    return vid


async def _jobs() -> list[dict]:
    """비식별화 잡을 한 번 만들어 돌려준다."""
    async with SessionLocal() as s:
        return await deident_service.build_jobs(s)


async def _status(vid: uuid.UUID) -> str:
    """영상의 비식별화 상태를 읽는다."""
    async with SessionLocal() as s:
        return (await s.get(Video, vid)).deident_status


async def test_job_has_no_remote_and_always_carries_the_coordinates_url(
    client: AsyncClient, admin_token: str, puts: list[dict],
):
    """잡에 `remote`·`staged`가 없고 좌표 JSON의 presigned PUT이 JSON 형식으로 실리는지 확인한다."""
    vid = await _pending_video(client, admin_token, "deidjob")
    job = next(j for j in await _jobs() if j["video_id"] == str(vid))
    assert "remote" not in job and "staged" not in job
    key = deident_service.coordinates_staging_key(vid, job["token"])
    assert job["coordinates_put_url"] == f"https://store/{key}?sig=x"
    put = next(c for c in puts if c["key"] == key)
    assert put["content_type"] == "application/json"


async def test_global_limit_holds_the_next_video(
    client: AsyncClient, admin_token: str, puts: list[dict], monkeypatch,
):
    """상한 1이면 먼저 올라온 영상만 보내고, 상한을 2로 올리면 다음 영상을 보내는지 확인한다."""
    from api_gateway.config import settings
    monkeypatch.setattr(settings, "deident_max_inflight", 1)
    first = await _pending_video(client, admin_token, "deidcap-a")
    second = await _pending_video(client, admin_token, "deidcap-b")
    sent = [j["video_id"] for j in await _jobs()]
    assert str(first) in sent and str(second) not in sent
    assert await _status(second) == "pending"
    # 앞 영상이 도는 동안에는 다음 회차에도 보내지 않는다.
    assert str(second) not in [j["video_id"] for j in await _jobs()]
    monkeypatch.setattr(settings, "deident_max_inflight", 2)
    assert str(second) in [j["video_id"] for j in await _jobs()]


async def test_deid_connection_and_project_server_choice_are_rejected(
    client: AsyncClient, admin_token: str,
):
    """서비스 `deid` 연결 생성과 프로젝트의 비식별화 서버 선택이 422로 거절되는지 확인한다."""
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": f"deid-{uuid.uuid4().hex[:8]}", "provider": "deid",
        "config": {"base_url": "https://deid.example"},
    })
    assert r.status_code == 422, r.text
    pid = await new_project(client, admin_token, "deidchoice")
    _pa, patok = await add_member(client, admin_token, pid, "project_admin")
    r = await client.patch(f"/projects/{pid}", headers=_h(patok),
                           json={"deident_connection_name": "deid-gpu-1"})
    assert r.status_code == 422, r.text
    dto = (await client.get(f"/projects/{pid}", headers=_h(patok))).json()
    assert "deident_connection_name" not in dto


def _upgrade_0105(sync_session) -> None:
    """마이그레이션 0105의 `upgrade`를 세션의 연결에서 실행한다."""
    spec = importlib.util.spec_from_file_location("m0105_drop_deid_connections", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ctx = MigrationContext.configure(sync_session.connection())
    with Operations.context(ctx):
        mod.upgrade()


async def test_migration_0105_drops_deid_rows_and_the_settings_key(
    client: AsyncClient, admin_token: str,
):
    """0105가 서비스 `deid` 연결과 `settings.deident`만 지우고 다른 연결과 설정 키는 남기는지 확인한다."""
    pid = uuid.UUID(await new_project(client, admin_token, "deidmig"))
    deid_name, keep_name = f"deid-{uuid.uuid4().hex[:8]}", f"qc-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        p = await s.get(Project, pid)
        p.settings = {**(p.settings or {}), "deident": {"connection_name": deid_name}}
        s.add_all([
            Connection(org_id=p.org_id, name=deid_name, provider="deid",
                       config={"base_url": "https://deid.example"}),
            Connection(org_id=p.org_id, name=keep_name, provider="anthropic",
                       config={"model": "m"}),
        ])
        await s.commit()
        org_id = p.org_id
    async with SessionLocal() as s:
        await s.run_sync(_upgrade_0105)
        await s.commit()
    async with SessionLocal() as s:
        names = set((await s.scalars(
            select(Connection.name).where(Connection.org_id == org_id))).all())
        settings = (await s.get(Project, pid)).settings
    assert deid_name not in names and keep_name in names
    assert "deident" not in settings and "analysis_preset" in settings
