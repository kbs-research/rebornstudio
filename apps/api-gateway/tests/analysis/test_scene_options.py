"""클립 길이 설정의 모델 규칙·덮어쓰기·잡 기록 테스트.

클립 길이는 API로 바꿀 수 없는 고정 기본값이다. 생성 기본값과 422 거절은 `test_fixed_options`가 본다.
여기서는 세 가지를 검증한다. 두 스택이 같은 곳의 값을 읽는다.
미뤄 둔 요청을 다시 보낼 때 기록한 값이 프로젝트 값보다 우선한다. 쓴 값이 잡에 남는다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Project
from api_gateway.services.analysis import scene_options
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, set_project_options, upload_video


@pytest.fixture(autouse=True)
async def _no_leftover_scene_jobs():
    """이 파일이 만든 장면 분할 잡을 테스트가 끝난 뒤 지운다.

    잡은 in-flight 상한을 공유한다.
    남기면 다음 파일의 `build_jobs`가 상한에 걸려 그 파일의 영상을 싣지 못한다.
    그러면 단독 실행은 통과하고 전체 실행만 실패한다.
    """
    yield
    from api_gateway.services.media.scene_split import ADAPTER_KEY
    from sqlalchemy import delete
    async with SessionLocal() as s:
        await s.execute(delete(AnalysisJob).where(AnalysisJob.adapter_key == ADAPTER_KEY))
        await s.commit()


def test_unset_means_vendor_default_not_zero():
    """비워 둔 값을 요청에 싣지 않아 벤더 기본값을 쓰게 한다.

    0으로 채워 보내면 0초짜리 클립을 요청하게 된다.
    필드를 빼야 벤더 기본값이 적용된다.
    """
    body = scene_options.SceneOptions().to_maiu_body()
    assert body == {"sceneRange": "MEDIUM"}
    assert "minSceneDurationSec" not in body and "maxSceneDurationSec" not in body


def test_set_values_reach_the_vendor_body():
    """지정한 값을 벤더 필드 이름으로 요청에 싣는다.

    길이를 직접 정하면 `sceneRange`는 빠진다(`test_preset_and_custom_are_never_sent_together`).
    """
    body = scene_options.SceneOptions(
        scene_range="SHORT", min_seconds=10, max_seconds=30).to_maiu_body()
    assert body == {"minSceneDurationSec": 10, "maxSceneDurationSec": 30}


def test_preset_and_custom_are_never_sent_together():
    """프리셋과 직접 지정을 함께 보내지 않는다.

    둘을 함께 보내면 벤더가 `409 code 10097`("sceneRange 필드와 minSceneDurationSec,
    maxSceneDurationSec 필드는 함께 쓸 수 없습니다")로 거절한다.
    항상 `sceneRange`를 실으면 길이를 지정한 프로젝트의 분석 요청이 모두 실패한다.
    """
    custom = scene_options.SceneOptions(min_seconds=15, max_seconds=60).to_maiu_body()
    assert custom == {"minSceneDurationSec": 15, "maxSceneDurationSec": 60}
    assert "sceneRange" not in custom, "직접 지정에 프리셋이 같이 실렸다 — 벤더가 409로 거절한다"

    preset = scene_options.SceneOptions(scene_range="LONG").to_maiu_body()
    assert preset == {"sceneRange": "LONG"}


def test_one_sided_range_is_allowed_but_does_not_reach_mi():
    """한쪽 값만 지정한 설정을 허용하고 MI에는 프리셋으로 보낸다.

    샷 검출 스택은 병합 상한(`max_seconds`) 하나만 쓴다.
    `sceneRange`와 `min_seconds`는 쓰지 않는다.
    그래서 모델이 한쪽만 지정한 설정을 거절하면 자체 스택을 설정할 수 없다.
    MI는 두 값을 쌍으로 요구한다(`409 code 10098` "함께 작성되어야 합니다").
    한쪽만 실어 보내면 요청이 실패하므로 쌍이 아닐 때는 프리셋으로 보낸다.
    MI 프로젝트에서 한쪽 값이 무시되지 않도록 화면이 두 값을 함께 받는다.
    """
    only_max = scene_options.SceneOptions(max_seconds=90)
    assert only_max.max_seconds == 90, "우리 스택이 읽는 값은 남아야 한다"
    assert only_max.to_maiu_body() == {"sceneRange": "MEDIUM"}, "쌍이 아닌데 MI로 나갔다"

    only_min = scene_options.SceneOptions(scene_range="LONG", min_seconds=20)
    assert only_min.to_maiu_body() == {"sceneRange": "LONG"}


def test_out_of_range_is_rejected_here_not_at_the_vendor():
    """범위 밖 값을 벤더에 보내기 전에 모델에서 거절한다.

    벤더에 보내면 400이 오지만 요청이 이미 나간 뒤라 잡이 실패로 남는다.
    그러면 운영자는 실패 사유를 벤더 오류 문자열에서 찾아야 한다.
    """
    for kw in ({"min_seconds": 5}, {"min_seconds": 31},
               {"max_seconds": 20}, {"max_seconds": 301}):
        with pytest.raises(ValueError):
            scene_options.SceneOptions(**kw)


def test_min_above_max_is_rejected():
    """하한이 상한보다 큰 설정을 벤더에 보내기 전에 모델에서 거절한다."""
    with pytest.raises(ValueError):
        scene_options.SceneOptions(min_seconds=30, max_seconds=25)


def test_broken_stored_value_falls_back_instead_of_crashing():
    """저장된 값이 잘못됐으면 예외 없이 기본값을 쓴다.

    여기서 예외를 던지면 설정 하나 때문에 프로젝트 조회 전체가 실패한다.
    """
    assert scene_options.of_project({"scene_options": "이건 dict가 아니다"}).scene_range == "MEDIUM"
    assert scene_options.of_project({"scene_options": {"min_seconds": 999}}).min_seconds is None
    assert scene_options.of_project(None).scene_range == "MEDIUM"


def test_request_beats_project():
    """요청 값이 프로젝트 값보다 우선한다. 미뤄 둔 요청은 처음 기록한 값으로 다시 보낸다."""
    stored = {"scene_options": {"scene_range": "LONG"}}
    assert scene_options.resolve(stored, None).scene_range == "LONG"
    over = scene_options.SceneOptions(scene_range="EXTRA_SHORT")
    assert scene_options.resolve(stored, over).scene_range == "EXTRA_SHORT"


def test_override_replaces_wholesale_not_field_by_field():
    """요청 값이 있으면 프로젝트 설정과 필드별로 섞지 않고 설정 전체를 바꾼다.

    필드별로 섞으면 운영자가 화면에서 본 값과 실제로 나간 값이 달라진다.
    그 차이는 분석이 끝난 뒤에야 보인다.
    """
    stored = {"scene_options": {"scene_range": "LONG", "max_seconds": 200}}
    got = scene_options.resolve(stored, scene_options.SceneOptions(scene_range="SHORT"))
    assert got.scene_range == "SHORT"
    assert got.max_seconds is None, "프로젝트의 200이 남았다 — 섞였다"


# ── API 통합 ──────────────────────────────────────────────────────────────────


async def test_the_job_records_what_it_was_analysed_with(
    client: AsyncClient, admin_token: str
):
    """요청 시점의 값을 잡에 기록하는지 확인한다.

    고정 기본값도 배포에 따라 바뀔 수 있다. 그래서 지금 프로젝트를 다시 읽어서는
    클립이 어떤 설정으로 잘렸는지 알 수 없다. 기본값과 다른 값은 DB에 직접 써서 기록을 구별한다.
    """
    from reborn_contracts.maiu import MaiuDispatchRequest

    pid = await new_project(client, admin_token, "rec")
    await set_project_options(
        pid, settings={scene_options.SETTINGS_KEY: {"scene_range": "EXTRA_SHORT"}},
    )
    vid = uuid.UUID(await upload_video(client, admin_token, pid, probed=True))
    async with SessionLocal() as s:
        from api_gateway.models import User, Video
        v = await s.get(Video, vid)
        v.transcode_status = v.deident_status = "skipped"
        v.publish_status = "done"
        user = await s.get(User, v.uploaded_by)
        await s.commit()
        from api_gateway.services.maiu import service as maiu_service
        await maiu_service.dispatch(
            s, uuid.UUID(pid),
            MaiuDispatchRequest(limit=5), user,
        )
    async with SessionLocal() as s:
        job = (await s.scalars(select(AnalysisJob).where(
            AnalysisJob.video_id == vid, AnalysisJob.adapter_key == "maiu"))).first()
    assert job is not None, "잡이 안 생겼다"
    assert job.payload["scene_options"]["scene_range"] == "EXTRA_SHORT"


async def test_our_stack_reads_the_same_knob(client: AsyncClient, admin_token: str):
    """두 스택이 같은 값을 읽는다. MI의 `maxSceneDurationSec`이 자체 스택의 병합 상한이다.

    각자 정의하면 프로젝트가 바꾼 값이 한쪽에만 반영된다.
    그 차이는 클립을 열어 보기 전까지 보이지 않는다.
    """
    from api_gateway.models import Video
    from api_gateway.services.media import scene_split

    pid = await new_project(client, admin_token, "knob", analysis_preset="ncp-shots")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.settings = {**p.settings, scene_options.SETTINGS_KEY: {"max_seconds": 90}}
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        v.duration_seconds = 300.0
        v.transcode_status = v.deident_status = "skipped"
        v.publish_status = "done"
        await s.commit()
    async with SessionLocal() as s:
        jobs = await scene_split.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))
    assert job["max_seconds"] == 90

    # 엔진이 그 값으로 실제로 병합하는지 확인한다. 값이 페이로드에만 있고 엔진이 쓰지 않으면 설정이 적용되지 않는다.
    from reborn_transcode import scene_split as engine
    shots = engine.to_shots([i * 10.0 for i in range(1, 30)], 300.0)
    assert all(s["end"] - s["start"] <= 95 for s in engine.merge(shots, [], 300.0, 90.0))
    assert len(engine.merge(shots, [], 300.0, 90.0)) < len(engine.merge(shots, [], 300.0, 40.0))
