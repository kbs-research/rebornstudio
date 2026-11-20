"""썸네일 다시 만들기 테스트.

엔드포인트는 `POST /projects/{pid}/videos/{vid}/thumbnails/retry`다.
상한에 걸려 영구 실패가 된 썸네일(`*_failed`)의 표식은 이 경로로만 지울 수 있다.
세 가지를 확인한다.
  1. 영구 실패 표식을 지우면 다음 주기 후보에 다시 들어가고 워커가 가져간다.
  2. 영구 실패가 없으면 409로 거절한다. 재시도 중인 썸네일은 워커가 다시 처리한다.
  3. 클립 DTO는 영구 실패만 사유와 함께 싣는다. 화면은 이 값으로 처리 중인지
     최종 실패인지 구분한다.
"""
from decimal import Decimal
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import Clip, DomainEvent, Video
from api_gateway.services.media import clip_thumbnail, video_thumbnail
from api_gateway.services.media.clip import merge_extra as clip_merge
from api_gateway.services.media.clip import thumbnail_reason
from api_gateway.services.media.video import merge_extra as video_merge
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, new_project, upload_video


async def _stuck(client: AsyncClient, admin_token: str, name: str) -> tuple[str, str, str]:
    """포스터와 클립 썸네일이 모두 상한에 걸려 영구 실패한 영상을 만들고 (pid, vid, clip_id)를 돌려준다."""
    pid = await new_project(client, admin_token, name)
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        clip = Clip(
            video_id=UUID(vid), project_id=UUID(pid),
            start_seconds=Decimal("0.000"), end_seconds=Decimal("5.000"),
            rights_status="auto_cleared", auto_caption={}, speakers=[],
        )
        s.add(clip)
        await s.flush()
        await clip_merge(s, clip.clip_id, {
            "thumbnail_failed": "true", "thumbnail_retry_count": 3,
            "thumbnail_error": "moov atom not found",
        })
        await video_merge(s, UUID(vid), {
            "poster_failed": "true", "poster_retry_count": 3, "poster_error": "ffmpeg 추출 실패",
        })
        await s.commit()
        return pid, vid, str(clip.clip_id)


async def test_the_dto_says_which_thumbnails_are_stuck(client: AsyncClient, admin_token: str):
    """클립 DTO에 영구 실패를 사유와 함께 싣는다.

    화면은 이 값으로 빈 썸네일이 처리 중인지 최종 실패인지 구분한다.
    """
    pid, vid, cid = await _stuck(client, admin_token, "th-dto")
    rows = (await client.get(f"/projects/{pid}/videos/{vid}/clips",
                             headers={"Authorization": f"Bearer {admin_token}"})).json()
    mine = next(r for r in rows if r["clip_id"] == cid)
    assert mine["thumbnail_error"], "굳은 실패가 DTO에 없다"
    assert "moov" not in mine["thumbnail_error"], "ffmpeg 원문이 화면 사유로 나간다"


async def test_retry_clears_the_marks_and_the_worker_picks_them_up(
    client: AsyncClient, admin_token: str,
):
    """다시 만들기를 요청하면 표식을 지워 다음 주기 후보에 다시 넣고 감사 기록을 남긴다."""
    pid, vid, cid = await _stuck(client, admin_token, "th-retry")
    r = await client.post(f"/projects/{pid}/videos/{vid}/thumbnails/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 204, r.text

    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        c = await s.get(Clip, UUID(cid))
        assert "poster_failed" not in (v.extra or {})
        assert "thumbnail_failed" not in (c.extra or {})
        assert "thumbnail_retry_count" not in (c.extra or {}), "카운터가 남으면 곧 다시 굳는다"
        # 후보 여부는 이 행에만 후보 조건을 적용해 확인한다.
        # `list_pending`은 오래된 것부터 워커 배치 상한까지만 가져간다.
        # 테스트 실행 중 쌓인 이전 후보가 상한을 채우면 가장 최신인 이 영상이 목록에서 빠진다.
        # 상한은 워커가 한 주기에 가져가는 양이라 제품 설정이므로 테스트를 위해 올리지 않는다.
        clip_candidate = (await s.execute(
            clip_thumbnail.pending_select().where(Clip.clip_id == UUID(cid))
        )).first()
        poster_candidate = (await s.execute(
            video_thumbnail.pending_select().where(Video.video_id == UUID(vid))
        )).first()
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.target_id == UUID(vid),
            DomainEvent.event_type == "video.thumbnails_retried",
        ))).first()
    assert clip_candidate is not None, "걷었는데 클립 썸네일 후보가 아니다"
    assert poster_candidate is not None, "걷었는데 포스터 후보가 아니다"
    assert ev is not None and ev.payload == {"poster": True, "clips": 1}


async def test_nothing_stuck_is_a_conflict(client: AsyncClient, admin_token: str):
    """영구 실패가 없으면 409로 거절한다. 재시도 중인 썸네일은 워커가 다시 처리한다."""
    pid = await new_project(client, admin_token, "th-none")
    vid = await upload_video(client, admin_token, pid)
    r = await client.post(f"/projects/{pid}/videos/{vid}/thumbnails/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 409 and r.json()["detail"] == "thumbnail_not_failed"


async def test_retry_needs_a_project_admin(client: AsyncClient, admin_token: str):
    """라벨러의 다시 만들기 요청을 403으로 거절한다. ffmpeg를 다시 돌리는 운영 액션이다."""
    pid, vid, _ = await _stuck(client, admin_token, "th-perm")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    r = await client.post(f"/projects/{pid}/videos/{vid}/thumbnails/retry",
                          headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_thumbnail_reason_keeps_our_words_and_hides_ffmpeg_lines():
    """한글 사유는 그대로 두고, 영문 ffmpeg 원문은 기능 설명으로 바꾼다.

    한글 사유는 이 코드가 만든 문구이고 영문은 ffmpeg 원문이다.
    분류기(`operator_text`)를 쓰지 않는 이유도 테스트로 보장한다.
    분류기는 분류하지 못한 원문에 "원문은 서버 기록에 남아 있습니다"라고 쓴다.
    썸네일 원문은 감사 기록에 없고 `extra`에 있다.
    """
    assert thumbnail_reason("스토리지 업로드 실패") == "스토리지 업로드 실패"
    assert thumbnail_reason("[일시] 프레임 추출 90초 초과") == "프레임 추출 90초 초과"
    assert thumbnail_reason("Server returned 403 Forbidden (access denied)") == "프레임을 추출하지 못했습니다"
    assert "서버 기록" not in (thumbnail_reason("moov atom not found") or "")
    assert thumbnail_reason("") is None
    assert thumbnail_reason(None) is None
