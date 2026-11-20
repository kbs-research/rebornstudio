"""비식별화를 켰는데 가리지 않은 영상을 외부 분석에 보내지 않고 다시 가릴 수 있게 하는지 확인하는 테스트.

`skipped` 한 값이 "쓰지 않기로 했다"와 "아직 하지 않았다"를 함께 뜻한다.
컬럼 기본값이 `skipped`라서 얼굴 가리기를 켜기 전에 올라온 영상은 `skipped`로 남는다.
MI 게이트가 이 값을 통과시키면 마스킹되지 않은 파일이 벤더로 나간다.
그래서 이 파일의 모든 검사는 프로젝트가 비식별화를 켰는지를 함께 확인한다.
막기만 하면 그 영상을 분석할 수 없으므로 재실행 경로를 함께 둔다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


async def _set(vid: str, **cols) -> None:
    """영상 컬럼을 주어진 값으로 바꾼다."""
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        for k, x in cols.items():
            setattr(v, k, x)
        await s.commit()


async def _deident_on(pid: str, on: bool = True) -> None:
    """프로젝트의 비식별화 스위치를 정한다."""
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = on
        await s.commit()


async def _row(client: AsyncClient, token: str, pid: str, vid: str) -> dict:
    """영상 목록에서 이 영상의 행을 찾는다."""
    r = await client.get(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["items"] if isinstance(body, dict) else body
    return next(x for x in rows if x["video_id"] == vid)


async def _retry(client: AsyncClient, token: str, pid: str, vid: str):
    """비식별화 재시도를 요청한다."""
    return await client.post(
        f"/projects/{pid}/videos/{vid}/deident/retry",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── 막는 쪽 ──────────────────────────────────────────────────────────────────

async def test_unmasked_is_blocked_not_waiting(client: AsyncClient, admin_token: str):
    """가리지 않은 영상을 막힘으로 분류한다. 요청 대기로 분류하면 누군가 분석 요청을 누른다."""
    pid = await new_project(client, admin_token, "deid-unmasked")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="done",
               publish_status="done", deident_status="skipped")

    row = await _row(client, admin_token, pid, vid)
    assert row["ingest_bucket"] == "blocked"
    assert row["prep_stage"] == "blocked"
    # 실패와 다른 값이다. 실패는 시도했지만 처리하지 못한 상태이고, `deident_unmasked`는 시도한 적이 없는 상태다.
    assert row["prep_blocked_stage"] == "deident_unmasked"


async def test_off_project_is_untouched(client: AsyncClient, admin_token: str):
    """비식별화를 켜지 않은 프로젝트의 영상은 막지 않는다.

    쓰지 않기로 한 프로젝트까지 막으면 그 프로젝트의 분석이 진행되지 않는다.
    """
    pid = await new_project(client, admin_token, "deid-off")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="done",
               publish_status="done", deident_status="skipped")
    assert (await _row(client, admin_token, pid, vid))["ingest_bucket"] != "blocked"


async def test_still_uploading_is_not_called_unmasked(client: AsyncClient, admin_token: str):
    """아직 비식별화 단계에 가지 않은 영상을 가리지 않은 영상으로 분류하지 않는다.

    기본값이 `skipped`라서 업로드 중인 영상도 같은 값이다. 그래서 분류에서 이 조건을 맨 끝에 둔다.
    위로 올리면 업로드 중인 영상이 모두 막힘이 된다.
    """
    pid = await new_project(client, admin_token, "deid-early")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, upload_status="pending", deident_status="skipped")
    row = await _row(client, admin_token, pid, vid)
    assert row["prep_blocked_stage"] != "deident_unmasked"


async def test_gate_does_not_dispatch_it(client: AsyncClient, admin_token: str):
    """분석 요청 게이트도 가리지 않은 영상을 보내지 않는다. 게이트와 화면이 다른 조건을 쓰면 화면은 막힘인데 영상이 나간다."""
    pid = await new_project(client, admin_token, "deid-gate")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="done",
               publish_status="done", deident_status="skipped")

    r = await client.post(
        f"/projects/{pid}/maiu/dispatch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"video_ids": [vid]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 0 and body["created"] == [], "마스킹 안 된 영상을 벤더에 보냈다"


# ── 여는 쪽 ──────────────────────────────────────────────────────────────────

async def test_it_can_be_masked_now(client: AsyncClient, admin_token: str):
    """가리지 않은 영상을 재시도로 다시 가릴 수 있다. 막기만 하면 그 영상을 분석할 수 없다."""
    pid = await new_project(client, admin_token, "deid-rerun")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="done",
               publish_status="done", deident_status="skipped")

    r = await _retry(client, admin_token, pid, vid)
    assert r.status_code == 204, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        assert v.deident_status == "pending"
        # 재생본도 다시 만든다. 재생본은 가리기 전 마스터에서 만들었다.
        # 그대로 두면 마스터만 가려지고 사람이 실제로 보는 480p에 얼굴이 남는다.
        assert v.publish_status == "skipped", "재생본을 가리기 전 것으로 남겨 뒀다"


async def test_off_project_cannot_rerun(client: AsyncClient, admin_token: str):
    """비식별화를 끈 프로젝트의 재실행은 409로 거절한다. 쓰지 않을 결과에 GPU 비용이 든다."""
    pid = await new_project(client, admin_token, "deid-rerun-off")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="skipped")
    r = await _retry(client, admin_token, pid, vid)
    assert r.status_code == 409 and r.json()["detail"] == "deident_not_retryable"


async def test_gone_source_is_refused_before_queueing(client: AsyncClient, admin_token: str):
    """원본이 없으면 큐에 넣기 전에 거절한다.

    넣으면 다운로드 단계에서야 실패한다. 그러면 원인과 달리 비식별화 실패로 표시된다.
    """
    pid = await new_project(client, admin_token, "deid-gone")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="skipped", source_retained=False)
    r = await _retry(client, admin_token, pid, vid)
    assert r.status_code == 409 and r.json()["detail"] == "deident_source_gone"


async def test_timeline_says_it_is_rerunnable(client: AsyncClient, admin_token: str):
    """켜기 전에 처리된 영상의 비식별화 단계를 `needed`로 표시한다. 실행해야 넘어가는 단계다.

    화면은 `deident_enabled`를 받지 않는다. 그래서 서버가 상태와 실행 가능 여부를 돌려준다.
    화면은 그 값으로 진행을 막는 단계를 무해한 건너뜀과 다른 모양으로 그리고 실행 버튼을 표시한다.
    """
    pid = await new_project(client, admin_token, "deid-tl")
    await _deident_on(pid)
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="skipped")

    r = await client.get(f"/videos/{vid}/prep-timeline",
                         headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    e = next(x for x in entries if x["stage"] == "deident")
    assert e["status"] == "needed" and e["retryable"] is True
    # 사유도 함께 돌려준다. 버튼만 있고 이유가 없으면 누를 근거가 없다.
    # 건너뛴 이유가 아니므로 `note`에 싣고 `skipped_reason`은 비운다.
    assert "켜기 전에 처리된" in (e["note"] or "")
    assert e["skipped_reason"] is None
    # 뒤에서 기다리는 분석 단계에는 기다리는 이유를 싣는다. 분석은 비식별화가 끝나야 요청된다.
    register = next(x for x in entries if x["stage"] == "register")
    assert register["status"] == "pending"
    assert register["note"] == "앞 단계가 끝나야 진행합니다."


async def test_off_project_timeline_has_no_handle(client: AsyncClient, admin_token: str):
    """쓰지 않는 프로젝트에는 재실행 버튼을 표시하지 않는다. 없는 다음 단계를 만들어 내지 않는다."""
    pid = await new_project(client, admin_token, "deid-tl-off")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, transcode_status="done", deident_status="skipped")
    r = await client.get(f"/videos/{vid}/prep-timeline",
                         headers={"Authorization": f"Bearer {admin_token}"})
    e = next(x for x in r.json()["entries"] if x["stage"] == "deident")
    assert e["status"] == "skipped" and e["retryable"] is False
