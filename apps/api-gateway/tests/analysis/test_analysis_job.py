"""`analysis_job`의 어댑터 중립 상태 단위 테스트.

세 가지를 확인한다.
단위는 영상과 클립 두 가지다.
같은 단위에 중복 행이 생기지 않는다.
부분 성공은 완료·실패와 별개의 독립 상태다.
"""
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, Video
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tests._helpers import new_project, seed_clip_work, upload_video


async def _job(**kw) -> AnalysisJob:
    """테스트용 MAIU 분석 작업 행을 만든다."""
    return AnalysisJob(adapter_key="maiu", trace_id=uuid.uuid4(), **kw)


async def test_clip_id_decides_the_unit(client: AsyncClient, admin_token: str):
    """`clip_id`로 단위를 정하는지 확인한다. NULL이면 영상 단위이고 값이 있으면 클립 단위다.

    두 단위 모두 분석 진행 상태를 기록하므로 한 테이블에 둔다.
    """
    pid = await new_project(client, admin_token, "aj")
    vid = await upload_video(client, admin_token, pid)
    cid_s, _wid = await seed_clip_work(pid, vid)
    cid = uuid.UUID(str(cid_s))

    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id, clip_id=None))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id, clip_id=cid))
        await s.commit()

    async with SessionLocal() as s:
        rows = list(await s.scalars(
            select(AnalysisJob).where(AnalysisJob.video_id == uuid.UUID(vid))
        ))
    assert len(rows) == 2
    assert {r.clip_id for r in rows} == {None, cid}
    assert all(r.status == "pending" for r in rows)


async def test_video_level_rows_cannot_duplicate(client: AsyncClient, admin_token: str):
    """같은 (영상, 클립, 어댑터)의 두 번째 행을 거절한다. 행이 둘이면 어느 행이 유효한지 알 수 없다.

    이 제약은 영상 단위 행 때문에 필요하다.
    Postgres는 기본적으로 NULL끼리 다른 값으로 비교한다.
    그래서 `NULLS NOT DISTINCT`가 없으면 `clip_id IS NULL` 행이 얼마든지 중복된다.
    """
    pid = await new_project(client, admin_token, "aj2")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id, clip_id=None))
        await s.commit()

    with pytest.raises(IntegrityError):
        async with SessionLocal() as s:
            v = await s.get(Video, uuid.UUID(vid))
            s.add(await _job(project_id=v.project_id, video_id=v.video_id, clip_id=None))
            await s.commit()


async def test_same_unit_different_adapters_coexist(client: AsyncClient, admin_token: str):
    """같은 단위라도 어댑터가 다르면 행을 따로 둘 수 있는지 확인한다.

    분석 스택을 교체할 수 있어야 하므로 어댑터마다 행을 둔다. 영상 단위 행으로 확인한다.
    """
    pid = await new_project(client, admin_token, "aj3")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(AnalysisJob(project_id=v.project_id, video_id=v.video_id,
                          adapter_key="maiu", trace_id=uuid.uuid4()))
        s.add(AnalysisJob(project_id=v.project_id, video_id=v.video_id,
                          adapter_key="pyscenedetect", trace_id=uuid.uuid4()))
        await s.commit()

    async with SessionLocal() as s:
        n = len(list(await s.scalars(
            select(AnalysisJob).where(AnalysisJob.video_id == uuid.UUID(vid))
        )))
    assert n == 2


async def test_partial_is_a_first_class_status(client: AsyncClient, admin_token: str):
    """부분 성공을 별도 상태(`partial`)로 저장하는지 확인한다.

    39개 중 37개 성공을 완료나 실패로 합치면 화면이 잘못 표시한다.
    `done`이면 빠진 2개가 보이지 않고, `failed`면 성공한 37개가 보이지 않는다.
    """
    pid = await new_project(client, admin_token, "aj4")
    vid = await upload_video(client, admin_token, pid)

    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id, status="partial"))
        await s.commit()

    async with SessionLocal() as s:
        row = await s.scalar(select(AnalysisJob).where(AnalysisJob.video_id == uuid.UUID(vid)))
    assert row.status == "partial"


async def test_status_check_rejects_vendor_stages(client: AsyncClient, admin_token: str):
    """`status`에 벤더 단계를 넣으면 DB 제약이 거절한다. MI의 `summarizing`은 `stage`에 넣는다.

    판정이 벤더 단계를 읽으면 벤더별 상태가 어댑터 중립 계약 안으로 들어온다.
    """
    pid = await new_project(client, admin_token, "aj5")
    vid = await upload_video(client, admin_token, pid)

    with pytest.raises(IntegrityError):
        async with SessionLocal() as s:
            v = await s.get(Video, uuid.UUID(vid))
            # 일부러 벤더 단계를 `status`에 넣는다. DB 제약이 거절해야 한다.
            s.add(await _job(project_id=v.project_id, video_id=v.video_id, status="summarizing"))
            await s.commit()

    # 같은 값을 `stage`에는 넣을 수 있다. 진단용이라 값을 제한하지 않는다.
    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id,
                         status="running", stage="summarizing"))
        await s.commit()


async def test_deleting_a_clip_takes_its_job(client: AsyncClient, admin_token: str):
    """클립을 지우면 그 클립의 분석 작업도 지워진다. 없는 클립을 가리키는 행을 남기지 않는다."""
    pid = await new_project(client, admin_token, "aj6")
    vid = await upload_video(client, admin_token, pid)
    cid_s, _wid = await seed_clip_work(pid, vid)
    cid = uuid.UUID(str(cid_s))

    async with SessionLocal() as s:
        v = await s.get(Video, uuid.UUID(vid))
        s.add(await _job(project_id=v.project_id, video_id=v.video_id, clip_id=cid))
        await s.commit()

    async with SessionLocal() as s:
        await s.delete(await s.get(Clip, cid))
        await s.commit()

    async with SessionLocal() as s:
        assert await s.scalar(
            select(AnalysisJob).where(AnalysisJob.clip_id == cid)
        ) is None
