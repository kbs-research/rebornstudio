"""클립 썸네일·영상 포스터의 한 주기 처리량과 같은 큐 작업과의 경합 상한 테스트.

네 가지를 확인한다.

  1. 한 주기 상한과 시간 예산 값. 예산은 beat 주기(60초)보다 짧고 둘을 합쳐도 주기를 넘지 않는다.
  2. 시간 예산을 넘기면 남은 항목을 다음 주기로 미룬다.
  3. 다른 실행이 돌고 있으면 후보를 고르지 않고 0을 돌려준다(q.media 워커의 동시 실행 두 개를 모두 점유하지 않는다).
  4. 후보는 프로젝트마다 번갈아 고른다. 오래된 클립이 많은 프로젝트가 다른 프로젝트를 기다리게 하지 않는다.

`process_pending`을 그대로 돌리면 같은 DB에 있는 다른 테스트의 행까지 처리한다.
그래서 순서는 후보 조건에 이 테스트의 프로젝트만 더해 확인한다(`fair_select`).
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, Video
from api_gateway.services.joblock import single_runner
from api_gateway.services.media import clip_thumbnail, video_thumbnail
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


def test_batch_limits_and_budgets():
    """한 주기 상한은 클립 40건·포스터 20건이고 예산의 합은 beat 주기(60초)보다 짧다."""
    assert clip_thumbnail.BATCH_LIMIT == 40
    assert video_thumbnail.BATCH_LIMIT == 20
    assert clip_thumbnail.TIME_BUDGET_S < 60 and video_thumbnail.TIME_BUDGET_S < 60
    assert clip_thumbnail.TIME_BUDGET_S + video_thumbnail.TIME_BUDGET_S < 60
    assert clip_thumbnail.LOCK_KEY != video_thumbnail.LOCK_KEY, "둘이 같은 락이면 한쪽이 매번 건너뛴다"


async def test_budget_defers_the_rest_to_the_next_beat():
    """예산을 넘기면 다음 항목을 시작하지 않는다. 시작한 항목은 끝까지 처리한다."""
    now = [0.0]
    handled: list[int] = []

    async def handle(i: int) -> bool:
        """한 건에 10초가 걸린 것처럼 시계를 10초 진행시킨다."""
        handled.append(i)
        now[0] += 10.0
        return True

    done = await clip_thumbnail.run_within_budget(
        list(range(10)), handle, budget_s=25.0, name="test", clock=lambda: now[0])
    assert handled == [0, 1, 2] and done == 3


async def test_one_failure_does_not_stop_the_batch():
    """한 건의 예외가 나머지 항목을 막지 않는다."""
    async def handle(i: int) -> bool:
        """두 번째 항목만 예외를 낸다."""
        if i == 1:
            raise RuntimeError("boom")
        return True

    done = await clip_thumbnail.run_within_budget(
        [0, 1, 2], handle, budget_s=60.0, name="test")
    assert done == 2


@pytest.mark.parametrize("module", [clip_thumbnail, video_thumbnail])
async def test_a_second_run_steps_aside(module, monkeypatch):
    """다른 실행이 락을 점유하고 있으면 후보를 고르지 않고 0을 돌려준다."""
    async def _must_not_pick(*a, **kw):
        """후보를 고르면 실패한다."""
        raise AssertionError("다른 실행이 도는 중인데 후보를 골랐다")

    monkeypatch.setattr(module, "list_pending", _must_not_pick)
    monkeypatch.setattr(module, "invalidate_unmasked", _must_not_pick)
    async with single_runner(module.LOCK_KEY) as got:
        assert got
        async with SessionLocal() as s:
            assert await module.process_pending(s) == 0


async def _project_with_clips(
    client: AsyncClient, token: str, name: str, n: int,
) -> tuple[str, str, list[str]]:
    """원본이 있는 영상 하나와 썸네일 없는 클립 n개를 가진 프로젝트를 만든다.

    한 트랜잭션의 `now()`는 같은 값이다. 그래서 만든 순서가 정렬에 반영되도록 시각을 1밀리초씩 늘린다.
    """
    pid = await new_project(client, token, name)
    vid = await upload_video(client, token, pid)
    ids: list[str] = []
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.source_uri = "s3://example-bucket/news/x.mp4"
        for i in range(n):
            c = Clip(
                video_id=UUID(vid), project_id=UUID(pid),
                start_seconds=Decimal(i * 5), end_seconds=Decimal(i * 5 + 4),
                rights_status="auto_cleared", auto_caption={"brief": "장면"}, speakers=[],
                created_at=datetime.now(UTC) + timedelta(milliseconds=i),
            )
            s.add(c)
            await s.flush()
            ids.append(str(c.clip_id))
        await s.commit()
    return pid, vid, ids


async def test_clips_are_picked_in_turns_across_projects(client: AsyncClient, admin_token: str):
    """클립이 많은 프로젝트가 먼저 들어왔어도 다른 프로젝트의 클립이 첫 묶음에 든다."""
    big, _, big_ids = await _project_with_clips(client, admin_token, "thumb-fair-big", 5)
    small, _, small_ids = await _project_with_clips(client, admin_token, "thumb-fair-small", 1)
    base = clip_thumbnail.pending_select().where(Clip.project_id.in_([UUID(big), UUID(small)]))
    async with SessionLocal() as s:
        rows = (await s.execute(clip_thumbnail.fair_select(base).limit(2))).all()
    picked = [str(c.clip_id) for c, _ in rows]
    assert picked == [big_ids[0], small_ids[0]], picked


async def test_posters_are_picked_in_turns_across_projects(client: AsyncClient, admin_token: str):
    """영상이 많은 프로젝트가 먼저 들어왔어도 다른 프로젝트의 영상이 첫 묶음에 든다."""
    big = await new_project(client, admin_token, "poster-fair-big")
    big_ids = [await upload_video(client, admin_token, big) for _ in range(3)]
    small = await new_project(client, admin_token, "poster-fair-small")
    small_ids = [await upload_video(client, admin_token, small)]
    async with SessionLocal() as s:
        for vid in big_ids + small_ids:
            (await s.get(Video, UUID(vid))).source_uri = "s3://example-bucket/news/x.mp4"
        await s.commit()
    base = video_thumbnail.pending_select().where(Video.project_id.in_([UUID(big), UUID(small)]))
    async with SessionLocal() as s:
        rows = list((await s.execute(video_thumbnail.fair_select(base).limit(2))).scalars())
    assert [str(v.video_id) for v in rows] == [big_ids[0], small_ids[0]]
