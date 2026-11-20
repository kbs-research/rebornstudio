"""자격증명을 등록한 연결에서만 읽는지 보는 테스트.

보장하는 것은 다음과 같다.
- 서버 환경 변수에 NCP 키·버킷이 있어도 설정과 자격증명 해석에 들어오지 않는다.
- 연결을 고르지 않은 대상은 어떤 자격증명도 받지 않는다. 버킷만 아는 경로도 알 수 없는 버킷은 읽지 않는다.
- 등록한 연결이 없으면 프로젝트 추가 화면의 방식이 그 사유와 먼저 등록할 연결의 서비스를 싣는다.
- 이전 영상의 원본 버킷은 그 영상에 고정 기록한 연결로 읽는다.

외부(NCP)는 부르지 않는다. 저장소는 인프로세스 moto다.
"""
import os
import uuid
from uuid import UUID

import pytest
from api_gateway.config import Settings
from api_gateway.database import SessionLocal
from api_gateway.models import (
    Connection,
    Video,
)
from api_gateway.services import crypto
from api_gateway.services import platform_credentials as pc
from api_gateway.services.analysis import presets
from httpx import AsyncClient
from tests import _storage
from tests._helpers import new_project, upload_video


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


# env는 읽지 않는다


def test_env_ncp_keys_are_ignored(monkeypatch):
    """이전 NCP 환경 변수가 남아 있어도 설정 필드가 되지 않고 기동도 막지 않는지 확인한다."""
    for key, value in {"S3_ENDPOINT": "https://x", "S3_BUCKET": "env-bucket",
                       "S3_ACCESS_KEY": "env-ak", "S3_SECRET_KEY": "env-sk" * 8,
                       "NCLOUD_ACCESS_KEY": "env-ak", "NCLOUD_SECRET_KEY": "env-sk",
                       "MAIU_WORKSPACE_NAME": "ws-env", "MAIU_PROJECT_ID": "env-1",
                       "MAIU_BASE_URL": "https://mi.env", "NCP_OBJECT_STORAGE_ENDPOINT": "x"
                       }.items():
        monkeypatch.setenv(key, value)
    s = Settings(_env_file=None)
    for field in ("s3_bucket", "s3_access_key", "s3_secret_key", "s3_endpoint", "s3_region",
                  "ncloud_access_key", "ncloud_secret_key", "maiu_workspace_name",
                  "maiu_project_id", "maiu_base_url", "ncp_object_storage_endpoint"):
        assert not hasattr(s, field), field
    assert os.environ["S3_BUCKET"] == "env-bucket"
    assert pc.storage_for_or_none(None) is None
    assert pc.storage_for_bucket_or_none("env-bucket") is None
    assert pc.analysis_for_or_none(pc.AnalysisTarget(None, "ws-env", "env-1")) is None


def test_targets_without_a_connection_get_no_credentials():
    """연결을 고르지 않은 대상이 다른 필드를 채워도 자격증명을 받지 않는지 확인한다. 채워 줄 기본값이 없다."""
    assert pc.storage_for_or_none(pc.StorageTarget(None, _storage.BUCKET)) is None
    assert pc.storage_for_or_none(pc.StorageTarget(_storage.connection_id(), None)) is None
    with pytest.raises(pc.CredentialsMissingError) as e:
        pc.storage_for(pc.StorageTarget(None, _storage.BUCKET))
    assert e.value.code == pc.STORAGE_NOT_CONFIGURED
    with pytest.raises(pc.CredentialsMissingError) as e:
        pc.analysis_for(pc.AnalysisTarget(None, "ws", "1"))
    assert e.value.code == pc.ANALYSIS_NOT_CONFIGURED
    creds = pc.storage_for(_storage.target())
    assert (creds.bucket, creds.connection_id) == (_storage.BUCKET, _storage.connection_id())


# 등록한 연결이 없을 때


async def test_presets_name_the_connection_to_register_first(monkeypatch):
    """등록한 연결이 없으면 NCP 방식을 고를 수 없고 먼저 등록할 연결의 서비스를 싣는지 확인한다."""
    from api_gateway.config import settings

    monkeypatch.setattr(pc, "_storage_by_id", {})
    monkeypatch.setattr(pc, "_analysis_by_id", {})
    monkeypatch.setattr(settings, "maiu_enabled", True)
    by = {p.key: p for p in presets.all_presets()}
    assert not by["ncp-mi"].available and by["ncp-mi"].missing_providers == ["ncp", "maiu"]
    assert "등록한 NCP 저장소 연결과 분석 연결이 없어" in by["ncp-mi"].unavailable_reason
    assert not by["ncp-shots"].available and by["ncp-shots"].missing_providers == ["ncp"]

    monkeypatch.setattr(settings, "maiu_enabled", False)
    by = {p.key: p for p in presets.all_presets()}
    assert by["ncp-mi"].missing_providers == ["ncp"], "가짜 분석이면 분석 연결은 필요 없다"


async def test_choices_list_connections_and_fill_nothing(
    client: AsyncClient, admin_token: str,
):
    """선택지가 등록한 연결을 싣되 처음 값은 주지 않는지 확인한다. 관리자가 직접 골라야 저장된다."""
    h = _h(admin_token)
    pid = await new_project(client, admin_token, "only-org", storage=False)
    mine = await _storage.connection_for(pid)
    c = (await client.get(f"/projects/{pid}/storage-choices", headers=h)).json()
    assert mine in [o["id"] for o in c["connections"]]
    assert c["selected"] == {"storage_connection_id": None, "bucket": None}
    assert "default" not in c and c["buckets"] is None


# 이전 원본 버킷


async def test_old_source_bucket_is_read_with_the_videos_connection(
    client: AsyncClient, admin_token: str,
):
    """프로젝트 버킷이 아닌 곳에 원본이 있는 영상을 그 영상에 고정 기록한 연결로 읽는지 확인한다.

    기본 연결을 쓰던 때 다른 버킷에서 등록한 원본이다. 이행이 영상에 그때의 연결을 기록했다.
    """
    pid = await new_project(client, admin_token, "old-src")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.source_uri = "s3://legacy-elsewhere/old/a.mp4"
        await s.commit()
    assert pc.storage_for_bucket_or_none("legacy-elsewhere") is None
    await _storage.reset_memory()
    creds = pc.storage_for_bucket("legacy-elsewhere")
    assert creds.bucket == "legacy-elsewhere"
    assert creds.connection_id == await _storage.connection_for(pid)


# 이행


async def _connection(org_id, provider: str, config: dict) -> str:
    """이행 테스트용 연결을 만들고 id를 돌려준다."""
    async with SessionLocal() as s:
        c = Connection(org_id=org_id, name=f"{provider}-{uuid.uuid4().hex[:8]}",
                       provider=provider, config=config,
                       secret_ciphertext=crypto.encrypt("ak\nsk"), last_status="ok")
        s.add(c)
        await s.commit()
        return str(c.id)

