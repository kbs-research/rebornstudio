"""원본을 파생물이 모두 쓰인 뒤에만 정리하는지 확인하는 테스트.

네 가지를 확인한다.
  1. publish는 원본을 지우지 않는다. 뒤에 MI 분석이 남아 있고 규격 내 원본은 그 입력이다.
  2. 처리가 끝나는 방식은 MI 분석 완료 하나다. 분석이 끝나지 않으면 지우지 않는다.
  3. 이 시스템이 올린 파일만 정리한다. 버킷 등록분은 원래 그 위치에 있던 파일이다.
  4. 지우는 원본은 체크섬을 기록했거나 체크섬 재시도가 상한에 닿은 뒤에만 정리한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video
from api_gateway.services.analysis.status_map import neutral_status
from api_gateway.services.media import storage
from api_gateway.services.media.retention import dispose_finished_sources
from httpx import AsyncClient
from sqlalchemy import update
from tests import _storage
from tests._helpers import new_project, upload_video


async def _source(vid: str) -> tuple[str, str]:
    """영상 원본의 (bucket, key)를 돌려준다."""
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        bucket, key = v.source_uri.split("://")[1].split("/", 1)
    return bucket, key


async def _mark_analyzed(
    vid: str, pid: str, status: str = "done", *, checksummed: bool = True,
) -> None:
    """영상에 지정한 단계의 MAIU 분석 잡을 넣는다.

    `checksummed`가 참이면 체크섬도 기록한다.
    지우는 원본은 체크섬이 끝나야 정리 대상이 되므로 정리 시점만 보는 테스트는 체크섬을 미리 채운다.
    """
    async with SessionLocal() as s:
        s.add(AnalysisJob(adapter_key="maiu", video_id=UUID(vid), project_id=UUID(pid),
                          stage=status, status=neutral_status(status)))
        if checksummed:
            await s.execute(update(Video).where(Video.video_id == UUID(vid))
                            .values(source_sha256="sha256:test"))
        await s.commit()


async def _run() -> int:
    """원본 정리를 한 번 실행하고 처리 건수를 돌려준다."""
    async with SessionLocal() as s:
        return await dispose_finished_sources(s)


async def _pick(s) -> set:
    """정리 대상으로 선택되는 영상 id 집합을 돌려준다.

    건수 대신 어떤 영상이 선택되는지로 단언한다.
    이 작업은 프로젝트와 무관하게 전체 영상을 조회한다.
    그래서 반환 건수로 단언하면 다른 테스트가 만든 영상에 영향을 받는다.
    """
    from api_gateway.services.media.retention import _finished_source_query

    return {v.video_id for v in (await s.scalars(_finished_source_query())).all()}


async def test_source_survives_until_analysis_is_done(
    client: AsyncClient, admin_token: str
):
    """분석이 끝나기 전에는 원본을 지우지 않는다.

    원본이 이미 MI 규격이면(`h264 · 720p`) 변환이 분석본을 만들지 않는다.
    그러면 `resolver`가 `analysis_uri or source_uri`로 폴백해 원본을 MI에 넘긴다.
    publish 완료가 그 원본을 먼저 지우면 기본 설정과 가장 흔한 입력의 조합에서
    로컬 업로드 영상을 분석할 수 없다(`maiu.dispatch_source_missing`).
    """
    pid = await new_project(client, admin_token, "ret-before")
    vid = await upload_video(client, admin_token, pid, title="a")
    bucket, key = await _source(vid)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")

    await _run()
    assert storage.head_in(bucket=bucket, key=key) is not None, \
        "분석도 안 끝났는데 원본을 치웠다"

    await _mark_analyzed(vid, pid)
    await _run()
    assert storage.head_in(bucket=bucket, key=key) is None, "분석이 끝났는데 안 치웠다"


async def test_analysis_in_flight_is_not_finished(
    client: AsyncClient, admin_token: str
):
    """MI 상태가 `done`이 아니면 원본을 지우지 않는다. 분석 중에 입력을 지우면 그 분석이 실패한다."""
    pid = await new_project(client, admin_token, "ret-inflight")
    vid = await upload_video(client, admin_token, pid, title="b")
    bucket, key = await _source(vid)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")

    await _mark_analyzed(vid, pid, "analyzing")
    await _run()
    assert storage.head_in(bucket=bucket, key=key) is not None


async def test_bucket_registered_source_is_never_touched(
    client: AsyncClient, admin_token: str
):
    """버킷 등록 원본은 지우지 않는다.

    버킷 등록 원본은 원래 그 위치에 있던 외부 파일이다.
    """
    pid = await new_project(client, admin_token, "ret-bucket")
    vid = await upload_video(client, admin_token, pid, title="d")
    key = "news/2026/already-there.mp4"
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        bucket = v.source_uri.split("://")[1].split("/", 1)[0]
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            source_uri=f"s3://{bucket}/{key}"))
        await s.commit()
    await _mark_analyzed(vid, pid)

    await _run()
    assert storage.head_in(bucket=bucket, key=key) is not None, "남의 파일을 치웠다"


async def test_disposed_once_is_not_retried_forever(
    client: AsyncClient, admin_token: str
):
    """이미 정리한 원본은 다시 선택하지 않는다. 다시 선택하면 10분마다 같은 행을 다시 처리한다."""
    pid = await new_project(client, admin_token, "ret-once")
    vid = await upload_video(client, admin_token, pid, title="e")
    _, key = await _source(vid)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    await _mark_analyzed(vid, pid)

    await _run()
    async with SessionLocal() as s:
        assert (await s.get(Video, UUID(vid))).source_retained is False
    # 두 번째 회차가 이 영상을 다시 선택하면 이미 없는 키에 DELETE를 또 보낸다.
    async with SessionLocal() as s:
        picked = await _pick(s)
    assert UUID(vid) not in picked, "치운 영상을 매 회차 다시 집는다"


async def test_one_failure_does_not_take_the_batch(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """한 건이 실패해도 나머지 원본은 정리한다.

    루프가 ORM 객체를 미리 담아 두고 실패마다 `rollback()`을 부르면,
    rollback이 세션에 연결된 나머지 객체를 모두 expire시킨다(`expire_on_commit=False`와 무관하다).
    그러면 다음 반복의 `video.source_uri` 접근이 암묵적 IO가 되어 `MissingGreenlet`으로 실패한다.
    이 예외도 같은 `except`에 잡혀 잘못된 한 건 때문에 나머지가 모두 실패한다.
    이 작업은 10분 주기라 같은 곳에서 매번 다시 실패한다.
    재현 순서는 여러 건 적재, `rollback()`, 두 번째 객체의 속성 접근이다.
    """
    from api_gateway.services.media import publish

    pid = await new_project(client, admin_token, "ret-batch")
    mine = []
    for i in range(3):
        vid = await upload_video(client, admin_token, pid, title=f"batch-{i}")
        _, key = await _source(vid)
        _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
        await _mark_analyzed(vid, pid)
        mine.append(vid)

    real = publish._dispose_source
    seen: list = []

    async def flaky(db, video):
        """첫 한 건만 실패시킨다. 순서를 가정하지 않으려고 id가 아니라 호출 횟수로 센다."""
        seen.append(video.video_id)
        if len(seen) == 1:
            raise RuntimeError("일부러 실패")
        return await real(db, video)

    monkeypatch.setattr(publish, "_dispose_source", flaky)
    await _run()

    gone = 0
    for vid in mine:
        bucket, key = await _source(vid)
        if storage.head_in(bucket=bucket, key=key) is None:
            gone += 1
    assert gone >= 2, (
        f"첫 실패 뒤 나머지가 안 치워졌다 — 내 3건 중 {gone}건만 처리됐다 "
        f"(_dispose_source 호출 {len(seen)}회)"
    )


async def _fill_my_checksum(vid: str, body: bytes, monkeypatch) -> str | None:
    """이 영상의 체크섬을 beat 경로로 채우고 기록된 값을 돌려준다.

    해시는 이 영상의 키만 계산하는 스텁으로 바꾼다.
    다른 영상의 키는 일시 오류로 돌려 실패 횟수를 늘리지 않는다.
    다른 테스트도 같은 DB를 쓰므로 실제 저장소를 읽으면 다른 테스트의 영상에 실패 기록이 남는다.
    다른 xdist 워커가 단일 실행 락을 잡고 있으면 0건으로 끝나므로 몇 번 다시 부른다.
    """
    import asyncio
    import hashlib

    from api_gateway.services.media import checksum
    from botocore.exceptions import EndpointConnectionError
    from sqlalchemy import func, select

    _, mine = await _source(vid)

    def _only_mine(*, bucket: str, key: str) -> str:
        """이 영상의 키만 해시하고 나머지는 일시 오류로 돌린다."""
        if key != mine:
            raise EndpointConnectionError(endpoint_url="https://storage.example")
        return f"sha256:{hashlib.sha256(body).hexdigest()}"

    monkeypatch.setattr(checksum, "sha256_of", _only_mine)
    for _ in range(20):
        async with SessionLocal() as s:
            pending = await s.scalar(select(func.count()).select_from(
                checksum._pending_query(None).subquery()))
            await checksum.fill_missing_checksums(s, limit=max(1, pending))
        async with SessionLocal() as s:
            got = (await s.get(Video, UUID(vid))).source_sha256
        if got:
            return got
        await asyncio.sleep(0.2)
    return None


async def test_disposal_waits_for_the_checksum(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """원본 정리가 체크섬보다 먼저 돌아도 원본을 지우지 않는다.

    정리와 체크섬은 같은 주기로 돈다. 분석이 빨리 끝나면 정리가 먼저 돌 수 있다.
    정리가 원본을 먼저 지우면 체크섬을 잴 수 없어 납품 패키지의 `source_checksum`이 빈다.
    """
    import hashlib

    body = b"\x01" * 64
    pid = await new_project(client, admin_token, "ret-ck-race")
    vid = await upload_video(client, admin_token, pid, title="ck-race")
    bucket, key = await _source(vid)
    _storage.put_bytes(key, body, content_type="video/mp4")
    await _mark_analyzed(vid, pid, checksummed=False)

    async with SessionLocal() as s:
        assert UUID(vid) not in await _pick(s), "체크섬 전에 정리 대상이 됐다"
    await _run()
    assert storage.head_in(bucket=bucket, key=key) is not None, "체크섬 전에 원본을 지웠다"

    assert await _fill_my_checksum(vid, body, monkeypatch) == (
        f"sha256:{hashlib.sha256(body).hexdigest()}"
    ), "원본이 남아 있는데 체크섬을 못 채웠다"
    await _run()
    assert storage.head_in(bucket=bucket, key=key) is None, "체크섬이 끝났는데 안 치웠다"
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert v.source_retained is False
        assert v.source_sha256 is not None, "정리가 체크섬 기록을 지웠다"


async def test_disposal_proceeds_once_checksum_gives_up(
    client: AsyncClient, admin_token: str
):
    """체크섬 재시도가 상한에 닿으면 체크섬 없이 원본을 정리한다.

    상한 전에는 기다린다. 상한에 닿은 뒤에도 기다리면 원본이 스테이징에 영구히 남는다.
    """
    from api_gateway.services.media import checksum
    from api_gateway.services.media.video import merge_extra

    pid = await new_project(client, admin_token, "ret-ck-cap")
    vid = await upload_video(client, admin_token, pid, title="ck-cap")
    _, key = await _source(vid)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    await _mark_analyzed(vid, pid, checksummed=False)

    async with SessionLocal() as s:
        await merge_extra(s, UUID(vid), {"checksum_failures": checksum._MAX_RETRIES - 1})
        await s.commit()
    async with SessionLocal() as s:
        assert UUID(vid) not in await _pick(s), "체크섬 재시도가 남았는데 정리 대상이 됐다"

    async with SessionLocal() as s:
        await merge_extra(s, UUID(vid), {"checksum_failures": checksum._MAX_RETRIES})
        await s.commit()
    async with SessionLocal() as s:
        assert UUID(vid) in await _pick(s), "체크섬을 포기한 원본이 영구히 남는다"
    await _run()
    async with SessionLocal() as s:
        assert (await s.get(Video, UUID(vid))).source_retained is False


async def test_retaining_project_does_not_wait_for_the_checksum(
    client: AsyncClient, admin_token: str
):
    """원본을 보관하는 프로젝트는 체크섬을 기다리지 않고 보관 위치로 옮긴다.

    보관 위치의 원본은 계속 읽을 수 있어서 체크섬은 옮긴 뒤에도 잴 수 있다.
    """
    from api_gateway.models import Project
    from api_gateway.services.media import checksum
    from api_gateway.services.media.video import archive_key

    pid = await new_project(client, admin_token, "ret-ck-keep")
    vid = await upload_video(client, admin_token, pid, title="ck-keep")
    bucket, key = await _source(vid)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    async with SessionLocal() as s:
        p = await s.get(Project, UUID(pid))
        p.retain_original = True
        await s.commit()
    await _mark_analyzed(vid, pid, checksummed=False)

    async with SessionLocal() as s:
        assert UUID(vid) in await _pick(s), "보관 프로젝트까지 체크섬을 기다린다"
    await _run()
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        assert v.source_retained is not False
        assert v.source_uri.endswith(archive_key(UUID(vid)))
        assert v.source_sha256 is None
        left = [r[0] for r in (await s.execute(checksum._pending_query(None))).all()]
    assert UUID(vid) in left, "보관한 원본이 체크섬 후보에서 빠졌다"
    assert storage.head_in(bucket=bucket, key=archive_key(UUID(vid))) is not None
