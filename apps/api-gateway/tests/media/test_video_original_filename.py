"""영상의 원래 파일 이름 저장 테스트. 업로드 등록과 버킷 등록 두 경로를 확인한다.

원래 파일 이름은 납품 패키지의 `sources.csv`가 원본 파일을 가리키는 참고 정보다.
업로드 키(`intake/{id}/source`)와 편집할 수 있는 제목에는 파일 이름이 남지 않는다.
"""
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from httpx import AsyncClient
from pydantic import ValidationError
from reborn_contracts import VideoCreateRequest
from reborn_contracts.video import ORIGINAL_FILENAME_KEY
from sqlalchemy import select
from tests._helpers import new_project
from tests.media.test_storage_register import _new_project, _register


def _h(token: str) -> dict:
    """Bearer 인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(("raw", "kept"), [
    ("뉴스 A.mp4", "뉴스 A.mp4"),
    ("C:\\fakepath\\뉴스 A.mp4", "뉴스 A.mp4"),
    ("/Users/me/영상/a.mov", "a.mov"),
    ("  a.mp4  ", "a.mp4"),
    ("..", None),
    ("dir/", None),
    (None, None),
])
def test_request_keeps_only_the_file_name(raw, kept):
    """경로 부분을 떼고 이름만 남기는지 확인한다. 이름이 남지 않으면 `None`이다."""
    assert VideoCreateRequest(title="t", original_filename=raw).original_filename == kept


@pytest.mark.parametrize("raw", ["a\nb.mp4", "a\tb.mp4", "a\x00.mp4", "a\x7f.mp4", "a\x85.mp4"])
def test_request_rejects_control_characters(raw):
    """제어 문자가 있는 이름을 거절하는지 확인한다. 줄바꿈이 있으면 CSV 한 행이 갈라진다."""
    with pytest.raises(ValidationError):
        VideoCreateRequest(title="t", original_filename=raw)


def test_request_rejects_names_over_255():
    """255자를 넘는 이름을 거절하는지 확인한다."""
    with pytest.raises(ValidationError):
        VideoCreateRequest(title="t", original_filename="a" * 252 + ".mp4")


async def test_upload_registration_stores_the_original_filename(
    client: AsyncClient, admin_token: str,
):
    """업로드 등록이 원래 파일 이름을 `extra`에 저장하고 응답의 `metadata`에 싣는지 확인한다."""
    pid = await new_project(client, admin_token, "orig-name")
    r = await client.post(f"/projects/{pid}/videos", headers=_h(admin_token), json={
        "title": "편집한 제목", "original_filename": "C:\\fakepath\\0901 9시 뉴스 샘플 A.mp4",
    })
    assert r.status_code == 201, r.text
    vid = r.json()["video_id"]
    assert r.json()["metadata"][ORIGINAL_FILENAME_KEY] == "0901 9시 뉴스 샘플 A.mp4"
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
    assert v.extra[ORIGINAL_FILENAME_KEY] == "0901 9시 뉴스 샘플 A.mp4"
    assert v.title == "편집한 제목"


async def test_upload_registration_without_a_name_leaves_no_key(
    client: AsyncClient, admin_token: str,
):
    """이름을 보내지 않으면 키를 만들지 않는지 확인한다. 빈 문자열로 채우지 않는다."""
    pid = await new_project(client, admin_token, "orig-none")
    r = await client.post(f"/projects/{pid}/videos", headers=_h(admin_token), json={"title": "t"})
    assert r.status_code == 201, r.text
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(r.json()["video_id"]))
    assert ORIGINAL_FILENAME_KEY not in v.extra


async def test_upload_registration_rejects_control_characters(
    client: AsyncClient, admin_token: str,
):
    """제어 문자가 있는 이름이면 422로 거절하고 영상을 만들지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "orig-bad")
    r = await client.post(f"/projects/{pid}/videos", headers=_h(admin_token), json={
        "title": "t", "original_filename": "a\nb.mp4",
    })
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        n = (await s.scalars(select(Video).where(Video.project_id == UUID(pid)))).all()
    assert n == []


async def test_bucket_registration_stores_the_key_basename(
    client: AsyncClient, admin_token: str,
):
    """버킷 등록이 키의 마지막 부분을 원래 파일 이름으로 저장하고 키도 그대로 두는지 확인한다."""
    pid = await _new_project(client, admin_token)
    r = await _register(client, admin_token, pid, ["news/2026/0901 9시 뉴스 샘플 A.mp4"])
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        v = (await s.scalars(select(Video).where(Video.project_id == UUID(pid)))).one()
    assert v.extra[ORIGINAL_FILENAME_KEY] == "0901 9시 뉴스 샘플 A.mp4"
    assert v.extra["key"] == "news/2026/0901 9시 뉴스 샘플 A.mp4"
