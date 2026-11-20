"""`clip_thumbnail` 서비스 단위 테스트. ffmpeg와 storage는 모두 mock으로 바꾼다.

새 MAIU 클립에 장면 썸네일을 만드는지 확인한다.
실패 재시도와 dead-letter가 동작하는지도 확인한다.
"""
from decimal import Decimal
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video
from api_gateway.services.media import clip_thumbnail, storage
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


async def _clip_with_source(client: AsyncClient, admin_token: str, name: str) -> tuple[str, str]:
    """프로젝트, source_uri가 있는 영상, 장면 썸네일이 없는 클립 1건을 만든다.

    (pid, clip_id)를 돌려준다.
    """
    pid = await new_project(client, admin_token, name)
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.source_uri = "s3://example-bucket/news/x.mp4"
        clip = Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("10.000"), end_seconds=Decimal("14.500"),
            rights_status="auto_cleared", auto_caption={"brief": "장면"}, speakers=[],
        )
        s.add(clip)
        await s.commit()
        return pid, str(clip.clip_id)


async def _is_candidate(clip_id: str) -> bool:
    """이 클립 한 건이 다음 주기의 후보이면 True. 후보 목록에 드는지로 판단하지 않는다.

    `list_pending`은 오래된 것부터 워커 배치 상한까지만 가져온다.
    그래서 후보인 클립도 앞선 후보에 밀려 목록에서 빠질 수 있다.
    이 테스트의 클립은 항상 가장 최신이라 밀리기 쉽다.
    그래서 상한·정렬을 붙이기 전의 조건(`pending_select`)만 이 행에 적용한다.
    다른 클립에 가짜 썸네일 키를 넣어 후보를 비우는 방식은 같은 DB에 있는 다른 테스트의 행을 바꾸므로 쓰지 않는다.
    """
    async with SessionLocal() as s:
        row = await s.execute(
            clip_thumbnail.pending_select().where(Clip.clip_id == UUID(clip_id))
        )
        return row.first() is not None


def _mock_ok(monkeypatch) -> None:
    """presign·추출·업로드를 성공하는 no-op으로 바꾼다."""
    monkeypatch.setattr(storage, "presign_get_for", lambda **kw: "https://ncp/x.mp4?sig=1")
    monkeypatch.setattr(storage, "put_bytes", lambda *a, **kw: None)
    monkeypatch.setattr(clip_thumbnail, "_extract_frame", lambda url, ts: b"\xff\xd8jpeg")


async def test_process_pending_sets_thumbnail_key(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """장면 썸네일이 없는 클립을 처리하면 metadata.scene_thumbnail_key가 채워진다."""
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-ok")
    _mock_ok(monkeypatch)

    async with SessionLocal() as s:
        ok = await clip_thumbnail.process_one_by_id(s, UUID(clip_id))
    assert ok is True

    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(clip_id))
        assert (c.extra or {}).get("scene_thumbnail_key") == f"clip-thumbs/{clip_id}.jpg"


async def test_process_pending_skips_clip_with_key(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """이미 썸네일 키가 있는 클립은 후보에서 빼고 다시 만들지 않는다."""
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-skip")
    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(clip_id))
        c.extra = {"scene_thumbnail_key": "clip-thumbs/existing.jpg"}
        await s.commit()

    assert not await _is_candidate(clip_id), "키가 있는데 후보로 잡혔다"


async def test_failure_retries_then_dead_letters(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """추출이 영구 오류로 3회 실패하면 thumbnail_failed로 확정하고 후보에서 뺀다.

    그래서 무한 재시도가 생기지 않는다.
    """
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-fail")
    monkeypatch.setattr(storage, "presign_get_for", lambda **kw: "https://ncp/x.mp4")
    monkeypatch.setattr(storage, "put_bytes", lambda *a, **kw: None)
    def _permanent(url, ts):
        """깨진 파일을 흉내 낸다. 영구 실패라서 재시도 상한을 차감한다."""
        raise clip_thumbnail.FrameError("moov atom not found", transient=False)

    monkeypatch.setattr(clip_thumbnail, "_extract_frame", _permanent)

    for _ in range(3):
        async with SessionLocal() as s:
            await clip_thumbnail.process_one_by_id(s, UUID(clip_id))

    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(clip_id))
        assert (c.extra or {}).get("thumbnail_retry_count") == 3
        assert (c.extra or {}).get("thumbnail_failed") == "true"
        assert "scene_thumbnail_key" not in (c.extra or {})

    assert not await _is_candidate(clip_id), "굳은 실패인데 후보로 잡혔다"


async def test_list_pending_excludes_video_without_source(
    client: AsyncClient, admin_token: str
):
    """source_uri가 없는 영상의 클립은 프레임을 추출할 원본이 없으므로 후보에서 뺀다."""
    pid = await new_project(client, admin_token, "thumb-nosrc")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.source_uri = None
        clip = Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.0"), end_seconds=Decimal("3.0"),
            rights_status="auto_cleared", auto_caption={}, speakers=[],
        )
        s.add(clip)
        await s.commit()
        cid = str(clip.clip_id)

    assert not await _is_candidate(cid), "원본이 없는데 후보로 잡혔다"


async def test_transient_failure_does_not_consume_the_cap(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """일시적인 네트워크 오류는 재시도 상한을 차감하지 않는다.

    차감하면 일시 오류가 세 번 난 클립이 다시는 썸네일을 얻지 못한다.
    이 워커는 원본을 내려받지 않고 presigned URL을 ffmpeg에 직접 넘긴다.
    그래서 요청량 제한·타임아웃·URL 만료가 정상 경로에서도 생긴다.
    이 오류를 깨진 파일과 같이 세면 3회 만에 `thumbnail_failed`로 확정된다. 그 값을 지우는 코드는 없다.
    """
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-transient")
    monkeypatch.setattr(storage, "presign_get_for", lambda **kw: "https://ncp/x.mp4")
    monkeypatch.setattr(storage, "put_bytes", lambda *a, **kw: None)

    def _transient(url, ts):
        """일시 실패(503)를 흉내 낸다."""
        raise clip_thumbnail.FrameError("Server returned 503", transient=True)

    monkeypatch.setattr(clip_thumbnail, "_extract_frame", _transient)

    for _ in range(5):  # 상한(3)보다 많이 호출한다
        async with SessionLocal() as s:
            await clip_thumbnail.process_one_by_id(s, UUID(clip_id))

    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(clip_id))
        extra = c.extra or {}
    assert extra.get("thumbnail_failed") is None, "일시 오류로 dead-letter 됐다"
    assert extra.get("thumbnail_retry_count") is None, "일시 오류가 상한을 깎았다"
    assert extra.get("thumbnail_transient_streak") == 5
    assert extra.get("thumbnail_retry_after"), "백오프 시각이 없다 — 매 주기 다시 두드린다"
    assert extra.get("thumbnail_error", "").startswith("[일시]")


async def test_transient_backoff_keeps_it_out_of_pending(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """백오프 중인 클립을 후보에서 뺀다.

    빼지 않으면 일시 오류가 계속되는 클립이 매 주기 배치 상한 8개를 모두 차지한다.
    후보는 오래된 것부터 가져오므로 그러면 새 클립이 처리되지 않는다.
    후보 여부는 `_is_candidate`로 한 건씩 확인한다. 목록으로 확인하면 이 테스트의 클립은
    항상 가장 최신이라 앞에 후보가 쌓일 때 목록에서 밀려 단언이 잘못 통과한다.
    """
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-backoff")
    monkeypatch.setattr(storage, "presign_get_for", lambda **kw: "https://ncp/x.mp4")
    monkeypatch.setattr(storage, "put_bytes", lambda *a, **kw: None)
    monkeypatch.setattr(clip_thumbnail, "_extract_frame", _raise_transient)

    assert await _is_candidate(clip_id), "처음엔 후보여야 한다"

    async with SessionLocal() as s:
        await clip_thumbnail.process_one_by_id(s, UUID(clip_id))

    assert not await _is_candidate(clip_id), \
        "일시 실패 직후인데 다시 집혔다 — 백오프가 안 걸렸다"


async def test_upload_failure_keeps_the_raw_error_off_the_screen(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """업로드 실패 원문(엔드포인트·RequestId)을 사유 키에 싣지 않는다.

    한글 접두어 뒤에 원문을 붙이면(`업로드 실패: {e}`) 화면의 오류 문구 변환이 이미 번역된 문장으로 판단해 원문을 그대로 표시한다.
    원문은 로그에만 남긴다.
    """
    _, clip_id = await _clip_with_source(client, admin_token, "thumb-upload-raw")
    monkeypatch.setattr(storage, "presign_get_for", lambda **kw: "https://ncp/x.mp4")
    monkeypatch.setattr(clip_thumbnail, "_extract_frame", lambda url, ts: b"\xff\xd8jpeg")

    def _denied(*a, **kw):
        """권한 거부를 흉내 낸다. boto 원문 형태라서 엔드포인트·RequestId가 섞여 있다."""
        raise RuntimeError(
            "An error occurred (AccessDenied) when calling the PutObject operation: "
            "https://kr.object.ncloudstorage.com RequestId abc123"
        )

    monkeypatch.setattr(storage, "put_bytes", _denied)
    async with SessionLocal() as s:
        await clip_thumbnail.process_one_by_id(s, UUID(clip_id))
    async with SessionLocal() as s:
        c = await s.get(Clip, UUID(clip_id))
        said = (c.extra or {}).get("thumbnail_error", "")
    assert said.endswith("썸네일을 저장소에 올리지 못했습니다"), said
    assert "AccessDenied" not in said and "RequestId" not in said


def _raise_transient(url, ts):
    """일시 실패를 내는 `_extract_frame` 대역."""
    raise clip_thumbnail.FrameError("Connection timed out", transient=True)


def test_stored_ffmpeg_line_drops_the_presigned_signature():
    """저장하는 ffmpeg 마지막 줄에서 주소의 쿼리스트링(서명·접근 키)을 지운다.

    실패 사유는 DB에 남고 화면·내보내기로 다시 나간다. 서명이 남으면 유효 기간 동안
    그 줄을 읽은 사람이 객체를 읽을 수 있다.
    """
    stderr = ("ffmpeg version 6.1\n"
              "[https @ 0x1] HTTP error 403 Forbidden\n"
              "https://kr.object.ncloudstorage.com/b/k.mp4?X-Amz-Credential=AKIAXXXX%2F2026"
              "&X-Amz-Signature=deadbeef: Server returned 403 Forbidden (access denied)\n")
    line = clip_thumbnail._last_ffmpeg_line(stderr)
    assert "403" in line
    assert "AKIA" not in line and "Signature" not in line and "?" in line
