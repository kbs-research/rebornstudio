"""클립 썸네일과 영상 대표 이미지를 추출할 파일의 선택 규칙.

썸네일은 라벨러를 포함한 모든 역할이 목록에서 본다.
비식별화를 켠 프로젝트에서 원본으로 추출하면 마스킹하지 않은 얼굴이 썸네일에 노출된다.
그래서 비식별화를 쓰는 영상은 비식별화와 재생본 확정이 모두 끝날 때까지 추출을 미룬다.
비식별화를 쓰지 않는 영상은 원본에서 추출한다.

추출한 파일의 출처(`MASKED`·`SOURCE`)는 썸네일 키와 함께 저장한다.
비식별화를 쓰는 영상인데 출처가 `MASKED`가 아닌 썸네일은 키를 지워 다음 주기의 후보로 돌린다.
그래서 원본에서 추출한 썸네일은 수동 수정 없이 주기 작업이 다시 만든다.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Project, Video

#: 비식별화본(분석본 또는 마스터)에서 추출한 썸네일의 출처 값.
MASKED = "deident"
#: 원본에서 추출한 썸네일의 출처 값. 비식별화를 쓰지 않는 영상에만 붙는다.
SOURCE = "source"


def _deident_expected():
    """영상의 프로젝트가 지금 비식별화를 쓰면 참인 식을 만든다. 프로젝트가 없으면 거짓이다."""
    from api_gateway.services.media.video.classify import _deident_expected_expr
    return func.coalesce(_deident_expected_expr(), False)


def masked_required():
    """썸네일을 비식별화본에서 추출해야 하는 영상이면 참인 식을 만든다.

    프로젝트가 지금 비식별화를 쓰거나 영상의 비식별화가 끝났으면 참이다.
    프로젝트가 나중에 비식별화를 꺼도 이미 마스킹한 영상에는 원본 썸네일을 두지 않는다.
    """
    return or_(_deident_expected(), Video.deident_status == "done")


def ready_expr():
    """지금 썸네일을 추출할 수 있는 영상이면 참인 식을 만든다.

    비식별화본에서 추출해야 하는 영상은 비식별화와 재생본 확정이 모두 끝나야 참이다.
    확정 전에는 분석본 위치의 파일이 마스킹 전 파일일 수 있다.
    `deident.retry`가 비식별화를 다시 돌리는 영상이 그런 경우다.
    """
    return or_(
        and_(Video.deident_status == "done", Video.publish_status == "done"),
        ~masked_required(),
    )


def stale_expr(origin):
    """이미 있는 썸네일을 다시 추출해야 하면 참인 식을 만든다.

    Args:
        origin: 썸네일 출처를 담은 JSONB 값의 텍스트 식.
            출처가 없는 행은 원본에서 추출한 것으로 본다.
    """
    return and_(origin.is_distinct_from(MASKED), masked_required())


async def source_for(db: AsyncSession, video: Video) -> tuple[str, str] | None:
    """썸네일을 추출할 파일의 URI와 출처를 돌려준다. 아직 추출할 수 없으면 `None`을 돌려준다.

    `ready_expr`과 같은 규칙을 한 건에 적용한다. 단건 경로(`process_one_by_id`)는 후보 조회를
    거치지 않으므로 여기서 다시 확인한다.

    Returns:
        `(URI, 출처)`. 출처는 `MASKED` 또는 `SOURCE`다.
    """
    from api_gateway.services.media.transcode import master_bucket, master_key

    if video.deident_status == "done":
        if video.publish_status != "done":
            return None
        # 확정된 분석본은 비식별화본이다. `analysis_uri`가 없으면 스테이징 마스터를 읽는다.
        uri = video.analysis_uri or f"s3://{master_bucket(video)}/{master_key(video.video_id)}"
        return uri, MASKED
    expected = await db.scalar(
        select(Project.deident_enabled).where(Project.project_id == video.project_id))
    if expected:
        return None
    if not video.source_uri:
        return None
    return video.source_uri, SOURCE
