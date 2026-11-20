"""원본을 보관 경로로 옮길 때의 순서 테스트. 어느 단계에서 멈춰도 행이 없는 파일을 가리키지 않는지 본다.

복사한 뒤 원래 키를 먼저 지우고 `source_uri`를 나중에 커밋하면, 그 사이에 워커가 비정상 종료될 때
행은 지워진 업로드 키를 가리키고 보관본은 아무 행도 가리키지 않는다.
순서는 복사, `source_uri` 갱신과 커밋, 원래 키 삭제다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Project, Video
from api_gateway.services.analysis.status_map import neutral_status
from api_gateway.services.media import retention, storage
from api_gateway.services.media.video import archive_key
from httpx import AsyncClient
from sqlalchemy import update
from tests import _storage
from tests._helpers import new_project, upload_video


async def _retaining_analyzed_video(client: AsyncClient, token: str) -> UUID:
    """원본을 보관하는 프로젝트에 분석이 끝난 업로드 영상 하나를 만든다."""
    pid = await new_project(client, token, "archive-order")
    vid = UUID(await upload_video(client, token, pid, title="archive"))
    async with SessionLocal() as s:
        await s.execute(update(Project).where(Project.project_id == UUID(pid))
                        .values(retain_original=True))
        s.add(AnalysisJob(adapter_key="maiu", video_id=vid, project_id=UUID(pid),
                          stage="done", status=neutral_status("done")))
        await s.commit()
    return vid


async def test_crash_while_deleting_the_upload_keeps_the_row_on_the_archive(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """원래 키를 지운 직후 멈춰도 행은 보관본을 가리킨다. 남은 업로드 키는 지워도 되는 사본이다."""
    vid = await _retaining_analyzed_video(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        bucket, key = v.source_uri.split("://")[1].split("/", 1)
    _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    real_delete = storage.delete_object

    def _delete_then_crash(*, bucket: str, key: str, store=None) -> None:
        """객체를 지운 뒤 커밋 전에 워커가 멈춘 상황을 흉내 낸다."""
        real_delete(bucket=bucket, key=key, store=store)
        raise RuntimeError("worker stopped")

    monkeypatch.setattr(storage, "delete_object", _delete_then_crash)
    async with SessionLocal() as s:
        await retention.dispose_finished_sources(s)
    monkeypatch.undo()

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
    assert v.source_uri == f"s3://{bucket}/{archive_key(vid)}", "행이 지워진 업로드 키를 가리킨다"
    assert storage.head_in(bucket=bucket, key=archive_key(vid)) is not None


async def test_archive_tolerates_an_upload_key_that_is_already_gone(
    client: AsyncClient, admin_token: str,
):
    """보관본이 이미 있고 업로드 키가 사라진 영상도 정리를 마친다. 앞 실행이 복사 뒤 멈춘 경우다."""
    vid = await _retaining_analyzed_video(client, admin_token)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        bucket, key = v.source_uri.split("://")[1].split("/", 1)
    _storage.put_bytes(archive_key(vid), b"\x00" * 64, content_type="video/mp4")
    storage.delete_object(bucket=bucket, key=key)
    async with SessionLocal() as s:
        await retention.dispose_finished_sources(s)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
    assert v.source_uri == f"s3://{bucket}/{archive_key(vid)}"
