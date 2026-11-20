"""reconciler 하위 모듈이 같이 쓰는 배치 크기, 일시 오류 표지, 감사 이벤트 대상 id."""
from __future__ import annotations

from uuid import UUID

from api_gateway.models import AnalysisJob
from api_gateway.services.maiu.service import TRANSIENT_MARK

__all__ = ["TRANSIENT_MARK", "_BATCH", "_job_target"]

_BATCH = 50


def _job_target(job: AnalysisJob) -> UUID:
    """maiu 단계 이벤트의 `target_id`로 쓸 id를 돌려준다. 잡이 분석하는 클립 id, 없으면 영상 id다.

    `target_id`가 있어야 같은 초에 기록된 이벤트가 서로 다른 잡인지 같은 잡의 반복인지 구분된다.
    잡은 (영상, 클립, 어댑터)마다 하나라서 이 id로 잡을 식별할 수 있다.
    잡의 BIGSERIAL id를 UUID로 바꾸면 앞자리가 모두 `00000000`이 된다.
    그러면 감사 로그의 짧은 대상 ID로 잡을 구분할 수 없다.
    """
    return job.clip_id or job.video_id
