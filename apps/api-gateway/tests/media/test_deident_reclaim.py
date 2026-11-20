"""비식별화·재생본 잡 회수와 회수 뒤 처리 테스트.

배포로 워커가 다시 만들어지면 회수 작업이 리스가 끝난 잡을 대기로 돌린다. 세 가지를 확인한다.

  1. 회수가 그 시도의 단계 이력을 닫는다. 닫지 않으면 다음 시도가 시작될 때까지 준비 타임라인이
     아무도 처리하지 않는 시도를 "진행 중"으로 표시한다.
  2. 회수된 영상을 먼저 다시 보낸다. 이미 한 번 차례를 받은 영상이 대기 줄 끝으로 밀리지 않게 한다.
  3. 회수한 시도의 완료 보고가 늦게 오면 산출 근거가 맞을 때만 새 토큰으로 이어받는다.
"""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import MediaStageRun, Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media import stage_runs
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import new_project, upload_video


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _seed(client: AsyncClient, token: str, name: str) -> uuid.UUID:
    """비식별화를 켠 프로젝트에 720p 영상을 올리고 비식별화 대기까지 연다."""
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


async def _dispatch(vid: uuid.UUID) -> dict:
    """비식별화 잡을 만들어 이 영상의 잡을 돌려준다."""
    async with SessionLocal() as s:
        jobs = await deident_service.build_jobs(s)
    return next(j for j in jobs if j["video_id"] == str(vid))


async def _expire(vid: uuid.UUID, stage: str) -> None:
    """그 단계의 리스를 지난 시각으로 돌린다. 워커가 비정상 종료된 상태와 같다."""
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        setattr(v, f"{stage}_lease_until", datetime.now(UTC) - timedelta(seconds=1))
        await s.commit()


async def _runs(vid: uuid.UUID, stage: str) -> list[MediaStageRun]:
    """그 단계의 이력을 오래된 것부터 돌려준다."""
    async with SessionLocal() as s:
        return list((await s.scalars(
            select(MediaStageRun)
            .where(MediaStageRun.video_id == vid, MediaStageRun.stage == stage)
            .order_by(MediaStageRun.id)
        )).all())


async def test_deident_reclaim_closes_the_stage_run(client: AsyncClient, admin_token: str):
    """회수한 비식별화 시도를 단계 이력에서 일시 오류로 닫는다. 그래서 "진행 중"으로 남지 않는다."""
    vid = await _seed(client, admin_token, "deid-reclaim-close")
    await _dispatch(vid)
    await _expire(vid, "deident")
    async with SessionLocal() as s:
        assert await deident_service.reclaim_expired(s) >= 1

    runs = await _runs(vid, "deident")
    assert [r.status for r in runs] == ["failed"], [r.status for r in runs]
    assert runs[0].finished_at is not None
    assert runs[0].detail.get("transient") is True
    assert runs[0].detail.get("reclaimed") is True
    assert "리스" not in (runs[0].error or ""), "설계 용어가 화면 사유에 샜다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "pending"
        assert v.deident_attempts == 0, "회수는 시도를 소모하지 않는다"


async def test_publish_reclaim_closes_the_stage_run(client: AsyncClient, admin_token: str):
    """재생본 회수에도 같은 규칙을 적용한다. 회수한 시도를 닫아 타임라인에 "진행 중"으로 남기지 않는다."""
    pid = await new_project(client, admin_token, "pub-reclaim-close")
    vid = uuid.UUID(await upload_video(client, admin_token, pid))
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.publish_status = "running"
        v.publish_token = "t-old"
        v.publish_attempts = 1
        v.publish_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await stage_runs.start(s, vid, "publish", detail={"from": "x"})
        await s.commit()
    async with SessionLocal() as s:
        assert await publish_service.reclaim_expired(s) >= 1

    runs = await _runs(vid, "publish")
    assert [r.status for r in runs] == ["failed"]
    assert runs[0].detail.get("transient") is True
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).publish_status == "pending"


async def test_reclaim_leaves_a_marker_with_the_old_token(client: AsyncClient, admin_token: str):
    """회수한 영상에는 지운 토큰과 그 시도의 시작 시각이 남는다. 늦은 보고를 확인할 근거다."""
    vid = await _seed(client, admin_token, "deid-reclaim-marker")
    job = await _dispatch(vid)
    await _expire(vid, "deident")
    async with SessionLocal() as s:
        await deident_service.reclaim_expired(s)
    async with SessionLocal() as s:
        marker = (await s.get(Video, vid)).extra.get("deident_reclaimed")
    assert marker and marker["token"] == job["token"]
    started = (await _runs(vid, "deident"))[0].started_at
    assert datetime.fromisoformat(marker["dispatched_at"]) == started


async def test_reclaimed_video_goes_before_older_waiting_ones(
    client: AsyncClient, admin_token: str,
):
    """회수돼 돌아온 영상을 대기 영상보다 먼저 보낸다.

    상한이 1이라 한 번에 한 영상만 나간다. 그래서 회수된 영상이 먼저 올라온 대기 영상들 뒤로
    밀리면 리스가 끝날 때까지 기다린 시간에 대기 줄 전체를 다시 기다린다.
    """
    older = await _seed(client, admin_token, "deid-prio-older")
    newer = await _seed(client, admin_token, "deid-prio-newer")
    # 먼저 올라온 영상을 잠시 대기에서 빼 둔다. 나중 영상이 먼저 나가 회수되게 한다.
    async with SessionLocal() as s:
        (await s.get(Video, older)).deident_status = "skipped"
        await s.commit()
    await _dispatch(newer)
    await _expire(newer, "deident")
    async with SessionLocal() as s:
        await deident_service.reclaim_expired(s)
        (await s.get(Video, older)).deident_status = "pending"
        await s.commit()

    async with SessionLocal() as s:
        jobs = await deident_service.build_jobs(s)
    sent = [j["video_id"] for j in jobs]
    assert sent and sent[0] == str(newer), f"회수된 영상이 대기 줄 뒤로 밀렸다: {sent}"
    async with SessionLocal() as s:
        v = await s.get(Video, newer)
        assert v.deident_status == "running"
        assert "deident_reclaimed" not in v.extra, "다시 보낸 뒤에도 회수 표식이 남았다"


def _heads(monkeypatch, *, output: datetime | None, source: datetime) -> None:
    """저장소 조회를 대역으로 바꾼다. 스테이징 mp4는 `output`, 그 밖의 키는 `source` 시각이다."""
    def _head(*, bucket: str, key: str):
        """준비한 시각으로 객체 정보를 돌려준다. 시각이 없으면 객체가 없다."""
        at = output if key.endswith(".deid.mp4") else (
            None if key.endswith(".faces.json") else source)
        return None if at is None else SimpleNamespace(last_modified=at)

    monkeypatch.setattr("api_gateway.services.media.deident.storage.head_in", _head)
    monkeypatch.setattr("api_gateway.services.media.deident.storage.copy_object",
                        lambda **kw: None)
    monkeypatch.setattr("api_gateway.services.media.deident.storage.delete_object",
                        lambda **kw: None)


async def _reclaimed(client: AsyncClient, token: str, name: str) -> tuple[uuid.UUID, str, datetime]:
    """잡을 한 번 보내고 회수까지 마친 영상을 만든다. (영상, 이전 토큰, 그 시도를 보낸 시각)을 돌려준다."""
    vid = await _seed(client, token, name)
    job = await _dispatch(vid)
    await _expire(vid, "deident")
    async with SessionLocal() as s:
        await deident_service.reclaim_expired(s)
        marker = (await s.get(Video, vid)).extra["deident_reclaimed"]
    return vid, job["token"], datetime.fromisoformat(marker["dispatched_at"])


_REPORT = {"model": "face-anonymizer", "masked": True, "detection_rate": 0.9}


async def test_late_result_of_the_reclaimed_attempt_is_adopted(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """회수한 시도의 결과가 늦게 와도 산출이 그 시도에서 나왔으면 이어받는다.

    배포 전에 받아 둔 메시지를 새 워커가 이전 토큰으로 처리하면 산출을 올린 뒤 이전 토큰으로 보고한다.
    펜싱이 이 보고를 버리면 오래 걸려 만든 결과가 사라진다.
    """
    vid, old, sent_at = await _reclaimed(client, admin_token, "deid-late-adopt")
    _heads(monkeypatch, output=sent_at + timedelta(minutes=15),
           source=sent_at - timedelta(hours=1))
    async with SessionLocal() as s:
        ok = await deident_service.complete(
            s, vid, old, ok=True, elapsed_s=3.6, report=dict(_REPORT))
    assert ok is True, "근거가 맞는데 버렸다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "done"
        assert v.deident_token is None
        assert "deident_reclaimed" not in v.extra
        assert v.extra["deident"]["adopted"]["from_token"] == old[:8]
        assert v.publish_status == "pending", "다음 단계가 열려야 한다"
    runs = await _runs(vid, "deident")
    assert [r.status for r in runs] == ["failed", "ok"], "이어받은 시도가 따로 기록돼야 한다"
    assert runs[1].detail.get("adopted")


async def test_late_result_without_evidence_is_still_discarded(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """산출이 그 시도를 보내기 전에 올라와 있었으면 이어받지 않는다. 앞선 시도의 파일일 수 있다."""
    vid, old, sent_at = await _reclaimed(client, admin_token, "deid-late-noevidence")
    _heads(monkeypatch, output=sent_at - timedelta(minutes=1),
           source=sent_at - timedelta(hours=1))
    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, old, ok=True, elapsed_s=3.6, report=dict(_REPORT)) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "pending"
        assert v.deident_token is None
    assert [r.status for r in await _runs(vid, "deident")] == ["failed"]


async def test_late_result_does_not_override_a_newer_attempt(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """이미 새 시도를 보냈으면 이전 토큰의 보고를 받지 않는다. 새 시도가 처리를 다시 한다."""
    vid, old, sent_at = await _reclaimed(client, admin_token, "deid-late-newer")
    _heads(monkeypatch, output=sent_at + timedelta(minutes=15),
           source=sent_at - timedelta(hours=1))
    newer = await _dispatch(vid)
    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, old, ok=True, elapsed_s=3.6, report=dict(_REPORT)) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "running"
        assert v.deident_token == newer["token"]


async def test_unknown_stale_token_is_discarded(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """회수 표식에 없는 토큰의 보고를 받지 않는다. 더 앞선 시도의 보고다."""
    vid, _, sent_at = await _reclaimed(client, admin_token, "deid-late-unknown")
    _heads(monkeypatch, output=sent_at + timedelta(minutes=15),
           source=sent_at - timedelta(hours=1))
    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, "f" * 32, ok=True, elapsed_s=3.6, report=dict(_REPORT)) is False
    async with SessionLocal() as s:
        assert (await s.get(Video, vid)).deident_status == "pending"
