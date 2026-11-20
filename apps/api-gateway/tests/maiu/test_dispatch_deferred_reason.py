"""보류된 분석 요청의 사유 표시 테스트.

요청 대기 행의 "보류됨"에 보류 사유를 함께 표시한다. 서버가 보류 표시에 종류를 남긴다.
영상 응답은 짧은 이름·전체 사유·다음 재요청 시각을 돌려준다. 화면은 사유 문장을 나눠 종류를 다시 판정하지 않는다.
세 가지를 확인한다.

  1. 상태 코드로 종류를 정한다(429 요청량 제한·408/425 응답 지연·5xx 오류·응답 없음 연결 실패).
  2. 429로 보류된 영상의 목록 응답에 `분석 서비스 요청량 제한`과 전체 사유, 다음 재요청 시각이 실린다.
  3. 종류가 없는 옛 표시는 일반 이름으로 싣고 시각은 비운다.
"""
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Video
from api_gateway.services import error_text
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.naver_client import MaiuTransientError
from httpx import AsyncClient
from tests._helpers import new_project, upload_video

_THROTTLED = ('MAIU GET /x → 429 {"errorCode":"410","message":"Throttle Limited"}')


class _Throttled(FakeMaiuClient):
    """모든 등록 요청을 429로 거절하는 가짜 분석 서비스."""

    async def dispatch(self, *, request: dict) -> str:
        """요청량 제한으로 거절한다."""
        raise MaiuTransientError(_THROTTLED, status=429)


@pytest.mark.parametrize(("status", "kind"), [
    (429, "throttled"), (408, "timeout"), (425, "timeout"),
    (500, "server_error"), (503, "server_error"), (None, "unreachable"), (400, "other"),
])
def test_kind_follows_the_status_code(status, kind):
    """종류를 상태 코드로 정하고 이름을 `ANALYSIS_TRANSIENT_LABEL` 한 곳에서 꺼내는지 확인한다."""
    assert error_text.analysis_transient_kind(status) == kind
    assert kind in error_text.ANALYSIS_TRANSIENT_LABEL


async def _clear(vid: str) -> None:
    """보류 표시를 지운다.

    남겨 두면 다른 테스트의 보류 재요청(`redispatch_deferred`)이 이 영상까지 다시 요청해 그 테스트의 건수가 어긋난다.
    """
    from api_gateway.services.media.video import merge_extra
    async with SessionLocal() as s:
        await merge_extra(s, UUID(vid), {}, remove=("dispatch_deferred",))
        await s.commit()


async def _row(client: AsyncClient, token: str, pid: str, vid: str) -> dict:
    """영상 목록에서 그 영상 행을 읽는다."""
    r = await client.get(f"/projects/{pid}/videos", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return next(v for v in r.json() if v["video_id"] == vid)


async def test_throttled_dispatch_names_the_reason(client: AsyncClient, admin_token: str):
    """요청량 제한으로 보류된 행에 짧은 이름·전체 사유·다음 재요청 시각을 싣는다."""
    maiu_client.set_client(_Throttled())
    try:
        pid = await new_project(client, admin_token, "deferred-reason")
        vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
        r = await client.post(f"/projects/{pid}/maiu/dispatch",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"video_ids": [vid]})
        assert r.status_code == 200 and r.json()["deferred"] == 1, r.text
    finally:
        maiu_client._client_singleton = None

    try:
        d = (await _row(client, admin_token, pid, vid))["dispatch_deferred"]
    finally:
        await _clear(vid)
    assert d["kind"] == "throttled"
    assert d["label"] == "분석 서비스 요청량 제한"
    assert "요청량 제한" in d["reason"] and "Throttle Limited" in d["reason"], d["reason"]
    at, by = datetime.fromisoformat(d["at"]), datetime.fromisoformat(d["retry_by"])
    assert timedelta(seconds=30) < by - at <= timedelta(minutes=2), (at, by)


async def test_old_mark_without_kind_gets_the_general_name(client: AsyncClient, admin_token: str):
    """종류가 없는 옛 보류 표시는 일반 이름으로 싣고 다음 재요청 시각은 비운다."""
    pid = await new_project(client, admin_token, "deferred-old")
    vid = await upload_video(client, admin_token, pid, policy="pre_cleared", probed=True)
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.extra = {**(v.extra or {}), "dispatch_deferred": {
            "at": "2026-10-02T01:00:00+00:00", "reason": "분석 서비스 오류 (503)",
            "scene_options": None}}
        await s.commit()
    try:
        d = (await _row(client, admin_token, pid, vid))["dispatch_deferred"]
    finally:
        await _clear(vid)
    assert d == {"kind": None, "label": "분석 서비스 일시 오류", "reason": "분석 서비스 오류 (503)",
                 "at": "2026-10-02T01:00:00Z", "retry_by": None}, d
