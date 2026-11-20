"""준비 과정의 단계 순서를 정하는 유일한 모듈.

순서를 여러 곳에 정의하면 단계를 추가할 때 한 곳이 빠지기 쉽다.
그 경로의 영상은 상태가 pending인 채 어느 워커도 가져가지 않아 오류 없이 "변환 중"으로 남는다.
단계를 추가할 때는 `_ORDER`와 그 단계의 필요 조건(`_needs`)만 고친다.

단계는 `transcode`, `deident`, `publish` 순서로 연다.
`transcode`는 분석본 마스터를 만들고, `deident`는 비식별화를 하고,
`publish`는 재생본을 만들고 산출을 최종 위치로 확정한다.
각 단계는 필요할 때만 열고, 필요 조건은 단계마다 다르다.

- `transcode`: 원본이 MI 규격을 벗어나 마스터를 만들어야 할 때만 연다.
- `deident`: 프로젝트가 비식별화를 켰을 때 연다. 끄면 skipped, 켰는데 처리하지 못하면 failed다.
- `publish`: 재생본 생성이나 마스터 확정 중 하나라도 필요할 때 연다.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Project, Video
from api_gateway.services.media.transcode import plan_outputs

log = logging.getLogger(__name__)

# 준비 단계 순서. 단계 순서의 단일 출처다.
_ORDER = ("transcode", "deident", "publish")


def _needs(video: Video, stage: str, plan: list[str], *, deident_on: bool) -> bool:
    """영상이 `stage` 단계를 거쳐야 하면 True를 돌려준다."""
    if stage == "transcode":
        # 계획에 분석본 마스터가 있을 때만 변환한다.
        # 원본이 규격 안이면 변환하지 않는다. 비식별화가 켜져 있으면 비식별화가 마스터를 만든다.
        return "analysis" in plan
    if stage == "deident":
        return deident_on
    if stage == "publish":
        # 계획에 산출이 하나라도 있으면 확정 단계가 필요하다.
        # 비식별화가 켜져 있으면 비식별화 산출(마스터)을 최종 위치로 옮겨야 한다.
        # 그래서 계획이 비어 있어도 연다.
        return bool(plan) or deident_on
    raise ValueError(f"unknown stage: {stage}")


async def open_next(db: AsyncSession, video: Video) -> str | None:
    """다음에 열어야 할 단계를 열고 그 이름을 돌려준다. 열 단계가 없으면 None을 돌려준다.

    이미 끝난 단계(`done`·`failed`)는 건드리지 않는다.
    `skipped`는 상태 열의 기본값이라 아직 정해지지 않은 단계로 보고, 필요할 때만 pending으로 연다.
    필요 없는 단계를 pending으로 되돌리면 완료된 영상이 파이프라인으로 돌아간다.

    probe 통과 직후, transcode·deident 완료 직후, `transcode.build_jobs`가 변환이
    필요 없어진 영상을 건너뛸 때 부른다.
    주의: 다른 곳에서 부르지 않는다.
    호출 지점이 흩어지면 영상이 다시 처리되는 원인을 추적할 수 없다.

    판정에 필요한 값은 ORM 인스턴스 대신 명시적인 SELECT 한 번으로 읽는다.
    호출 직전에 `merge_extra` 같은 raw UPDATE가 돌면 ORM 인스턴스가 만료되고,
    만료된 인스턴스의 속성을 읽으면 지연 로드가 일어나 `MissingGreenlet` 예외가 난다.
    """
    row = (await db.execute(
        select(
            Video.extra, Video.transcode_profile,
            Video.transcode_status, Video.deident_status, Video.publish_status,
            Project.deident_enabled,
        )
        .join(Project, Project.project_id == Video.project_id)
        .where(Video.video_id == video.video_id)
    )).first()
    if row is None:
        return None
    extra, profile, *statuses, deident_on = row
    status_by = dict(zip(_ORDER, statuses, strict=True))
    plan = plan_outputs((extra or {}).get("probe") or {}, profile)

    for stage in _ORDER:
        st = status_by[stage]
        if st in ("pending", "queued", "running"):
            return None          # 단계가 이미 열려 있다. 그 단계가 끝나면 다시 호출된다.
        if st in ("done", "failed"):
            continue             # 이미 끝난 단계
        # st == "skipped"는 아직 정해지지 않은 단계다.
        if _needs(video, stage, plan, deident_on=bool(deident_on)):
            # UPDATE로 연다. 만료된 인스턴스에 속성을 쓰면 다시 지연 로드가 일어난다.
            await db.execute(
                update(Video).where(Video.video_id == video.video_id)
                .values(**{f"{stage}_status": "pending"})
            )
            log.info("pipeline: %s 단계를 연다 video=%s", stage, video.video_id)
            return stage
    return None
