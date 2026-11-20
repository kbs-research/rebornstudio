"""워커 beat가 부르는 reconciler 진입점.

`single_runner(_RECONCILER_LOCK)` advisory lock으로 여러 워커의 동시 실행을 막는다.
한 번에 한 워커만 단계를 실행한다. 각 단계는 FOR UPDATE SKIP LOCKED LIMIT `_BATCH`로 배치를 잠근다.
Celery beat가 5초마다 `run_once`를 부른다.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.services.joblock import single_runner
from api_gateway.services.maiu import naver_client
from api_gateway.services.maiu import slot_counter as maiu_slot_counter
from api_gateway.services.maiu.reconciler.common import _BATCH
from api_gateway.services.maiu.reconciler.stages import (
    bulk_check_analyses,
    bulk_check_registrations,
    bulk_check_summaries,
    bulk_poll_analyses,
    bulk_poll_registrations,
    bulk_poll_scene_summaries,
    promote_failures_to_deferred,
    retry_deferred,
    sweep_stale_in_flight,
)

_RECONCILER_LOCK = 42424242


async def run_once(db: AsyncSession) -> dict[str, int]:
    """5단계를 한 번 실행한다. 정상 사이클과 보류 사이클을 10:1로 배분한다.

    실행마다 `maiu_slot_counter`에서 'normal' 또는 'deferred' 슬롯을 받는다.
    'deferred' 사이클(11회 중 1번)은 `retry_deferred`로 보류 행을 `_BATCH`건까지 재처리한다.
    이어서 일시 오류로 보내지 못한 분석 요청을 `redispatch_deferred`로 다시 보낸다.
    'normal' 사이클(11회 중 10번)은 5단계를 진행하고 보류 행은 재처리하지 않는다.
    advisory lock을 얻지 못하면 `{"skipped": 1}`을 돌려준다.
    한 사이클의 MI 요청은 httpx 클라이언트 하나를 같이 쓴다(`naver_client.shared_session`).
    """
    async with single_runner(_RECONCILER_LOCK) as got:
        if not got:
            return {"skipped": 1}
        async with naver_client.shared_session():
            return await _run_stages(db)


async def _run_stages(db: AsyncSession) -> dict[str, int]:
    """락을 가진 상태에서 5단계를 한 번 실행한다. 락 획득과 해제는 `run_once`가 한다."""
    slot = maiu_slot_counter.get_counter().acquire()
    if slot == "deferred":
        # 보류 사이클이다. 실패해 보류된 행을 재처리한다. 정상 사이클만 돌면 보류 행이 계속 남는다.
        n = await retry_deferred(db, limit=_BATCH)
        # 일시 오류로 보내지 못한 분석 요청도 여기서 다시 보낸다.
        # 자동으로 다시 보내지 않으면 "요청 대기" 잡은 사람이 다시 누를 때까지 진행되지 않는다.
        # 보류 사이클은 11회 중 1번이라 요청량 제한에 걸린 API를 짧은 간격으로 다시 호출하지 않는다.
        from api_gateway.services.maiu.service import redispatch_deferred
        m = await redispatch_deferred(db)
        return {"slot": slot, "retried_deferred": n, "redispatched": m}
    # 정상 사이클이다. 5단계를 진행하고 stale sweeper를 실행한다.
    return {
        "slot": "normal",
        "registering":  await bulk_check_registrations(db),
        "registered":   await bulk_poll_registrations(db),
        "analyzing":    await bulk_check_analyses(db),
        "analyzed":     await bulk_poll_analyses(db),
        "summarizing":  await bulk_check_summaries(db),
        "summarized":   await bulk_poll_scene_summaries(db),
        "deferred":     await promote_failures_to_deferred(db),
        "swept":        await sweep_stale_in_flight(db),
    }
