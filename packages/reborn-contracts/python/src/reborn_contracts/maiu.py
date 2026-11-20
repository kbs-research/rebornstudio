"""MI 분석 요청 스키마.

잡 상태는 8개 상태를 그대로 외부에 노출한다.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 8개 상태. reconciler가 상태를 전이한다.
MaiuJobStatus = Literal[
    "pending", "registering", "registered",
    "analyzing", "analyzed", "summarizing",
    "done", "failed",
]


class MaiuDispatchRequest(BaseModel):
    """MI 분석 요청. 대상을 받는다. 클립 길이는 고정 기본값이라 받지 않고, 모르는 필드는 422다."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=10, ge=1, le=100, description="이번에 MI로 요청할 최대 work_item 갯수"
    )
    video_ids: list[UUID] | None = Field(
        default=None, max_length=100,
        description="지정 시 이 영상들만 요청(선택 기반). 미지정이면 대기분을 limit개 일괄 요청.",
    )


class MaiuJobDTO(BaseModel):
    """MI 분석 잡 한 건. 잡은 작업 단위가 아니라 영상 단위다."""
    #: 잡 식별자. 내부 정수 id를 하위 비트에 담은 UUID라서 잡을 구분하는 데만 쓴다.
    job_id: UUID
    project_id: UUID
    #: 분석한 영상.
    video_id: UUID
    #: `video_id`와 같은 값. 영상 단위 잡에는 작업 식별자가 없다. 기존 소비자가 읽으므로 남긴다.
    work_item_id: UUID = Field(
        deprecated="video_id와 같은 값입니다. video_id를 사용합니다.",
        description="video_id와 같은 값입니다. 영상 단위 잡이라 작업 식별자가 없습니다.",
    )
    maiu_asset_id: str | None = None
    maiu_analysis_id: str | None = None
    status: MaiuJobStatus
    deferred: bool = False
    retry_count: int = 0
    in_flight_count: int = 0
    error: str | None = None
    trace_id: UUID | None = None
    dispatched_at: datetime
    completed_at: datetime | None = None


# 고른 영상을 요청하지 않은 사유 코드.
# 앞의 여섯은 준비 판정(`media.video._analysis_hold_case`)이 정한다.
MaiuHoldReason = Literal[
    "not_probed", "probe_failed", "prep_failed", "preparing", "unmasked", "no_duration",
    "not_waiting",
]


class MaiuDispatchResult(BaseModel):
    """MI 요청 결과. 분모를 함께 싣는다.

    `deferred`는 MI 쪽 일시 제한(429·5xx)으로 이번에 보내지 못한 수다.
    보류이므로 다시 요청하면 이어서 나간다.
    `failed`는 그 영상 고유의 문제로 실패한 수다. `skipped`는 이미 job이 있는 건수다.
    `held`는 고른 영상 중 요청 게이트가 막아 집지 않은 수다. `held_reasons`는 사유별 수다.
    영상을 골라 요청했을 때만 센다. 생성된 job 목록만 주면 나가지 않은 건이
    보류인지, 이미 요청된 것인지, 준비 중이라 막힌 것인지 구분할 수 없다.
    """
    created: list[MaiuJobDTO]
    requested: int
    deferred: int = 0
    failed: int = 0
    skipped: int = 0
    held: int = 0
    held_reasons: dict[MaiuHoldReason, int] = Field(default_factory=dict)
