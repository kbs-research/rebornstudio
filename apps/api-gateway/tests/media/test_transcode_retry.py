"""변환 재시도 테스트.

엔드포인트는 `POST /projects/{pid}/videos/{vid}/transcode/retry`다.
변환만 실패한 영상은 규격을 통과했으므로 `probe/reset`이 409로 거절한다. 그래서 이 경로가 유일한 복구 수단이다.
세 가지를 확인한다.
  1. `failed`만 다시 돌린다. 도는 중에 다시 넣으면 잡이 둘이 된다.
     끝난 변환을 다시 돌리면 다음 단계가 쓴 마스터를 덮어쓴다.
     서버가 409 `transcode_not_failed`로 거절한다.
  2. 재시도하면 다음 투입(`build_jobs`)이 그 영상을 실제로 가져간다. 시도 횟수를 비워야
     다음 실패 한 번에 바로 영구 실패가 되지 않는다.
  3. 이 영상의 규격 재검사(`probe/reset`) 요청은 409로 거절한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, Video
from api_gateway.services.media import transcode as transcode_service
from api_gateway.services.media.video import merge_extra
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project, reset_transcode_active, upload_video


async def _fail_transcode(video_id: str, *, attempts: int = 3) -> None:
    """상한에 걸려 영구 실패한 상태를 만든다.

    `complete_transcode`가 남기는 것과 같은 필드를 채운다.
    """
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.probe_status = "passed"
        v.transcode_status = "failed"
        v.transcode_attempts = attempts
        v.extra = {**(v.extra or {}), "probe": {"video_codec": "av1", "height": 720}}
        await s.flush()
        await merge_extra(s, v.video_id, {"transcode_error": "ffmpeg 종료 코드 1"})
        await s.commit()


async def test_failed_transcode_goes_back_to_the_queue(client: AsyncClient, admin_token: str):
    """영구 실패한 변환을 다시 대기열에 넣는다. 상태·시도 횟수·실패 기록을 비우고 감사 이벤트를 남긴다."""
    await reset_transcode_active()
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "tc-retry")
    vid = await upload_video(client, admin_token, pid)
    await _fail_transcode(vid)

    r = await client.post(f"/projects/{pid}/videos/{vid}/transcode/retry", headers=h)
    assert r.status_code == 204, r.text

    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert v.transcode_status == "pending"
        # 시도 횟수를 비우지 않으면 다음 영구 실패 한 번에 곧바로 다시 `failed`가 된다.
        assert v.transcode_attempts == 0
        assert "transcode_error" not in (v.extra or {}), "실패 흔적이 남으면 목록이 계속 실패라 말한다"
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.target_id == UUID(vid),
            DomainEvent.event_type == "video.transcode_retried",
        ))).first()
    assert ev is not None, "운영 액션은 감사에 남는다"
    assert ev.payload["previous_error"] == "ffmpeg 종료 코드 1"


async def test_the_next_dispatch_picks_it_up(client: AsyncClient, admin_token: str):
    """다시 넣은 영상을 다음 투입이 실제로 가져가는지 확인한다.

    상태만 바꾸고 아무도 가져가지 않으면 버튼을 눌러도 아무 일도 일어나지 않는다.
    """
    await reset_transcode_active()
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "tc-retry-pick")
    vid = await upload_video(client, admin_token, pid)
    await _fail_transcode(vid)
    assert (await client.post(
        f"/projects/{pid}/videos/{vid}/transcode/retry", headers=h)).status_code == 204

    async with SessionLocal() as s:
        jobs = await transcode_service.build_jobs(s)
    assert any(j["video_id"] == vid for j in jobs), "되살린 영상을 투입이 안 집었다"


async def test_only_failed_transcodes_are_retried(client: AsyncClient, admin_token: str):
    """도는 중이거나 끝났거나 건너뛴 변환의 재시도를 409로 거절한다.

    허용하면 잡이 둘 돌거나 다음 단계가 쓴 마스터를 덮어쓴다.
    """
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "tc-retry-409")
    vid = await upload_video(client, admin_token, pid)
    for st in ("pending", "running", "done", "skipped"):
        async with SessionLocal() as s:
            v = await s.get(Video, UUID(vid))
            v.transcode_status = st
            await s.commit()
        r = await client.post(f"/projects/{pid}/videos/{vid}/transcode/retry", headers=h)
        assert r.status_code == 409, (st, r.text)
        assert r.json()["detail"] == "transcode_not_failed"
    await reset_transcode_active()


async def test_probe_reset_is_not_the_way_back(client: AsyncClient, admin_token: str):
    """변환만 실패한 영상의 규격 재검사를 409로 거절한다. 그래서 변환 재시도 경로가 따로 필요하다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "tc-retry-probe")
    vid = await upload_video(client, admin_token, pid)
    await _fail_transcode(vid)
    r = await client.post(f"/projects/{pid}/videos/{vid}/probe/reset", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "not_resettable"


async def test_retry_needs_a_project_admin(client: AsyncClient, admin_token: str):
    """라벨러의 재시도 요청을 403으로 거절한다. 변환은 CPU 예산을 쓰는 운영 액션이다."""
    pid = await new_project(client, admin_token, "tc-retry-perm")
    vid = await upload_video(client, admin_token, pid)
    await _fail_transcode(vid)
    _, tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(
        f"/projects/{pid}/videos/{vid}/transcode/retry",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403


async def test_retry_is_scoped_to_the_project(client: AsyncClient, admin_token: str):
    """다른 프로젝트 id로 호출하면 404를 돌려준다. 경로의 프로젝트와 영상이 맞지 않으면 없는 영상으로 처리한다."""
    h = {"Authorization": f"Bearer {admin_token}"}
    pid = await new_project(client, admin_token, "tc-retry-own")
    other = await new_project(client, admin_token, "tc-retry-other")
    vid = await upload_video(client, admin_token, pid)
    await _fail_transcode(vid)
    r = await client.post(f"/projects/{other}/videos/{vid}/transcode/retry", headers=h)
    assert r.status_code == 404
