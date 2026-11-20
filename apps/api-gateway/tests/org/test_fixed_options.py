"""프로젝트 처리 옵션이 고정 기본값으로 묶였는지 보는 테스트.

관리자가 바꿀 수 있는 것은 이름·설명·가이드·마감일·목표 시간·상태,
비식별화 켜기와 서버 연결, 캡션 품질검수 켜기와 검사 방식·모델 연결이다.
캡션 최소 길이·문장 타임코드·원본 보관·비식별화 목표·클립 길이·
반려 사유 목록·품질검수 프롬프트·용어집·비속어 사전은 고정 기본값이고 요청에 실으면 422다.
없어진 설정(보유 만료 시간·동시 보유 한도 둘)도 요청에 실으면 422다.
모르는 필드를 조용히 무시하면 200을 받고도 값이 바뀌지 않아 화면 안내가 사실과 달라진다.
옵션에 따라 달라지는 동작은 각 도메인 테스트가 DB에 값을 직접 써서 확인한다(`set_project_options`).
"""
import uuid
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent, Project
from api_gateway.services import reject_reasons
from api_gateway.services.analysis import scene_options
from api_gateway.services.caption_qc import settings as qc_settings
from httpx import AsyncClient
from sqlalchemy import func, select
from tests._helpers import add_member, new_project, upload_video

#: 고정된 처리 옵션과 기본값이 아닌 값. 한 요청에 하나씩 보낸다.
#: 몰아 보내면 한 필드가 거절되지 않아도 다른 필드의 422에 가려진다.
_FIXED = [
    ("default_assignment_ttl_hours", 5),
    ("caption_min_tokens", 0),
    ("sentence_timecode_enabled", False),
    ("retain_original", True),
    ("target_deident_pct", 50),
    ("scene_options", {"scene_range": "SHORT", "min_seconds": 12, "max_seconds": 40}),
    ("reject_reasons", [{"code": None, "label": "새 사유", "enabled": True}]),
    ("analysis_preset", "ncp-shots"),
    # 없어진 설정. 한 사람이 역할마다 한 건씩 맡아 동시 보유 한도가 없다.
    ("default_max_concurrent_assignments", 3),
    ("review_max_concurrent", 7),
]

#: 품질검수 설정에서 고정된 하위 필드와 기본값이 아닌 값.
_FIXED_QC = [
    ("prompts", {"grammar": "프롬프트"}),
    ("glossary", {"AI": "에이아이"}),
    ("profanity", ["비속어표본"]),
]


def _h(tok: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _snapshot(pid: str) -> tuple:
    """프로젝트 행의 처리 옵션 컬럼과 `settings`, `project.updated` 감사 행 수를 읽는다."""
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        n = await s.scalar(select(func.count()).select_from(DomainEvent).where(
            DomainEvent.project_id == UUID(pid), DomainEvent.event_type == "project.updated"))
    cols = (p.name, p.default_assignment_ttl_hours, p.caption_min_tokens,
            p.sentence_timecode_enabled, p.retain_original, float(p.target_deident_pct),
            p.default_max_concurrent_assignments, p.review_max_concurrent)
    return cols, dict(p.settings or {}), n


async def _connection(client: AsyncClient, admin_token: str) -> str:
    """품질검수가 고를 수 있는 모델 연결을 하나 만들고 이름을 돌려준다."""
    name = f"qc-fixed-{uuid.uuid4().hex[:8]}"
    r = await client.post("/connections", headers=_h(admin_token), json={
        "name": name, "provider": "anthropic", "config": {"model": "m"},
        "access_key": "", "secret_key": "SECRET_FIXED",
    })
    assert r.status_code == 201, r.text
    return name


@pytest.mark.parametrize(("key", "value"), _FIXED, ids=[k for k, _ in _FIXED])
async def test_fixed_option_in_patch_is_422_and_changes_nothing(
    client: AsyncClient, admin_token: str, key: str, value: object,
):
    """고정 옵션을 실은 수정 요청이 422이고 같은 요청의 이름도 저장되지 않는지 확인한다.

    오류는 그 필드를 가리킨다. 행과 `settings`, 감사 기록이 모두 그대로다.
    """
    pid = await new_project(client, admin_token, "fixed-patch")
    before = await _snapshot(pid)
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token),
                           json={"name": "바뀌면 안 되는 이름", key: value})
    assert r.status_code == 422, r.text
    assert ["body", key] in [e["loc"][:2] for e in r.json()["detail"]], r.text
    assert await _snapshot(pid) == before


@pytest.mark.parametrize(("key", "value"), _FIXED_QC, ids=[k for k, _ in _FIXED_QC])
async def test_fixed_caption_qc_field_is_422_and_changes_nothing(
    client: AsyncClient, admin_token: str, key: str, value: object,
):
    """품질검수 설정에 프롬프트·용어집·비속어 사전을 실으면 422이고 켜기도 저장되지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "fixed-qc")
    before = await _snapshot(pid)
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token),
                           json={"caption_qc": {"enabled": True, key: value}})
    assert r.status_code == 422, r.text
    assert ["body", "caption_qc", key] in [e["loc"] for e in r.json()["detail"]], r.text
    assert await _snapshot(pid) == before


async def test_allowed_fields_still_save(client: AsyncClient, admin_token: str):
    """바꿀 수 있는 필드는 저장되고 고정 옵션은 기본값 그대로인지 확인한다.

    품질검수는 켜기와 검사 방식, 모델 연결만 저장하고 프롬프트·용어집·비속어 사전은 비어 있다.
    """
    name = await _connection(client, admin_token)
    pid = await new_project(client, admin_token, "fixed-ok")
    body = {
        "name": "고정 옵션 확인",
        "description": "설명",
        "guide_md": "# 가이드",
        "contract_deadline": "2099-01-01",
        "target_hours": 120,
        "project_status": "paused",
        "deident_enabled": True,
        "caption_qc": {"enabled": True, "adapter_key": "llm", "connection_name": name},
    }
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["name"], got["description"], got["guide_md"]) == ("고정 옵션 확인", "설명", "# 가이드")
    assert (got["contract_deadline"], got["target_hours"], got["status"]) == ("2099-01-01", 120, "paused")
    assert got["deident_enabled"] is True
    qc = got["caption_qc"]
    assert (qc["enabled"], qc["adapter_key"], qc["connection_name"]) == (True, "llm", name)
    assert (qc["prompts"], qc["glossary"], qc["profanity"]) == ({}, {}, [])
    # 고정 옵션은 기본값 그대로다. 없어진 설정은 응답에 싣지 않는다.
    assert "default_assignment_ttl_hours" not in got
    assert "default_max_concurrent_assignments" not in got
    assert got["caption_min_tokens"] == 50
    assert got["retain_original"] is False
    assert got["target_deident_pct"] == 90

    # 품질검수를 끄고 연결을 비우는 것도 저장된다.
    r = await client.patch(f"/projects/{pid}", headers=_h(admin_token), json={
        "caption_qc": {"enabled": False, "connection_name": None}})
    assert r.status_code == 200, r.text
    assert (r.json()["caption_qc"]["enabled"], r.json()["caption_qc"]["connection_name"]) \
        == (False, None)


async def test_project_admin_cannot_set_removed_concurrency_limits(
    client: AsyncClient, admin_token: str,
):
    """멤버인 프로젝트 관리자가 보낸 동시 보유 한도도 422인지 확인한다. 한도 설정은 없어졌다."""
    pid = await new_project(client, admin_token, "fixed-limit")
    _, pa = await add_member(client, admin_token, pid, "project_admin")
    r = await client.patch(f"/projects/{pid}", headers=_h(pa), json={
        "default_max_concurrent_assignments": 2, "review_max_concurrent": 9})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("extra", [
    {"scene_options": {"scene_range": "SHORT"}},
    {"settings": {"retain_original": True}},
    {"caption_qc": {"enabled": True, "glossary": {"AI": "에이아이"}}},
], ids=["scene_options", "settings", "caption_qc_glossary"])
async def test_create_with_fixed_option_is_422_and_makes_nothing(
    client: AsyncClient, admin_token: str, extra: dict,
):
    """생성 요청에 처리 옵션을 실으면 422이고 프로젝트가 생기지 않는지 확인한다."""
    slug = f"fixed-new-{uuid.uuid4().hex[:8]}"
    r = await client.post("/projects", headers=_h(admin_token),
                          json={"slug": slug, "name": slug, **extra})
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Project).where(
            Project.slug == slug)) == 0


async def test_created_project_gets_the_fixed_defaults(client: AsyncClient, admin_token: str):
    """새 프로젝트가 고정 기본값을 갖는지 확인한다.

    클립 길이는 `MEDIUM`이고 하한·상한은 비어 있다. 반려 사유는 기본 목록이고 변환은 꺼져 있다.
    클립 길이는 생성 때 `settings`에 적어 분석 잡이 같은 값을 읽는다.
    """
    pid = await new_project(client, admin_token, "fixed-defaults")
    got = (await client.get(f"/projects/{pid}", headers=_h(admin_token))).json()
    assert got["reject_reasons"] == [r.model_dump() for r in reject_reasons.BUILTIN]
    assert got["caption_min_tokens"] == 50
    assert got["retain_original"] is False
    assert got["target_deident_pct"] == 90
    qc = got["caption_qc"]
    assert (qc["prompts"], qc["glossary"], qc["profanity"]) == ({}, {}, [])

    async with SessionLocal() as s:
        settings = (await s.get(Project, UUID(pid))).settings
    assert scene_options.of_project(settings) == scene_options.SceneOptions()
    assert settings[scene_options.SETTINGS_KEY]["scene_range"] == "MEDIUM"
    assert qc_settings.of_project(settings).glossary == {}


async def test_dispatch_with_scene_options_is_422(client: AsyncClient, admin_token: str):
    """분석 요청에 클립 길이를 실으면 422이고 분석 잡이 생기지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "fixed-dispatch")
    vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
    r = await client.post(f"/projects/{pid}/maiu/dispatch", headers=_h(admin_token), json={
        "video_ids": [vid], "scene_options": {"scene_range": "SHORT"}})
    assert r.status_code == 422, r.text
    assert ["body", "scene_options"] in [e["loc"] for e in r.json()["detail"]], r.text
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(AnalysisJob).where(
            AnalysisJob.project_id == UUID(pid))) == 0
