"""비식별화가 실패한 영상을 막힘으로 표시하는지 확인하는 테스트.

필수 단계가 실패한 영상을 처리할 일이 없는 영상으로 표시하지 않는다.
`_ingest_bucket_case`에 `deident` 분기가 없으면 비식별화를 켜 놓고 실패한 영상이
`ready`(처리할 일 없음)로 분류된다. 그래서 바로 위 `transcode`와 같은 분기를 둔다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from httpx import AsyncClient
from tests._helpers import add_member, new_project, upload_video


async def _set(vid: str, **cols) -> None:
    """영상 컬럼을 주어진 값으로 바꾼다."""
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        for k, x in cols.items():
            setattr(v, k, x)
        await s.commit()


async def _row(client: AsyncClient, token: str, pid: str, vid: str) -> dict:
    """영상 목록에서 이 영상의 행을 찾는다."""
    r = await client.get(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    rows = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    return next(x for x in rows if x["video_id"] == vid)


async def test_a_failed_deident_is_blocked_not_ready(client: AsyncClient, admin_token: str):
    """비식별화를 켜 놓고 처리하지 못한 영상을 막힘으로 분류한다. `ready`로 분류하면 아무도 처리하지 않는다."""
    pid = await new_project(client, admin_token, "deid-blocked")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="failed")
    row = await _row(client, admin_token, pid, vid)
    assert row["ingest_bucket"] == "blocked"
    # 막힌 단계도 표시한다. 해시만 보고 조치하게 하지 않는다.
    assert row.get("prep_stage") == "blocked"
    assert row.get("prep_blocked_stage") == "deident"


async def test_deident_in_flight_is_preparing(client: AsyncClient, admin_token: str):
    """비식별화 처리 중인 영상을 준비 중으로 분류한다. 기다리면 끝나므로 변환과 같게 처리한다."""
    pid = await new_project(client, admin_token, "deid-inflight")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="running")
    assert (await _row(client, admin_token, pid, vid))["ingest_bucket"] == "preparing"


async def test_skipped_is_not_blocked(client: AsyncClient, admin_token: str):
    """비식별화를 쓰지 않는 영상(`skipped`)은 막힘으로 분류하지 않는다. 기다릴 것도 처리할 것도 없다."""
    pid = await new_project(client, admin_token, "deid-skipped")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="skipped")
    assert (await _row(client, admin_token, pid, vid))["ingest_bucket"] != "blocked"


# ── 운영자 재시도 ────────────────────────────────────────────────────
#
# 막힌 영상은 운영자가 조치할 수 있어야 한다. 완료된 영상은 다시 돌릴 수 없다.
# 마스킹본이 마스터를 대체했으므로 다시 돌리면 이미 가린 영상을 또 가린다.

async def _retry(client: AsyncClient, token: str, pid: str, vid: str):
    """비식별화 재시도를 요청한다."""
    return await client.post(f"/projects/{pid}/videos/{vid}/deident/retry",
                             headers={"Authorization": f"Bearer {token}"})


async def test_an_operator_can_requeue_a_failed_one(client: AsyncClient, admin_token: str):
    """실패한 영상을 다시 큐에 넣고 시도 횟수를 0으로 되돌린다.

    되돌리지 않으면 상한(3회)에 걸린 상태라서 다음 실패 한 번에 바로 최종 실패가 된다.
    그러면 재시도 기회가 사실상 없다.
    """
    pid = await new_project(client, admin_token, "deid-retry")
    vid = await upload_video(client, admin_token, pid)
    # 비식별화가 실패하는 것은 켠 프로젝트뿐이다. 끈 프로젝트의 실패는 다시 넣지 않는다.
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        await s.commit()
    await _set(vid, transcode_status="done", deident_status="failed", deident_attempts=3,
               extra={"deident_error": "[영구] 모델 서버 응답 없음"})
    assert (await _retry(client, admin_token, pid, vid)).status_code == 204
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        assert v.deident_status == "pending" and v.deident_attempts == 0
        # 실패 기록을 지운다. 남기면 다음 화면이 지금도 실패 중이라고 표시한다.
        assert "deident_error" not in (v.extra or {})
    assert (await _row(client, admin_token, pid, vid))["ingest_bucket"] == "preparing"


async def test_a_finished_one_is_refused(client: AsyncClient, admin_token: str):
    """완료된 영상의 재시도를 서버에서 409로 거절한다. 화면에서 버튼만 숨기면 API로 재시도할 수 있다.

    마스킹본이 마스터를 대체했으므로 다시 돌리면 이미 가린 영상을 또 가린다.
    화질만 떨어지고 검출률이 0에 가깝게 나와 이력에 얼굴이 없었다고 잘못 기록된다.
    """
    pid = await new_project(client, admin_token, "deid-done")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="done")
    r = await _retry(client, admin_token, pid, vid)
    assert r.status_code == 409 and r.json()["detail"] == "deident_not_retryable"


async def test_one_in_flight_is_refused(client: AsyncClient, admin_token: str):
    """처리 중인 영상의 재시도도 409로 거절한다.

    재시도를 받으면 같은 영상에 잡이 두 개 돈다.
    """
    pid = await new_project(client, admin_token, "deid-running")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="running")
    assert (await _retry(client, admin_token, pid, vid)).status_code == 409


async def test_only_operators_requeue(client: AsyncClient, admin_token: str):
    """재시도는 운영자만 한다. 라벨러의 재시도 요청은 403으로 거절한다."""
    pid = await new_project(client, admin_token, "deid-role")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="failed")
    _, tok = await add_member(client, admin_token, pid, "annotator")
    assert (await _retry(client, tok, pid, vid)).status_code == 403
