"""분석 스택 프리셋 테스트. 카탈로그는 코드에 정의한다.

세 가지를 확인한다.
임의 조합을 만들 수 없다.
쓸 수 없는 프리셋도 이유와 함께 목록에 표시된다.
프로젝트 생성 뒤에는 스택을 바꿀 수 없다.
"""
from api_gateway.services.analysis import presets
from httpx import AsyncClient
from tests._helpers import new_project


async def test_catalog_shows_unavailable_ones_with_a_reason(
    client: AsyncClient, admin_token: str
):
    """쓸 수 없는 프리셋도 이유와 함께 목록에 표시한다.

    목록에서 빼면 운영자는 그 프리셋이 없는지 아직 쓸 수 없는지 구분하지 못한다.
    그래서 `available=False`인 프리셋에는 이유가 있어야 한다.
    """
    rows = (await client.get(
        "/projects/analysis-presets", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    assert len(rows) >= 2
    for r in rows:
        if not r["available"]:
            assert r["unavailable_reason"], f"이유 없는 비활성은 막다른 길이다: {r['key']}"


async def test_creation_records_the_stack(client: AsyncClient, admin_token: str):
    """고른 스택을 프로젝트 설정에 기록한다. 나중에 어떤 스택으로 분석했는지 확인하는 근거다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Project

    pid = await new_project(client, admin_token, "preset")
    async with SessionLocal() as s:
        p = await s.get(Project, __import__("uuid").UUID(pid))
    assert p.settings[presets.SETTINGS_KEY] == presets.DEFAULT_KEY


async def test_unavailable_preset_cannot_be_chosen(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """쓸 수 없는 프리셋으로 프로젝트를 만들면 400으로 거절한다.

    고를 수 있게 두면 영상 등록은 되지만 분석은 되지 않는 프로젝트가 생긴다.
    """
    blocked = presets.NCP_SHOTS.model_copy(update={
        "available": False, "unavailable_reason": "시험용으로 막은 방식입니다.",
    })
    monkeypatch.setattr(presets, "_ALL", (presets.NCP_MI, blocked))

    r = await client.post(
        "/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"slug": "blocked-preset", "name": "막힌 스택",
              "analysis_preset": blocked.key},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "analysis_preset_unavailable"


async def test_unknown_preset_is_rejected_not_defaulted(client: AsyncClient, admin_token: str):
    """등록되지 않은 프리셋 키를 기본값으로 바꾸지 않고 400으로 거절한다.

    알리지 않고 기본값으로 바꾸면 운영자가 고른 스택과 실제로 도는 스택이 달라진다.
    이 차이는 분석이 끝난 뒤에야 드러난다.
    """
    r = await client.post(
        "/projects",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"slug": "bad-preset", "name": "없는 스택",
              "analysis_preset": "nope"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "unknown_analysis_preset"


async def test_stack_is_not_in_the_update_allowlist(client: AsyncClient, admin_token: str):
    """프로젝트 갱신이 스택 키를 422로 거절해 생성 때 고른 스택이 그대로 남는지 확인한다.

    A 벤더로 분석한 클립이 있는데 B로 바꾸면 한 프로젝트에 두 벤더의 결과가 섞인다.
    라벨러는 그 차이를 모른 채 작업하게 된다.
    스택이 갱신 허용 목록에 들어가면 API 호출 하나로 두 벤더의 결과가 섞일 수 있다.
    갱신 요청은 모르는 필드를 거절하므로 같은 요청의 다른 필드도 저장되지 않는다.
    """
    from uuid import UUID

    from api_gateway.database import SessionLocal
    from api_gateway.models import Project
    from reborn_contracts import ProjectUpdateRequest

    assert presets.SETTINGS_KEY not in ProjectUpdateRequest.model_fields

    pid = await new_project(client, admin_token, "preset-fixed")
    other = presets.NCP_SHOTS.key
    assert other != presets.DEFAULT_KEY
    r = await client.patch(
        f"/projects/{pid}", headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "스택 고정", presets.SETTINGS_KEY: other},
    )
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
    assert p.name != "스택 고정"
    assert p.settings[presets.SETTINGS_KEY] == presets.DEFAULT_KEY


def test_existing_projects_fall_back_to_the_running_stack():
    """설정이 없는 기존 프로젝트를 기본 조합(`DEFAULT_KEY`)으로 읽는다. 그래서 기존 프로젝트의 동작이 바뀌지 않는다."""
    assert presets.of_project(None).key == presets.DEFAULT_KEY
    assert presets.of_project({}).key == presets.DEFAULT_KEY


def test_mi_has_no_separate_scene_step():
    """MI 조합에 장면 분할 어댑터가 없는지 확인한다. MI가 분석하면서 구간을 자른다.

    화면은 이 값으로 장면 분할 항목을 "벤더 위임"으로 표시한다.
    """
    assert presets.NCP_MI.scene_adapter_key is None


def test_our_own_cutting_is_selectable():
    """이 시스템이 직접 자르는 스택을 고를 수 있는지 확인한다.

    파이프라인이 완성되어 있어도 아무도 그 스택을 고를 수 없으면 실행되지 않는다.
    지금 카탈로그에서 `available`이면서 직접 자르는 조합은 `NCP_SHOTS` 하나다.
    """
    assert presets.NCP_SHOTS.available is True
    assert presets.NCP_SHOTS.scene_adapter_key == "scenedetect"
    # 벤더 분석기가 없다. 이 스택은 자르는 일만 한다.
    assert presets.NCP_SHOTS.adapter_key == presets.NCP_SHOTS.scene_adapter_key


def test_presets_differ_on_both_axes():
    """조합을 스토리지와 분석기 두 축의 코드 값으로 정하는지 확인한다.

    한 축만 코드에 두면 다른 축은 라벨에만 남는다.
    """
    assert {p.key for p in presets.all_presets()} == {"ncp-mi", "ncp-shots"}
    # `ncp-shots`는 MI 조합과 스토리지가 같고 분석기만 다르다.
    assert presets.NCP_SHOTS.storage_key == presets.NCP_MI.storage_key
    assert presets.NCP_SHOTS.adapter_key != presets.NCP_MI.adapter_key
