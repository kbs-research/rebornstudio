"""비식별화를 끈 프로젝트에서 실패로 확정된 영상을 관리자가 건너뛰는 요청 테스트.

끈 프로젝트의 대기 영상은 배치가 건너뜀으로 넘긴다(`skip_disabled`). 실패로 확정된 영상은
그대로 두고 재시도도 받지 않는다. 이 영상은 관리자가 고를 때만 넘긴다(`skip_failed`).
네 가지를 확인한다.

  1. 관리자가 넘기면 `skipped`가 되고, 실패 기록을 지우고, 재생본 단계를 연다. 누가 넘겼는지와
     직전 실패 사유를 감사 기록에 남긴다. 타임라인은 넘기기 전 실패 줄에 건너뛰기 버튼을 열고,
     넘긴 뒤에는 "쓰지 않음"으로 표시한다.
  2. 관리자가 아니면 403이다.
  3. 비식별화를 켠 프로젝트는 409 `deident_enabled`다. 넘기면 가리지 않은 원본이 분석으로 나간다.
  4. 실패로 확정된 영상이 아니면 409 `deident_not_failed`다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, MediaStageRun, Project, Video
from api_gateway.services.deident_gap import DEIDENT_OFF
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project, upload_video

_BROKEN = "입력 영상을 해석하지 못했습니다"


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


async def _seed_failed(client: AsyncClient, token: str, name: str) -> tuple[str, uuid.UUID]:
    """비식별화를 켠 프로젝트에서 영구 오류로 실패가 확정된 영상을 만든다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = True
        v = await s.get(Video, vid)
        v.probe_status = "passed"
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert await open_next(s, v) == "deident"
        await s.commit()
    for _ in range(deident_service._MAX_RETRIES):
        async with SessionLocal() as s:
            jobs = await deident_service.build_jobs(s)
        job = next(j for j in jobs if j["video_id"] == str(vid))
        async with SessionLocal() as s:
            await deident_service.complete(s, vid, job["token"], ok=False, error=_BROKEN,
                                           transient=False)
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "failed"
    return pid, vid


async def _switch_off(client: AsyncClient, token: str, pid: str) -> None:
    """프로젝트 설정 화면과 같은 경로로 비식별화를 끈다."""
    r = await client.patch(f"/projects/{pid}", headers=_h(token), json={"deident_enabled": False})
    assert r.status_code == 200, r.text


async def _entry(client: AsyncClient, token: str, vid: uuid.UUID) -> dict:
    """준비 타임라인의 비식별화 줄을 읽는다."""
    r = await client.get(f"/videos/{vid}/prep-timeline", headers=_h(token))
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["entries"] if x["stage"] == "deident")


async def _skip(client: AsyncClient, token: str, pid: str, vid: uuid.UUID):
    """비식별화 건너뛰기를 요청한다."""
    return await client.post(f"/projects/{pid}/videos/{vid}/deident/skip", headers=_h(token))


async def test_admin_skips_a_failed_video_of_an_off_project(
    client: AsyncClient, admin_token: str,
):
    """관리자가 넘기면 건너뜀이 되고 재생본 단계가 열리며 누가 넘겼는지 감사 기록에 남는다."""
    pid, vid = await _seed_failed(client, admin_token, "deid-skip-ok")
    await _switch_off(client, admin_token, pid)
    before = await _entry(client, admin_token, vid)
    assert before["status"] == "failed" and before["skippable"] is True, before

    r = await _skip(client, admin_token, pid, vid)
    assert r.status_code == 204, r.text

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "skipped"
        assert v.publish_status == "pending", "재생본 단계가 열리지 않았다"
        assert "deident_error" not in (v.extra or {})
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "video.deident_skip_manual",
            DomainEvent.target_id == vid))).all()
    me = (await client.get("/auth/me", headers=_h(admin_token))).json()
    assert len(ev) == 1, "건너뛴 사실이 감사 기록에 없다"
    assert str(ev[0].actor_user_id) == me["user_id"], "누가 넘겼는지 남지 않았다"
    assert ev[0].payload["reason"] == "manual"
    assert _BROKEN in ev[0].payload["previous_error"]

    after = await _entry(client, admin_token, vid)
    assert after["status"] == "skipped" and after["skipped_reason"] == DEIDENT_OFF, after
    assert after["skippable"] is False
    async with SessionLocal() as s:
        failed = (await s.scalars(select(MediaStageRun).where(
            MediaStageRun.video_id == vid, MediaStageRun.stage == "deident",
            MediaStageRun.status == "failed"))).all()
    assert len(failed) >= 1, "넘기기 전의 실패한 시도가 이력에서 사라졌다"


async def test_non_admin_cannot_skip(client: AsyncClient, admin_token: str):
    """관리자가 아닌 멤버는 403 `not_project_admin`이고 영상은 실패로 남는다."""
    pid, vid = await _seed_failed(client, admin_token, "deid-skip-403")
    await _switch_off(client, admin_token, pid)
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    r = await _skip(client, rev, pid, vid)
    assert r.status_code == 403 and r.json()["detail"] == "not_project_admin", r.text
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "failed"


async def test_enabled_project_refuses_to_skip(client: AsyncClient, admin_token: str):
    """비식별화를 켠 프로젝트의 실패 영상은 넘기지 않고 409로 거절한다. 타임라인에도 건너뛰기 버튼을 열지 않는다."""
    pid, vid = await _seed_failed(client, admin_token, "deid-skip-on")
    assert (await _entry(client, admin_token, vid))["skippable"] is False
    r = await _skip(client, admin_token, pid, vid)
    assert r.status_code == 409 and r.json()["detail"] == "deident_enabled", r.text
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "failed"


async def test_only_a_failed_video_can_be_skipped(client: AsyncClient, admin_token: str):
    """실패로 확정된 영상이 아니면 409 `deident_not_failed`다. 완료된 영상을 건너뜀으로 바꾸지 않는다."""
    pid, vid = await _seed_failed(client, admin_token, "deid-skip-done")
    async with SessionLocal() as s:
        await s.execute(Video.__table__.update().where(Video.video_id == vid)
                        .values(deident_status="done"))
        await s.commit()
    await _switch_off(client, admin_token, pid)
    r = await _skip(client, admin_token, pid, vid)
    assert r.status_code == 409 and r.json()["detail"] == "deident_not_failed", r.text
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "done"
