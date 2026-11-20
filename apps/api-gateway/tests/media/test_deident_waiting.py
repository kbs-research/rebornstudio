"""비식별화를 켠 프로젝트에 새로 올린 영상이 앞 단계를 기다리는 동안의 표시를 확인하는 테스트.

`deident_status`의 열 기본값이 `skipped`라서 규격 검증 전이나 변환 중인 영상도 `skipped`다.
이 값을 "켜기 전에 처리됨"으로 읽으면 새 영상이 검증을 마칠 때까지 실행이 필요한 단계로 보이고
재시도 버튼까지 생긴다. 그 버튼을 누르면 검증하지 않은 원본을 변환과 동시에 비식별화한다.
실제로 켜기 전에 준비를 마친 영상은 여전히 실행 필요와 재시도로 표시해야 한다.
"""
import uuid

import pytest
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


async def _deident_project(client: AsyncClient, token: str, name: str) -> str:
    """비식별화를 켠 프로젝트를 만들고 ID를 돌려준다."""
    pid = await new_project(client, token, name)
    async with SessionLocal() as s:
        (await s.get(Project, uuid.UUID(pid))).deident_enabled = True
        await s.commit()
    return pid


async def _timeline(client: AsyncClient, token: str, vid: str) -> dict[str, dict]:
    """준비 타임라인을 단계 이름으로 묶어 돌려준다."""
    r = await client.get(f"/videos/{vid}/prep-timeline",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return {e["stage"]: e for e in r.json()["entries"]}


async def test_new_video_before_probe_is_waiting(client: AsyncClient, admin_token: str):
    """규격 검증 전인 새 영상의 비식별화 단계를 대기로 표시하고 재시도 버튼을 두지 않는다."""
    pid = await _deident_project(client, admin_token, "deid-wait-probe")
    vid = await upload_video(client, admin_token, pid)
    tl = await _timeline(client, admin_token, vid)
    e = tl["deident"]
    assert e["status"] == "pending", e
    assert e["retryable"] is False, e
    assert "켜기 전" not in (e["note"] or "") + (e["skipped_reason"] or ""), e
    # 변환 단계도 검증 전이라 대기다. 두 단계가 같은 이유로 같은 상태를 보인다.
    assert tl["transcode"]["status"] == "pending", tl["transcode"]

    # 목록의 진행 위치도 검증 중이다. 막힘(가리지 않은 영상)으로 분류하지 않는다.
    r = await client.get(f"/projects/{pid}/videos",
                         headers={"Authorization": f"Bearer {admin_token}"})
    body = r.json()
    rows = body["items"] if isinstance(body, dict) else body
    row = next(x for x in rows if x["video_id"] == vid)
    assert row["prep_stage"] == "probing", row
    assert row["prep_blocked_stage"] != "deident_unmasked", row


@pytest.mark.parametrize("transcode", ["pending", "running"])
async def test_video_in_transcode_is_waiting(
    client: AsyncClient, admin_token: str, transcode: str,
):
    """검증은 통과했지만 변환 중인 영상도 비식별화 단계가 아직 열리지 않았으므로 대기다."""
    pid = await _deident_project(client, admin_token, f"deid-wait-tc-{transcode}")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status=transcode)
    e = (await _timeline(client, admin_token, vid))["deident"]
    assert (e["status"], e["retryable"]) == ("pending", False), e


async def test_waiting_behind_failed_transcode_says_why(client: AsyncClient, admin_token: str):
    """변환이 실패한 영상의 비식별화 단계는 대기이고 앞 단계를 기다린다는 사유를 싣는다."""
    pid = await _deident_project(client, admin_token, "deid-wait-tc-fail")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="failed")
    e = (await _timeline(client, admin_token, vid))["deident"]
    assert e["status"] == "pending" and e["retryable"] is False, e
    assert e["note"] == "앞 단계가 끝나야 진행합니다.", e


async def test_finished_before_on_is_still_needed(client: AsyncClient, admin_token: str):
    """비식별화를 끈 채 준비를 마친 영상은 켠 뒤에 실행 필요와 재시도로 표시한다."""
    pid = await _deident_project(client, admin_token, "deid-wait-before")
    vid = await upload_video(client, admin_token, pid)
    await _set(vid, probe_status="passed", duration_seconds=60.0, transcode_status="skipped",
               publish_status="done", deident_status="skipped")
    e = (await _timeline(client, admin_token, vid))["deident"]
    assert (e["status"], e["retryable"]) == ("needed", True), e
    assert "켜기 전" in (e["note"] or ""), e


async def test_retry_refuses_a_waiting_video(client: AsyncClient, admin_token: str):
    """검증 전 영상의 비식별화 재시도는 서버도 거절한다. 화면의 버튼과 같은 판정을 쓴다."""
    pid = await _deident_project(client, admin_token, "deid-wait-retry")
    vid = await upload_video(client, admin_token, pid)
    r = await client.post(f"/projects/{pid}/videos/{vid}/deident/retry",
                          headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 409 and r.json()["detail"] == "deident_waiting", r.text
    async with SessionLocal() as s:
        assert (await s.get(Video, uuid.UUID(vid))).deident_status == "skipped"


@pytest.mark.parametrize(("probe", "transcode", "publish", "downstream", "reached"), [
    ("pending", "skipped", "skipped", False, False),
    ("passed", "pending", "skipped", False, False),
    ("passed", "running", "skipped", False, False),
    ("passed", "failed", "skipped", False, False),
    ("passed", "skipped", "skipped", False, True),
    ("passed", "done", "skipped", False, True),
    ("passed", "skipped", "done", False, True),
    ("pending", "skipped", "skipped", True, True),
    ("pending", "done", "skipped", False, True),
])
def test_reached_deident_cases(
    probe: str, transcode: str, publish: str, downstream: bool, reached: bool,
):
    """파이프라인 위치 판정이 열 기본값과 실제 처리를 구분하는지 확인한다."""
    from api_gateway.services.deident_gap import deident_gap, reached_deident

    got = reached_deident(probe_status=probe, transcode_status=transcode,
                          publish_status=publish, downstream=downstream)
    assert got is reached
    assert deident_gap("skipped", True, reached=got) == ("before_on" if reached else "waiting")
    # 끈 프로젝트는 위치와 상관없이 쓰지 않음이다.
    assert deident_gap("skipped", False, reached=got) == "off"
