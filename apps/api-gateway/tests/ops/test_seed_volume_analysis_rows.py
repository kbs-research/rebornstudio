"""운영 밀도 시드(`scripts/dev/seed_dev_volume.py`)의 분석 행이 화면 판정과 맞는지 보는 테스트.

시드의 '분석 중' 표본은 영상 단위 MI 분석 행(`analysis_job`)이다. 화면 판정(`media/video/classify.py`)이
그 행을 분석 중으로 읽어야 단계 화면에서 분석 중 분기를 볼 수 있다. 판정이 읽지 않는 표에 쓰면
표본 영상이 티켓도 분석 행도 없는 영상이 되어 막힘으로 분류된다.
표본은 가짜 에셋 번호를 쓰므로 리컨실러가 처리하지 않아야 한다. 처리하면 실제 MI 조회가 실패해
표본이 실패로 넘어간다.
"""
from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
from api_gateway.services.analysis import job_state
from api_gateway.services.maiu.reconciler.stages import _lock_batch
from httpx import AsyncClient
from sqlalchemy import delete
from tests._helpers import new_project, upload_video

# `scripts/`는 패키지가 아니라서 폴더를 경로에 넣고 import한다. import 시점의 로컬 확인은
# 테스트 환경(로컬 DB, ENVIRONMENT=test)에서 통과한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "dev"))
import seed_dev_volume  # noqa: E402


async def _bucket(client: AsyncClient, token: str, pid: str, vid: str) -> str:
    """영상 목록에서 이 영상의 반입 상태 분류를 돌려준다."""
    r = await client.get(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    rows = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    return next(x for x in rows if x["video_id"] == vid)["ingest_bucket"]


async def test_seed_analyzing_rows_classify_as_analyzing_and_stay_held(
    client: AsyncClient, admin_token: str,
):
    """시드의 진행 단계마다 영상이 분석 중으로 분류되고 리컨실러의 처리 대상에서 빠진다."""
    pid = await new_project(client, admin_token, "seed-vol-an")
    vid = await upload_video(client, admin_token, pid)

    for stage in dict.fromkeys(seed_dev_volume.MAIU_STAGES):
        async with SessionLocal() as s:
            await s.execute(delete(AnalysisJob).where(AnalysisJob.video_id == uuid.UUID(vid)))
            video = await s.get(Video, uuid.UUID(vid))
            video.copyright_status = "cleared"
            job = seed_dev_volume._maiu_job(
                video, uuid.UUID(pid), "asset-test", updated=datetime.now(UTC),
            )
            job_state.advance(job, stage)
            job.payload = {"retry_after": seed_dev_volume._HOLD_UNTIL}
            s.add(job)
            await s.commit()
            job_id = job.id

        assert await _bucket(client, admin_token, pid, vid) == "analyzing", stage

        async with SessionLocal() as s:
            picked = {j.id for j in await _lock_batch(s, stage=stage)}
            await s.rollback()
        assert job_id not in picked, f"리컨실러가 시드 표본을 집었다: {stage}"


async def test_seed_done_row_is_not_analyzing(client: AsyncClient, admin_token: str):
    """시드의 완료 행은 분석 중으로 분류되지 않는다. 클립이 없으면 막힘(재분석 판단 대상)이다."""
    pid = await new_project(client, admin_token, "seed-vol-done")
    vid = await upload_video(client, admin_token, pid)
    async with SessionLocal() as s:
        video = await s.get(Video, uuid.UUID(vid))
        job = seed_dev_volume._maiu_job(
            video, uuid.UUID(pid), "asset-done-test", updated=datetime.now(UTC),
        )
        job_state.advance(job, "done")
        s.add(job)
        await s.commit()

    assert await _bucket(client, admin_token, pid, vid) == "blocked"
