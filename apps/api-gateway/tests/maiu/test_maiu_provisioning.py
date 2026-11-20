"""MI 분석 자원 진단 테스트. 진단은 읽기만 하고 벤더 계정에 자원을 만들지 않는다.

세 가지를 확인한다. 진단은 아무것도 만들지 않는다. 자원이 없는 경우와 읽지 못한 경우를 구분한다.
자원을 만드는 API 경로는 없다.
"""
import pytest
from api_gateway.config import settings
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import provisioning
from tests import _storage

#: 진단이 쓰는 분석 연결 id. `get_client`를 가짜로 바꿔 어떤 값이든 같은 가짜를 받는다.
_CONN = "c-test"


@pytest.fixture
def fake(monkeypatch):
    """가짜 클라이언트 하나를 고정한다. 진단 뒤에 만든 자원이 없는지 같은 인스턴스로 확인한다.

    분석 연동을 켠다. 워크스페이스 연동 버킷은 테스트 저장소다.
    """
    cli = maiu_client.FakeMaiuClient()
    cli.linked_bucket = _storage.BUCKET
    monkeypatch.setattr(maiu_client, "get_client", lambda target=None: cli)
    monkeypatch.setattr(settings, "maiu_enabled", True)
    return cli


def _proj() -> dict:
    """저장소 대상과 분석 연결·워크스페이스를 고른 프로젝트 설정. MI 프로젝트는 아직 없다."""
    return {"stack": {"storage_connection_id": _storage.connection_id(),
                      "bucket": _storage.BUCKET,
                      "analysis_connection_id": _CONN, "maiu_workspace": "ws"}}


def _by(checks, key):
    """검사 결과 목록에서 key가 같은 항목을 찾는다."""
    return next(c for c in checks if c.key == key)


# ── 진단: 읽기만 한다 ────────────────────────────────────────────────────────


async def test_diagnose_does_not_create_anything(fake):
    """진단이 아무것도 만들지 않는지 확인한다. 화면이 진단을 자동으로 호출하므로 언제 호출해도 안전해야 한다."""
    await provisioning.diagnose(_proj())
    assert fake.list_projects.__self__._projects == [], "진단이 프로젝트를 만들었다"


async def test_diagnose_reports_bucket_mismatch_as_a_failure(fake):
    """연동 버킷이 프로젝트 버킷과 다르면 fail로 진단한다.

    버킷이 달라도 등록·업로드는 모두 성공하고 분석만 실패한다. 그래서 진단 없이는 발견하기 어렵다.
    테스트는 벤더 쪽 값을 바꾼다. 프로젝트 설정을 바꾸면 가짜 클라이언트가 그 값을 그대로 돌려줘서
    항상 일치한다.
    """
    fake.linked_bucket = "남의-버킷"
    c = _by(await provisioning.diagnose(_proj()), "maiu_bucket")
    assert c.status == "fail"
    assert "남의-버킷" in c.detail and _storage.BUCKET in c.detail, "실측값을 안 적었다"


async def test_workspace_without_a_linked_bucket_is_a_failure(fake):
    """연동 버킷이 없어도 fail로 진단한다. 연동이 없는 경우와 다른 경우는 사유가 다르지만 결과는 같다."""
    fake.linked_bucket = ""
    assert _by(await provisioning.diagnose(_proj()), "maiu_bucket").status == "fail"


async def test_missing_workspace_and_unreadable_are_not_the_same(fake):
    """워크스페이스가 없는 경우와 읽지 못한 경우를 다른 힌트로 진단한다.

    404면 콘솔에서 만들면 해결되지만 자격 증명 문제는 만들어도 해결되지 않는다.
    """
    fake.workspace_missing = True
    c = _by(await provisioning.diagnose(_proj()), "maiu_ws")
    assert c.status == "fail"
    # 힌트가 해결 방법을 가리키는지 확인한다. 문구 전체 대신 만들 곳(`콘솔`)만 확인한다.
    # 문장 전체를 단언하면 같은 뜻으로 문장을 다듬을 때마다 테스트가 실패한다.
    assert "콘솔" in (c.hint or ""), "고치는 법(콘솔에서 만들기)이 안 적혀 있다"

    async def _denied():
        """인증 실패로 조회가 거부되는 경우를 흉내 낸다."""
        raise RuntimeError('MAIU GET /api/v1/workspaces/ws → 401 {"error": "unauthorized"}')

    fake.workspace_missing = False
    fake.get_workspace = _denied
    c = _by(await provisioning.diagnose(_proj()), "maiu_ws")
    # 짝이 되는 긍정 단언과 같은 낱말을 확인한다. 기준 낱말이 같아야 두 경우를 함께 구분한다.
    assert c.status == "fail" and "RuntimeError" not in c.detail
    assert c.detail.startswith("조회 실패") and "콘솔" not in (c.hint or "")


async def test_no_mi_project_is_a_warning_not_a_failure(fake):
    """MI 프로젝트가 없으면 warn으로 진단한다. 기본 프로젝트로도 분석이 돈다."""
    assert _by(await provisioning.diagnose(_proj()), "maiu_project").status == "warn"


async def test_disabled_maiu_says_so_instead_of_pretending(fake, monkeypatch):
    """`MAIU_ENABLED=false`면 조회하지 않고 꺼져 있다고 표시한다. 조회를 시도하면 실패처럼 보인다."""
    monkeypatch.setattr(settings, "maiu_enabled", False)
    checks = await provisioning.diagnose(_proj())
    assert checks[0].status == "warn" and "꺼져 있습니다" in checks[0].detail
    assert "MAIU_ENABLED" not in checks[0].detail + (checks[0].hint or "")


async def test_throttled_workspace_read_is_a_warning(fake, monkeypatch):
    """요청량 제한으로 워크스페이스를 읽지 못하면 warn으로 진단하고 힌트에 인증 문제를 쓰지 않는다."""
    from api_gateway.services.maiu.naver_client import MaiuTransientError

    async def _throttled():
        """요청량 제한을 흉내 낸다."""
        raise MaiuTransientError(
            'MAIU GET /api/v1/workspaces/ws → 429 {"code":"410","message":"Throttle Limited"}',
            status=429)

    monkeypatch.setattr(fake, "get_workspace", _throttled)
    ws = _by(await provisioning.diagnose(_proj()), "maiu_ws")
    assert ws.status == "warn", ws
    assert "인증" not in (ws.hint or "")


async def test_failure_hint_never_names_server_env_vars(fake, monkeypatch):
    """프로젝트 화면의 진단은 환경 변수 이름을 싣지 않는다. 힌트는 분석 연동과 자격증명 연결을 가리킨다."""
    async def _denied():
        """권한 거절을 흉내 낸다."""
        raise RuntimeError("MAIU GET /api/v1/workspaces/ws → 401 unauthorized")

    monkeypatch.setattr(fake, "get_workspace", _denied)
    ws = _by(await provisioning.diagnose(_proj()), "maiu_ws")
    assert ws.status == "fail"
    shown = ws.detail + (ws.hint or "")
    for name in ("MAIU_WORKSPACE_NAME", "NCLOUD_ACCESS_KEY", "NCLOUD_SECRET_KEY"):
        assert name not in shown, shown
    assert "자격증명 연결" in (ws.hint or ""), ws.hint


async def test_nothing_is_diagnosed_without_a_picked_connection(fake):
    """분석 연결·워크스페이스를 고르지 않으면 진단은 그 항목 하나만 fail로 돌려준다."""
    bare = {"stack": {"storage_connection_id": _storage.connection_id(),
                      "bucket": _storage.BUCKET}}
    checks = await provisioning.diagnose(bare)
    assert [(c.key, c.status) for c in checks] == [("maiu_ws", "fail")]
    assert fake._projects == []


# ── API 통합 ──────────────────────────────────────────────────────────────────


async def test_get_is_read_only_and_there_is_no_create_route(client, admin_token, fake):
    """GET은 아무것도 만들지 않고, 자원을 만드는 POST 경로는 없는지 확인한다.

    워크스페이스와 MI 프로젝트는 운영자가 NCP 콘솔에서 만든다. 화면 조회의 부수 효과로도,
    버튼으로도 벤더 계정에 자원이 생기지 않아야 한다.
    """
    from tests._analysis import mi_connection
    from tests._helpers import new_project

    cid = await mi_connection(client, admin_token)
    pid = await new_project(client, admin_token, "prov")
    h = {"Authorization": f"Bearer {admin_token}"}

    r = await client.get(f"/projects/{pid}/analysis-provisioning", headers=h)
    assert r.status_code == 200, r.text
    assert fake._projects == [], "GET이 벤더 자원을 만들었다"

    r = await client.post(f"/projects/{pid}/analysis-provisioning", headers=h,
                          json={"analysis_connection_id": cid, "maiu_workspace": "ws"})
    assert r.status_code == 405, r.text
    assert fake._projects == [], "POST가 벤더 자원을 만들었다"


# ── 경로: 가짜 클라이언트가 확인하지 못하는 부분 ──────────────────────────────


def test_project_list_hangs_off_the_workspace_not_the_project():
    """프로젝트 목록 경로가 워크스페이스 바로 아래(`_ws()` 뒤 `/projects`)에 있는지 확인한다.

    `_wp()`는 이미 `/projects/{id}`를 포함한다. 여기에 `/projects`를 또 붙이면
    `.../projects/101/projects`가 되고, 이 오류는 실제 MI를 호출해야 404로 드러난다.
    가짜 클라이언트는 URL을 확인하지 않으므로 이 오류를 잡지 못한다.
    그래서 경로 문자열을 직접 확인한다.
    """
    from api_gateway.services.maiu.naver_client import NaverMaiuClient

    cli = NaverMaiuClient.__new__(NaverMaiuClient)
    cli.workspace, cli.project_id = "ws", "101"

    assert cli._ws() == "/api/v1/workspaces/ws"
    assert cli._wp() == "/api/v1/workspaces/ws/projects/101"
    # 목록 경로에는 프로젝트 id가 없어야 한다.
    assert f"{cli._ws()}/projects" == "/api/v1/workspaces/ws/projects"
    assert "/projects/101/projects" not in f"{cli._ws()}/projects"
