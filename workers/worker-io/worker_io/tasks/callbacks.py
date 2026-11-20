"""무DB 잡이 보낸 하트비트와 완료 보고를 DB에 반영하는 태스크.

펜싱 판정은 서비스 함수 한 곳에서 한다. 낡은 토큰의 보고는 거기서 버린다.
"""
from reborn_contracts.queues import (
    DEIDENT_COMPLETE,
    DEIDENT_HEARTBEAT,
    PUBLISH_COMPLETE,
    PUBLISH_HEARTBEAT,
    SCENE_SPLIT_COMPLETE,
    SCENE_SPLIT_HEARTBEAT,
    TRANSCODE_COMPLETE,
    TRANSCODE_HEARTBEAT,
)

from ..celery_app import app
from ._session import _run_with_fresh_session


@app.task(name=SCENE_SPLIT_HEARTBEAT, ignore_result=True)
def scene_split_heartbeat(video_id: str, token: str) -> bool:
    """씬 분할 리스를 연장한다. 무DB 워커의 하트비트를 DB UPDATE로 바꾼다."""
    from uuid import UUID

    from api_gateway.services.media.scene_split import extend_lease
    return _run_with_fresh_session(lambda s: extend_lease(s, UUID(video_id), token))


@app.task(name=SCENE_SPLIT_COMPLETE)
def scene_split_complete(
    *, video_id: str, token: str, ok: bool,
    segments: list | None = None, basis: dict | None = None,
    error: str | None = None, transient: bool = False,
) -> str:
    """씬 분할 워커의 회신을 받아 DB에 반영한다. 펜싱 판정도 이 한 곳에서 한다."""
    from uuid import UUID

    from api_gateway.services.media.scene_split import complete
    return _run_with_fresh_session(lambda s: complete(
        s, video_id=UUID(video_id), token=token, ok=ok,
        segments=segments, basis=basis, error=error, transient=transient,
    ))


@app.task(name=DEIDENT_HEARTBEAT, ignore_result=True)
def deident_heartbeat(
    video_id: str, token: str, done_seconds: float | None = None,
) -> bool:
    """비식별화 리스를 연장한다. 무DB 워커의 하트비트를 DB UPDATE로 바꾼다."""
    from uuid import UUID

    from api_gateway.services.media.deident import extend_lease
    return _run_with_fresh_session(
        lambda s: extend_lease(s, UUID(video_id), token, done_seconds=done_seconds))


@app.task(name=DEIDENT_COMPLETE, ignore_result=True)
def deident_complete(
    video_id: str, token: str, ok: bool, error: str | None = None,
    transient: bool = False, elapsed_s: float | None = None,
    report: dict | None = None,
) -> bool:
    """비식별화 완료 보고를 처리한다. 펜싱·산출 승격·다음 단계 결정은 DB 쪽 한 곳에서 한다."""
    from uuid import UUID

    from api_gateway.services.media.deident import complete
    return _run_with_fresh_session(
        lambda s: complete(s, UUID(video_id), token, ok=ok, error=error,
                           transient=transient, elapsed_s=elapsed_s, report=report))


@app.task(name=PUBLISH_HEARTBEAT, ignore_result=True)
def publish_heartbeat(
    video_id: str, token: str, done_seconds: float | None = None,
) -> bool:
    """publish 리스를 연장한다. 무DB 워커의 하트비트를 DB UPDATE로 바꾼다."""
    from uuid import UUID

    from api_gateway.services.media.publish import extend_lease
    return _run_with_fresh_session(
        lambda s: extend_lease(s, UUID(video_id), token, done_seconds=done_seconds))


@app.task(name=PUBLISH_COMPLETE, ignore_result=True)
def publish_complete(
    video_id: str, token: str, ok: bool,
    error: str | None = None, transient: bool = False, elapsed_s: float | None = None,
    outputs: list | None = None,
) -> bool:
    """publish 완료 보고를 처리한다.

    펜싱·산출 검사·최종 버킷 반영·스테이징 정리는 DB 쪽 한 곳에서 한다.
    `outputs`(재생본 측정값)는 선택 인자다. 구 워커가 보낸 메시지가 큐에 남아 있을 수 있다.
    그 메시지가 새 인자 때문에 실패하면 배포 중에 완료 보고가 모두 끊긴다.
    """
    from uuid import UUID

    from api_gateway.services.media.publish import complete
    return _run_with_fresh_session(
        lambda s: complete(s, UUID(video_id), token, ok=ok, error=error,
                           transient=transient, elapsed_s=elapsed_s, outputs=outputs))


@app.task(name=TRANSCODE_HEARTBEAT, ignore_result=True)
def transcode_heartbeat(
    video_id: str, token: str, done_seconds: float | None = None,
) -> bool:
    """무DB 워커 대신 리스를 연장한다. 하트비트 메시지를 DB UPDATE로 바꾼다.

    `done_seconds`는 인코딩한 미디어 시간이다. 이 인자를 선택으로 둔 이유는
    구 워커가 보낸 2-인자 메시지가 큐에 남아 있을 수 있기 때문이다.
    새 인자 때문에 그 메시지가 실패하면 배포 중에 리스 연장이 모두 끊긴다.
    """
    from uuid import UUID

    from api_gateway.services.media.transcode import extend_lease
    return _run_with_fresh_session(
        lambda s: extend_lease(s, UUID(video_id), token, done_seconds=done_seconds)
    )


@app.task(name=TRANSCODE_COMPLETE, ignore_result=True)
def transcode_complete(
    video_id: str, token: str, *, ok: bool,
    error: str | None = None, transient: bool = False, elapsed_s: float | None = None,
    outputs: list | None = None,
) -> bool:
    """무DB 워커의 완료 보고를 처리한다.

    펜싱·산출 검사·성공 기록·실패 분기(환불/상한)는 DB 쪽이 한다.
    `outputs`(분석본 측정값)는 선택 인자다. 구 워커가 보낸 메시지가 큐에 남아 있을 수 있다
    (`transcode_heartbeat`의 `done_seconds`와 같은 이유). 없으면 산출 검사 없이 받는다.
    """
    from uuid import UUID

    from api_gateway.services.media.transcode import complete_transcode
    return _run_with_fresh_session(lambda s: complete_transcode(
        s, UUID(video_id), token, ok=ok, error=error,
        transient=transient, elapsed_s=elapsed_s, outputs=outputs,
    ))
