"""프로젝트별 분석 대상(MI 연결·워크스페이스·MI 프로젝트) 테스트.

보장하는 것은 다음과 같다.
- 프로젝트가 고른 MI 프로젝트로 에셋을 등록하고 그 값을 잡에 기록한다. 폴링은 잡에 기록한 값으로 한다.
- MI 방식 프로젝트는 분석 대상(연결·워크스페이스·MI 프로젝트)을 기록해야 영상 등록·분석 요청을 받는다
  (분석 연동이 켜진 서버). 채워 주는 기본값은 없고, 이전 `maiu_project_id`도 쓰지 않는다.
- 첫 영상이 생기면 바꿀 수 없다(409). 연동 저장소가 다르면 422, MI를 쓰지 않는 방식이면 422,
  프로젝트가 고른 연결은 지울 수 없다(409).
- 점검은 워크스페이스·저장소·MI 프로젝트·에셋 권한을 확인하고, 등록 직전 점검이 실패하면 사유와 함께 409다.
  주기 점검은 상태가 바뀐 때만 전이를 기록한다.
- 선택지 API는 외부 조회가 실패해도 500이 아니라 사유를 싣는다.

외부(NCP)는 부르지 않는다. 실제 클라이언트 경로는 `NaverMaiuClient._request`를 기록용 가짜로 바꿔
경로 문자열을 직접 확인한다. 나머지는 가짜 클라이언트(`FakeMaiuClient`)를 주입한다.
"""
import uuid
from uuid import UUID

import pytest
import pytest_asyncio
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent, Project
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import naver_client, provisioning
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.reconciler import stages
from api_gateway.services.ops.diagnostics import Check
from api_gateway.services.stacks import project_stack
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests._analysis import mi_connection
from tests._helpers import new_project, upload_video


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _reset_clients():
    """테스트마다 클라이언트 캐시·연결 메모리·점검 캐시를 처음으로 돌린다."""
    maiu_client.set_client(None)
    await _storage.reset_memory()
    project_stack._cache.clear()
    yield
    maiu_client.set_client(None)
    await _storage.reset_memory()
    project_stack._cache.clear()


@pytest_asyncio.fixture(loop_scope="session")
async def mi(client: AsyncClient, admin_token: str) -> str:
    """점검이 정상인 MI 연결을 만들고 id를 돌려준다(액세스 키 `conn-key`)."""
    return await mi_connection(client, admin_token)


@pytest_asyncio.fixture(loop_scope="session")
async def fake():
    """가짜 MI를 주입한다. 워크스페이스에는 MI 프로젝트 두 개(id 1·2)가 있다.

    워크스페이스 연동 저장소는 테스트 저장소다. 테스트 프로젝트는 그 저장소를 저장소 대상으로 고른다.
    """
    c = FakeMaiuClient()
    c.linked_bucket = _storage.BUCKET
    await c.create_project("가")
    await c.create_project("나")
    maiu_client.set_client(c)
    yield c
    maiu_client.set_client(None)


class _Recorder:
    """`NaverMaiuClient._request`를 대신해 부른 경로와 키를 기록하고 정상 응답을 준다."""

    def __init__(self) -> None:
        """기록 목록을 비운다. `deny_assets`면 에셋 목록 조회를 403으로 거절한다."""
        self.calls: list[tuple[str, str, str]] = []
        self.deny_assets = False

    async def __call__(self, cli, method: str, path: str) -> dict:
        """경로에 맞는 최소 응답을 돌려준다.

        실제 MI처럼 목록 조회(워크스페이스·프로젝트·에셋)에 `pageNum`이 없으면
        요청 파라미터 오류(400, 코드 10001)로 거절한다.
        """
        self.calls.append((method, path, cli.access_key))
        base, _, query = path.partition("?")
        listing = method == "GET" and (
            base == "/api/v1/workspaces" or base.endswith(("/projects", "/assets", "/video-assets")))
        if listing and "pageNum=" not in query:
            raise RuntimeError(
                f'MAIU GET {path} → 400 {{"code":"10001","message":"유효하지 않은 요청입니다"}}')
        if base.endswith("/import/object-storage"):
            return {"result": {"assetId": f"a-{uuid.uuid4().hex[:8]}"}}
        if base.endswith("/import-status"):
            return {"result": {"uploadStatus": "IN_PROGRESS"}}
        if base.endswith(("/assets", "/video-assets")):
            if self.deny_assets:
                raise RuntimeError(f"MAIU GET {path} → 403 forbidden")
            return {"result": {"assets": []}}
        if base == "/api/v1/workspaces":
            return {"result": {"content": [{"workspaceName": "ws-a"}, {"workspaceName": "ws-b"}],
                               "totalCount": 2}}
        if base.endswith("/projects"):
            return {"result": {"projects": [{"projectId": "p-42", "name": "가"},
                                            {"projectId": "p-77", "name": "나"},
                                            {"projectId": "p-90", "name": "다"}]}}
        if base.startswith("/api/v1/workspaces/") and base.count("/") == 4:
            return {"result": {"workspaceName": cli.workspace, "bucketName": _storage.BUCKET}}
        return {"result": {}}

    def paths(self, suffix: str) -> list[str]:
        """끝이 `suffix`인 호출 경로를 고른다."""
        return [p for _, p, _ in self.calls if p.split("?")[0].endswith(suffix)]


@pytest.fixture
def rec(monkeypatch):
    """실제 클라이언트의 HTTP만 기록용으로 바꾼다. 분석 연동은 `_enable`이 켠다."""
    r = _Recorder()

    async def _request(self, method, path, *, json=None):
        """기록용 가짜로 넘긴다."""
        return await r(self, method, path)

    monkeypatch.setattr(naver_client.NaverMaiuClient, "_request", _request)
    return r


def _enable(monkeypatch, *, keep_fake: bool = False) -> None:
    """분석 연동을 켠다. `keep_fake`면 주입한 가짜를 그대로 쓴다."""
    monkeypatch.setattr(settings, "maiu_enabled", True)
    if not keep_fake:
        maiu_client.set_client(None)


def _storage_block() -> dict:
    """테스트 저장소 연결과 버킷을 고른 저장소 대상. 분석 대상과 같은 `stack` 블록에 둔다."""
    return {"storage_connection_id": _storage.connection_id(), "bucket": _storage.BUCKET}


async def _write_block(pid: str, block: dict | None, **extra) -> None:
    """프로젝트 설정의 `stack` 블록을 DB에 바로 쓴다. 저장 규칙(잠금)을 거치지 않는 상황을 만든다.

    블록에 저장소 대상이 없으면 테스트 저장소 연결과 버킷을 함께 쓴다.
    """
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        new = {**(p.settings or {}), **extra}
        if block is not None:
            new["stack"] = {**_storage_block(), **block}
        p.settings = new
        await s.commit()


async def _settings(pid: str) -> dict:
    """프로젝트 설정을 읽는다."""
    async with SessionLocal() as s:
        return dict((await s.get(Project, UUID(pid))).settings or {})


async def _job(pid: str) -> AnalysisJob:
    """프로젝트의 분석 잡 하나를 읽는다."""
    async with SessionLocal() as s:
        return (await s.scalars(
            select(AnalysisJob).where(AnalysisJob.project_id == UUID(pid)))).one()


# 라우팅


async def test_chosen_mi_project_receives_the_asset(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """프로젝트가 고른 연결·워크스페이스·MI 프로젝트 경로로 에셋을 등록하고 그 값을 잡에 남기는지 확인한다."""
    cid = mi
    _enable(monkeypatch)
    pid = await new_project(client, admin_token, "route")
    await _write_block(pid, {"analysis_connection_id": cid, "maiu_workspace": "ws-a",
                             "maiu_project_id": "p-42"})
    vid = await upload_video(client, admin_token, pid, probed=True)

    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(admin_token),
                          json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    calls = [(p, a) for m, p, a in rec.calls if p.endswith("/import/object-storage")]
    assert calls == [("/api/v1/workspaces/ws-a/projects/p-42/assets/import/object-storage",
                      "conn-key")], rec.calls
    assert (await _job(pid)).payload["maiu"] == {
        "connection_id": cid, "workspace": "ws-a", "project_id": "p-42"}


async def test_unpinned_project_with_videos_is_not_given_a_default(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """영상이 있는데 분석 대상이 없는 프로젝트의 등록·분석 요청이 409이고 기본값으로 채우지 않는지 확인한다.

    설정 최상위의 이전 `maiu_project_id`도 쓰지 않는다.
    기록은 이행(마이그레이션 0091)이나 운영 스크립트가 사람이 고른 연결로 채운다.
    """
    pid = await new_project(client, admin_token, "legacy")
    await _write_block(pid, None, maiu_project_id="legacy-9")
    vid = await upload_video(client, admin_token, pid, probed=True)   # 분석 연동이 꺼진 동안 등록한 영상
    _enable(monkeypatch)
    h = _h(admin_token)
    assert (await client.get(f"/projects/{pid}", headers=h)).json()["analysis_stack_missing"]
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=h, json={"video_ids": [vid]})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    assert rec.paths("/import/object-storage") == [], "고르지 않은 곳으로 분석을 요청했다"
    assert "analysis_connection_id" not in (await _settings(pid)).get("stack", {})


async def test_polling_follows_the_job_not_the_current_setting(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """요청 뒤에 프로젝트 선택이 바뀌어도 폴링이 잡에 기록한 MI 프로젝트를 쓰는지 확인한다.

    저장은 잠금이 막지만, DB를 직접 고친 경우에도 같은 곳을 조회해야 404를 받지 않는다.
    """
    _enable(monkeypatch)
    pid = await new_project(client, admin_token, "pin")
    await _write_block(pid, {"analysis_connection_id": mi, "maiu_workspace": "ws-a",
                             "maiu_project_id": "p-42"})
    vid = await upload_video(client, admin_token, pid, probed=True)
    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(admin_token),
                          json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    await _write_block(pid, {"analysis_connection_id": mi, "maiu_workspace": "ws-b",
                             "maiu_project_id": "p-77"})
    async with SessionLocal() as s:
        job = await s.get(AnalysisJob, (await _job(pid)).id)
        job.stage = "registering"
        await s.commit()

    async def _mine(db, *, stage):
        """이 프로젝트의 잡만 고른다. 다른 테스트의 잡을 건드리지 않는다."""
        return (await db.scalars(
            select(AnalysisJob).where(AnalysisJob.project_id == UUID(pid),
                                      AnalysisJob.stage == stage).with_for_update()
        )).all()

    monkeypatch.setattr(stages, "_lock_batch", _mine)
    rec.calls.clear()
    async with SessionLocal() as s:
        await stages.bulk_poll_registrations(s)
    polled = rec.paths("/import-status")
    assert polled and all("/workspaces/ws-a/projects/p-42/" in p for p in polled), polled


async def test_project_connection_drives_readiness(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """분석 요청 가능 여부를 프로젝트가 고른 연결로만 판단하고 고르지 않은 프로젝트는 409로 멈추는지 확인한다.

    고른 연결의 키를 지우면 그 프로젝트의 분석 요청도 막힌다. 다른 연결로 넘어가지 않는다.
    """
    _enable(monkeypatch)
    chosen = await new_project(client, admin_token, "ready-a")
    plain = await new_project(client, admin_token, "ready-b")
    await _write_block(chosen, {"analysis_connection_id": mi, "maiu_workspace": "ws-a",
                                "maiu_project_id": "p-42"})
    h = _h(admin_token)
    a = (await client.get(f"/projects/{chosen}", headers=h)).json()
    b = (await client.get(f"/projects/{plain}", headers=h)).json()
    assert a["analysis_ready"] is True and a["analysis_stack_missing"] is False
    assert b["analysis_stack_missing"] is True
    r = await client.post(f"/projects/{plain}/maiu/dispatch", headers=h, json={"video_ids": []})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    r = await client.post(f"/projects/{chosen}/maiu/dispatch", headers=h, json={"video_ids": []})
    assert r.status_code == 200, r.text

    r = await client.patch(f"/connections/{mi}", headers=h, json={"clear_secret": True})
    assert r.status_code == 200, r.text
    a = (await client.get(f"/projects/{chosen}", headers=h)).json()
    assert a["analysis_ready"] is False, "키가 없는 연결로 분석할 수 있다고 했다"
    r = await client.post(f"/projects/{chosen}/maiu/dispatch", headers=h, json={"video_ids": []})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_not_configured"


# 기록 필수


async def test_mi_project_needs_a_recorded_target_before_any_registration(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """기록 전에는 등록 세 경로가 모두 409이고 연결까지 골라 저장하면 받으며, 샷 검출 스택은 상관없는지 확인한다.

    선택지는 처음 값을 주지 않는다. 미리 채워 두는 기본 연결이 없다.
    """
    _enable(monkeypatch)
    pid = await new_project(client, admin_token, "unset", storage=False)
    shots = await new_project(client, admin_token, "unset-shots", analysis_preset="ncp-shots",
                              storage=False)
    h = _h(admin_token)
    assert (await client.get(f"/projects/{pid}", headers=h)).json()["analysis_stack_missing"]
    # 저장소 대상을 먼저 확인한다. 저장한 뒤에도 분석 대상이 없으면 막힌다.
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "storage_stack_unset"
    for p in (pid, shots):
        r = await client.put(f"/projects/{p}/storage-stack", headers=h, json={
            "storage_connection_id": await _storage.connection_for(p), "bucket": _storage.BUCKET})
        assert r.status_code == 200, r.text
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    r = await client.post(f"/projects/{pid}/storage/register", headers=h,
                          json={"keys": ["raw/a.mp4"], "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    r = await client.post(f"/projects/{pid}/storage/register-prefix", headers=h, json={
        "prefix": "raw/", "copyright_policy": "pre_cleared",
        "upload_batch_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_unset"
    r = await client.post(f"/projects/{shots}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, "샷 검출 스택은 분석 대상이 필요 없다"

    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=h)).json()
    assert c["required"] is True and c["selected"]["maiu_project_id"] is None
    assert "default" not in c and c["workspaces"] is None, "연결을 고르기 전에 무언가를 채웠다"
    assert mi in [o["id"] for o in c["connections"]]
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h,
                         json={"maiu_workspace": "ws-a", "maiu_project_id": "p-42"})
    assert r.status_code == 422 and r.json()["detail"] == "analysis_stack_incomplete"
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={
        "analysis_connection_id": mi, "maiu_workspace": "ws-a", "maiu_project_id": "p-42"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/projects/{pid}", headers=h)).json()["analysis_stack_missing"] \
        is False
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text


async def test_failed_check_blocks_registration_until_rechecked(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """에셋 권한이 빠지면 등록 직전 점검이 사유와 함께 409로 막고, 고친 뒤 점검하면 바로 풀리는지 확인한다.

    점검 결과는 캐시해서 등록마다 외부를 부르지 않는다.
    """
    _enable(monkeypatch)
    pid = await new_project(client, admin_token, "perm")
    h = _h(admin_token)
    await _write_block(pid, {})
    body = {"analysis_connection_id": mi, "maiu_workspace": "ws-a", "maiu_project_id": "p-42"}
    assert (await client.put(f"/projects/{pid}/analysis-stack", headers=h,
                             json=body)).status_code == 200
    rec.calls.clear()
    for _ in range(2):
        r = await client.post(f"/projects/{pid}/videos", headers=h,
                              json={"title": "t", "copyright_policy": "pre_cleared"})
        assert r.status_code == 201, r.text
    assert rec.paths("/workspaces/ws-a") == [], "저장 때 점검한 결과를 다시 쓰지 않았다"

    rec.deny_assets = True
    report = (await client.post(f"/projects/{pid}/analysis-stack/check", headers=h)).json()
    by = {c["key"]: c for c in report["checks"]}
    assert report["failed"] == 1 and by["maiu_assets"]["status"] == "fail"
    assert by["maiu_bucket"]["status"] == "ok" and by["maiu_project"]["status"] == "ok"
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 409 and r.json()["detail"] == "인증이 거부됐습니다"

    rec.deny_assets = False
    assert (await client.post(f"/projects/{pid}/analysis-stack/check",
                              headers=h)).json()["failed"] == 0
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text


# 저장 규칙


async def test_save_and_clear(client: AsyncClient, admin_token: str, fake, mi):
    """워크스페이스의 MI 프로젝트를 고르면 저장되고 모두 비우면 기록이 지워지며 감사에 남는지 확인한다."""
    pid = await new_project(client, admin_token, "save")
    body = {"analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"}
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json=body)
    assert r.status_code == 200, r.text
    assert r.json() == body
    stored = await _settings(pid)
    assert stored["stack"]["maiu_project_id"] == "1"
    assert stored.get("analysis_preset"), "다른 설정 키가 사라졌다"
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json={})
    assert r.status_code == 200 and r.json()["maiu_project_id"] is None
    from tests._helpers import events
    ev = await events(project_id=pid, event_type="project.updated")
    assert sum("analysis_stack" in (e.get("payload") or {}).get("changed", {}) for e in ev) == 2


async def test_locked_from_the_first_video(client: AsyncClient, admin_token: str, fake, mi):
    """영상이 하나라도 생기면 다른 MI 프로젝트로 바꿀 수 없고 같은 값은 다시 저장할 수 있는지 확인한다."""
    pid = await new_project(client, admin_token, "lock")
    h = _h(admin_token)
    body = {"analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"}
    assert (await client.put(f"/projects/{pid}/analysis-stack", headers=h,
                             json=body)).status_code == 200
    # 업로드를 시작만 한 영상 행도 잠금 근거가 된다.
    r = await client.post(f"/projects/{pid}/videos", headers=h,
                          json={"title": "t", "copyright_policy": "pre_cleared"})
    assert r.status_code == 201, r.text

    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h,
                         json={**body, "maiu_project_id": "2"})
    assert r.status_code == 409 and r.json()["detail"] == "analysis_stack_locked"
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={})
    assert r.status_code == 409, "잠긴 기록을 지울 수 있었다"
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json=body)
    assert r.status_code == 200, "같은 값을 다시 저장하는 것은 막지 않는다"
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=h)).json()
    assert c["locked"] is True and c["video_count"] == 1


async def test_bucket_mismatch_is_422(client: AsyncClient, admin_token: str, fake, mi):
    """워크스페이스 연동 저장소가 이 프로젝트의 저장소와 다르면 저장하지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "bucket")
    fake.linked_bucket = "other-bucket"
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 422 and r.json()["detail"] == "maiu_bucket_mismatch"


async def test_unknown_mi_project_and_half_choice_are_422(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """워크스페이스에 없는 MI 프로젝트와 세 필드 중 하나라도 비운 요청을 막는지 확인한다."""
    pid = await new_project(client, admin_token, "unknown")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "999"})
    assert r.status_code == 422 and r.json()["detail"] == "maiu_project_not_found"
    for half in ({"analysis_connection_id": mi, "maiu_workspace": "fake"},
                 {"maiu_workspace": "fake", "maiu_project_id": "1"}):
        r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token),
                             json=half)
        assert r.status_code == 422 and r.json()["detail"] == "analysis_stack_incomplete", half


async def test_non_mi_preset_is_422(client: AsyncClient, admin_token: str, fake):
    """샷 검출 스택이 MI를 부르지 않으므로 분석 대상을 받지 않고 선택지도 외부를 부르지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "shots", analysis_preset="ncp-shots")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token),
                         json={"maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 422 and r.json()["detail"] == "analysis_stack_unsupported"
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token))).json()
    assert c["supported"] is False and c["workspaces"] is None


async def test_non_mi_preset_has_no_provisioning_or_check(
    client: AsyncClient, admin_token: str, fake,
):
    """샷 검출 프로젝트의 연동 진단·점검이 모두 422이고 외부 서비스 자원을 만들지 않는지 확인한다.

    MI를 쓰지 않는 프로젝트에 분석 대상을 받으면 `stack` 블록이 잘못 기록된다.
    """
    pid = await new_project(client, admin_token, "shots-prov", analysis_preset="ncp-shots")
    for method, path in (("GET", "analysis-provisioning"), ("POST", "analysis-stack/check")):
        r = await client.request(method, f"/projects/{pid}/{path}", headers=_h(admin_token))
        assert (r.status_code, r.json()["detail"]) == (422, "analysis_stack_unsupported"), (
            method, path, r.text)
    assert fake._projects and len(fake._projects) == 2, "가짜 MI에 프로젝트가 새로 생겼다"
    assert "maiu_project_id" not in (await _settings(pid))["stack"]


async def test_chosen_connection_cannot_be_deleted(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """프로젝트가 고른 MI 연결이 사용처에 잡히고 삭제는 409인지 확인한다."""
    cid = mi
    pid = await new_project(client, admin_token, "inuse")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json={
        "analysis_connection_id": cid, "maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 200, r.text
    rows = (await client.get("/connections", headers=_h(admin_token))).json()
    name = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()["name"]
    assert name in next(c for c in rows if c["id"] == cid)["used_by"]
    r = await client.delete(f"/connections/{cid}", headers=_h(admin_token))
    assert r.status_code == 409 and r.json()["detail"] == "connection_in_use"


async def test_unverified_connection_is_422(client: AsyncClient, admin_token: str, fake):
    """점검이 정상이 아닌 MI 연결은 고를 수 없고 선택지에도 고를 수 없다고 싣는지 확인한다.

    자격증명 없이 저장한 연결은 저장 점검이 접속하지 않고 주의로 남는다.
    """
    saved = (await client.post("/connections", headers=_h(admin_token), json={
        "name": f"mi-{uuid.uuid4().hex[:8]}", "provider": "maiu",
        "config": {"workspace_name": "ws"},
    })).json()
    assert saved["last_status"] == "warn", saved
    cid = saved["id"]
    pid = await new_project(client, admin_token, "unverified")
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=_h(admin_token), json={
        "analysis_connection_id": cid, "maiu_workspace": "fake", "maiu_project_id": "1"})
    assert r.status_code == 422 and r.json()["detail"] == "connection_not_verified"
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token))).json()
    assert next(o for o in c["connections"] if o["id"] == cid)["selectable"] is False


# 선택지


async def test_choices_list_workspaces_projects_and_the_provisioned_one(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """선택지가 워크스페이스·연동 저장소·MI 프로젝트를 싣고 이전 키(`maiu_project_id`)가 가리키는 MI 프로젝트를 표시하는지 확인한다."""
    pid = await new_project(client, admin_token, "choices")
    await _write_block(pid, None, maiu_project_id="2")
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["error"] is None
    assert [w["name"] for w in c["workspaces"]] == ["fake"]
    assert c["workspace_bucket"] == c["project_bucket"] == _storage.BUCKET
    assert {p["project_id"]: p["provisioned"] for p in c["projects"]} == {"1": False, "2": True}
    assert c["legacy_maiu_project_id"] == "2"
    assert c["selected"]["maiu_project_id"] is None, "옛 키를 선택으로 읽었다"


async def test_choices_read_paged_lists_from_the_real_client(
    client: AsyncClient, admin_token: str, rec, monkeypatch, mi,
):
    """실제 클라이언트로 선택지를 읽으면 목록 조회가 `pageNum`을 붙여 오류 없이 채워지는지 확인한다.

    MI 목록 API는 `pageNum`이 없으면 10001로 거절한다. 기록용 가짜도 같은 규칙으로 거절한다.
    """
    _enable(monkeypatch)
    pid = await new_project(client, admin_token, "choices-real")
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                          params={"connection_id": mi, "workspace": "ws-a"})).json()
    assert c["error"] is None, c["error"]
    assert [w["name"] for w in c["workspaces"]] == ["ws-a", "ws-b"]
    assert [p["project_id"] for p in c["projects"]] == ["p-42", "p-77", "p-90"]
    assert rec.paths("/api/v1/workspaces")[0].endswith("?pageNum=1&pageSize=100")


async def test_choices_still_list_projects_when_workspace_list_fails(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """워크스페이스 목록을 읽지 못해도 고른 워크스페이스의 MI 프로젝트 목록은 읽는지 확인한다."""
    pid = await new_project(client, admin_token, "choices-partial")

    async def _down():
        """목록만 거절한다."""
        raise RuntimeError("MAIU GET /api/v1/workspaces → 400 10001")

    fake.list_workspaces = _down
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["workspaces"] is None
    assert c["error"].startswith("워크스페이스 목록을 읽지 못했습니다")
    assert c["error_transient"] is False, "요청이 거절된 것을 일시 오류로 읽었다"
    assert [p["project_id"] for p in c["projects"]] == ["1", "2"]


async def test_choices_report_external_failure_without_500(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """분석 서비스가 응답하지 않으면 200과 함께 사유를 싣고 목록은 비우는지 확인한다."""
    pid = await new_project(client, admin_token, "choices-fail")

    async def _down():
        """일시 오류를 흉내 낸다."""
        raise naver_client.MaiuTransientError("MAIU GET /api/v1/workspaces → 503", status=503)

    fake.list_workspaces = _down
    r = await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                         params={"connection_id": mi})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspaces"] is None
    assert body["error"].startswith("워크스페이스 목록을 읽지 못했습니다")
    assert body["error_transient"] is True, "일시 오류를 실패로 읽었다"


async def test_choices_mark_a_throttled_workspace_read_as_transient(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """고른 워크스페이스를 읽다가 요청량 제한에 걸리면 일시 오류로 표시하는지 확인한다.

    연동 상태 진단과 같은 분류다. 화면은 이 값으로 주의 톤과 다시 불러오기를 고른다.
    둘 중 하나라도 일시 오류가 아니면 일시 오류로 표시하지 않는다.
    """
    pid = await new_project(client, admin_token, "choices-throttle")

    async def _throttled():
        """요청량 제한을 흉내 낸다."""
        raise naver_client.MaiuTransientError(
            "MAIU GET /api/v1/workspaces/fake → 429 Throttle Limited", status=429)

    fake.get_workspace = _throttled
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["error"].startswith("워크스페이스를 읽지 못했습니다"), c["error"]
    assert c["error_transient"] is True, c

    async def _refused():
        """목록은 요청이 거절된다."""
        raise RuntimeError("MAIU GET /api/v1/workspaces → 400 10001")

    fake.list_workspaces = _refused
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=_h(admin_token),
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["error_transient"] is False, "거절이 섞였는데 일시 오류로 표시했다"


async def test_saved_project_name_survives_a_throttled_list(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """저장한 MI 프로젝트의 이름이 목록을 읽지 못하는 동안에도 선택지 응답에 남는지 확인한다.

    이름을 기록하지 않으면 요청량 제한 중에 분석 대상 카드가 "project · id 101" 대신 "id 101"만 보인다.
    그래서 저장할 때 점검이 확인한 이름을 함께 기록하고, 선택지는 그 이름을 항상 싣는다.
    """
    pid = await new_project(client, admin_token, "name-save")
    h = _h(admin_token)
    r = await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "2"})
    assert r.status_code == 200, r.text
    assert (await _settings(pid))["stack"]["maiu_project_name"] == "나"

    async def _throttled():
        """요청량 제한을 흉내 낸다."""
        raise naver_client.MaiuTransientError("MAIU GET … → 410 Throttle Limited", status=410)

    fake.get_workspace = _throttled
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=h,
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["projects"] is None and c["error_transient"] is True
    assert c["selected_project_name"] == "나"


async def test_choices_record_the_name_of_an_older_saved_project(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """이름을 기록하기 전에 저장한 선택도 목록을 한 번 읽으면 이름이 생기는지 확인한다. 잠긴 프로젝트도 같다."""
    pid = await new_project(client, admin_token, "name-old")
    await _write_block(pid, {"analysis_connection_id": mi, "maiu_workspace": "fake",
                             "maiu_project_id": "1"})
    h = _h(admin_token)
    base = (await client.get(f"/projects/{pid}/analysis-choices", headers=h)).json()
    assert base["selected_project_name"] is None, "목록을 읽기 전에 이름을 지어냈다"
    c = (await client.get(f"/projects/{pid}/analysis-choices", headers=h,
                          params={"connection_id": mi, "workspace": "fake"})).json()
    assert c["selected_project_name"] == "가"
    assert (await _settings(pid))["stack"]["maiu_project_name"] == "가"
    base = (await client.get(f"/projects/{pid}/analysis-choices", headers=h)).json()
    assert base["selected_project_name"] == "가"


async def test_changing_the_project_drops_the_old_name(
    client: AsyncClient, admin_token: str, fake, mi,
):
    """MI 프로젝트를 바꾸면 이전 이름을 남기지 않는지 확인한다. 남기면 다른 프로젝트의 이름이 표시된다."""
    pid = await new_project(client, admin_token, "name-change")
    h = _h(admin_token)
    await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "1"})
    await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={
        "analysis_connection_id": mi, "maiu_workspace": "fake", "maiu_project_id": "2"})
    assert (await _settings(pid))["stack"]["maiu_project_name"] == "나"
    await client.put(f"/projects/{pid}/analysis-stack", headers=h, json={})
    assert "maiu_project_name" not in (await _settings(pid))["stack"]


async def test_diagnosis_names_the_project_and_survives_a_failed_list(fake, monkeypatch):
    """진단 줄이 MI 프로젝트를 이름과 id로 쓰고, 이름을 찾지 못해도 진단은 정상이며 id만 쓰는지 확인한다."""
    _enable(monkeypatch, keep_fake=True)
    block = {"stack": {"analysis_connection_id": "c-1", "maiu_workspace": "fake",
                       "maiu_project_id": "2"}}
    proj = next(c for c in await provisioning.diagnose(block) if c.key == "maiu_project")
    assert (proj.status, proj.detail) == ("ok", "나 · id 2")

    async def _throttled():
        """요청량 제한을 흉내 낸다."""
        raise naver_client.MaiuTransientError("MAIU GET … → 410 Throttle Limited", status=410)

    fake.list_projects = _throttled
    proj = next(c for c in await provisioning.diagnose(block) if c.key == "maiu_project")
    assert (proj.status, proj.detail) == ("ok", "id 2")
    named = {"stack": {**block["stack"], "maiu_project_name": "나"}}
    proj = next(c for c in await provisioning.diagnose(named) if c.key == "maiu_project")
    assert proj.detail == "나 · id 2", "기록한 이름을 쓰지 않았다"


# 진단


async def test_diagnosis_says_legacy_value_is_not_used(fake, monkeypatch):
    """이전 키(`maiu_project_id`)만 있으면 진단이 분석 대상을 저장하지 않았다는 warn과 그 MI 프로젝트(id)를 골라 저장하라는 안내를 내는지 확인한다."""
    _enable(monkeypatch, keep_fake=True)
    checks = await provisioning.diagnose({
        "maiu_project_id": "2",
        "stack": {"analysis_connection_id": "c-1", "maiu_workspace": "fake"}})
    proj = next(c for c in checks if c.key == "maiu_project")
    assert proj.status == "warn" and "id 2" in proj.hint


# 주기 점검


async def test_periodic_check_records_failure_and_recovery_once_each(
    client: AsyncClient, admin_token: str, monkeypatch, mi,
):
    """주기 점검이 실패를 이어서 보아도 전이는 한 번만 기록하고, 풀리면 복구를 한 번 기록하는지 확인한다."""
    pid = await new_project(client, admin_token, "watch")
    await _write_block(pid, {"analysis_connection_id": mi, "maiu_workspace": "ws-a",
                             "maiu_project_id": "p-42"})
    monkeypatch.setattr(settings, "maiu_enabled", True)
    verdict = {"status": "fail"}

    async def _check(db, project, *, fresh=True):
        """이 프로젝트만 판정을 주고 다른 프로젝트는 실패로 건너뛰게 한다."""
        if str(project.project_id) != pid:
            raise RuntimeError("다른 테스트의 프로젝트")
        return [Check("maiu_assets", "에셋 조회 권한", verdict["status"], "인증이 거부됐습니다")]

    monkeypatch.setattr(project_stack, "check", _check)

    async def _storage_ok(db, project, *, fresh=True, origin=None):
        """저장소 대상 점검은 모든 프로젝트를 정상으로 둔다. 이 테스트는 분석 대상 점검만 본다."""
        return []

    monkeypatch.setattr(project_stack.project_storage, "check", _storage_ok)
    async with SessionLocal() as s:
        await project_stack.recheck_all(s)
        await project_stack.recheck_all(s)
    verdict["status"] = "ok"
    async with SessionLocal() as s:
        await project_stack.recheck_all(s)
    async with SessionLocal() as s:
        events = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == project_stack.HEALTH_EVENT,
            DomainEvent.target_id == UUID(pid)))).all()
    assert [e.payload["after"] for e in sorted(events, key=lambda e: e.occurred_at)] == [
        "fail", "ok"]
