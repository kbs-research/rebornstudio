"""분석본 정리 테스트. 파생물은 다시 만들 수 있는 캐시라서 지울 수 있다.

네 가지를 확인한다.
  1. 원본이 있을 때만 지운다. 원본이 없으면 분석본이 유일본이라 지우면 되돌릴 수 없다.
  2. 분석이 끝난 뒤에만 지운다. `done`이 아니면 그 사본을 아직 입력으로 쓴다.
  3. URI도 함께 비운다. 남기면 없는 파일을 MI 입력으로 고른다.
  4. 정리 시각과 감사 기록을 남긴다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, DomainEvent, Video
from api_gateway.services.analysis.status_map import neutral_status
from api_gateway.services.media import storage
from httpx import AsyncClient
from sqlalchemy import select, update
from tests import _storage
from tests._helpers import new_project, upload_video

_KEY = "transcoded/720p/{}.mp4"


async def _with_analysis(client, token, pid, title, *, mi="done", retained=True) -> str:
    """분석본이 실제로 있는 영상을 하나 만든다. 버킷에도 객체를 넣는다."""
    vid = await upload_video(client, token, pid, title=title)
    key = _KEY.format(vid)
    _storage.put_bytes(key, b"\x00" * 256, content_type="video/mp4")
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        bucket = v.source_uri.split("://")[1].split("/", 1)[0]
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            analysis_uri=f"s3://{bucket}/{key}", source_retained=retained))
        if mi:
            s.add(AnalysisJob(adapter_key="maiu", video_id=UUID(vid), project_id=UUID(pid),
                              stage=mi, status=neutral_status(mi)))
        await s.commit()
    return vid


async def _purge(client, token, vid):
    """영상의 분석본 정리를 요청한다."""
    return await client.delete(f"/videos/{vid}/artifacts/analysis",
                               headers={"Authorization": f"Bearer {token}"})


async def test_purges_and_clears_the_uri(client: AsyncClient, admin_token: str):
    """분석본을 지우면 URI도 비운다. 남기면 `resolver`가 없는 파일을 MI 입력으로 고른다.

    URI만 남으면 DB 기록과 실제 객체가 서로 다르다.
    URI를 비우면 폴백이 원본을 가리키므로 다시 분석할 수 있다.
    """
    pid = await new_project(client, admin_token, "purge-ok")
    vid = await _with_analysis(client, admin_token, pid, "a")

    r = await _purge(client, admin_token, vid)
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == 256

    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        bucket = v.source_uri.split("://")[1].split("/", 1)[0]
        assert v.analysis_uri is None, "URI를 안 비우면 없는 파일을 가리킨다"
    assert storage.head_in(bucket=bucket, key=_KEY.format(vid)) is None


async def test_refuses_when_original_is_gone(client: AsyncClient, admin_token: str):
    """원본이 없으면 분석본이 유일본이므로 정리를 409로 거절한다. 그 분석본을 읽는 코드가 지금 없어도 거절한다.

    다시 만들 방법이 없으므로 이 경우 삭제는 폐기에 해당한다.
    """
    pid = await new_project(client, admin_token, "purge-only")
    vid = await _with_analysis(client, admin_token, pid, "b", retained=False)

    r = await _purge(client, admin_token, vid)
    assert r.status_code == 409
    assert "유일한 사본" in r.json()["detail"], r.json()
    async with SessionLocal() as s:
        assert (await s.get(Video, UUID(vid))).analysis_uri is not None


async def test_refuses_while_analysis_is_not_done(client: AsyncClient, admin_token: str):
    """분석이 `done`이 아니면 그 사본은 아직 입력이므로 거절한다.

    실패한 분석도 포함한다. 재시도가 이 사본을 쓴다.
    """
    pid = await new_project(client, admin_token, "purge-inflight")
    for i, mi in enumerate(("analyzing", "failed")):
        vid = await _with_analysis(client, admin_token, pid, f"c{i}", mi=mi)
        r = await _purge(client, admin_token, vid)
        assert r.status_code == 409, mi
        assert "분석이 끝난 뒤" in r.json()["detail"]


async def test_annotator_cannot_purge(client: AsyncClient, admin_token: str):
    """라벨러의 정리 요청을 403으로 거절한다. 정리는 되돌릴 수 없는 액션이라 관리자만 할 수 있다."""
    from tests._helpers import add_member

    pid = await new_project(client, admin_token, "purge-authz")
    vid = await _with_analysis(client, admin_token, pid, "e")
    _, tok = await add_member(client, admin_token, pid, "annotator")

    assert (await _purge(client, tok, vid)).status_code == 403


async def test_purge_leaves_a_record_and_hides_the_button(
    client: AsyncClient, admin_token: str
):
    """정리하면 정리 시각과 감사 기록을 남기고, 영상 응답의 분석본 표시를 끈다.

    정리 시각(`analysis_purged_at`)은 분석본이 없는 상태가 정리인지 유실인지 가르는 근거다.
    영상 응답의 `has_analysis_copy`가 꺼지지 않으면 화면이 없는 사본의 정리 버튼을 계속 보인다.
    """
    pid = await new_project(client, admin_token, "purge-record")
    vid = await _with_analysis(client, admin_token, pid, "f")
    hdr = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.get(f"/videos/{vid}", headers=hdr)).json()["has_analysis_copy"] is True

    assert (await _purge(client, admin_token, vid)).status_code == 200

    assert (await client.get(f"/videos/{vid}", headers=hdr)).json()["has_analysis_copy"] is False
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert v.extra.get("analysis_purged_at"), "정리와 유실을 가를 기록이 없다"
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "media.analysis_purged",
            DomainEvent.target_id == UUID(vid)))).all()
    assert len(ev) == 1 and ev[0].payload["freed_bytes"] == 256, "정리한 사실이 감사 기록에 없다"
