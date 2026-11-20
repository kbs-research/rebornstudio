"""서명은 맞지만 이 DB에 없는 사용자의 토큰으로 로그아웃·갱신할 때를 보는 테스트.

같은 서명 키를 쓰는 다른 환경이 발급한 토큰이나 지워진 사용자의 토큰은 서명 검증을 통과한다.
감사 이벤트의 `actor_user_id`는 `users`를 가리키는 외래 키라서 없는 사용자로 기록하면 commit이
외래 키 위반으로 실패하고 응답이 500이 된다. 로그아웃은 언제나 쿠키를 지우고 끝나야 한다.
"""
from uuid import uuid4

from api_gateway import security
from api_gateway.services.auth import service as auth_service
from httpx import AsyncClient
from tests._helpers import REFRESH_COOKIE, events, refresh_headers, refresh_with_cookie


def _cleared_cookie(response) -> bool:
    """응답이 refresh 쿠키를 지우는 `Set-Cookie`(Max-Age=0)를 실었는지 돌려준다."""
    lines = [
        v for k, v in response.headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(f"{REFRESH_COOKIE}=")
    ]
    return len(lines) == 1 and "max-age=0" in lines[0].lower().replace(" ", "")


async def test_logout_with_unknown_user_token_clears_cookie(client: AsyncClient):
    """없는 사용자의 refresh 토큰으로 로그아웃하면 204로 쿠키를 지우고 토큰을 폐기하는지 확인한다."""
    ghost = uuid4()
    token = security.create_refresh_token(ghost)
    r = await client.post("/auth/logout", headers=refresh_headers(token))
    assert r.status_code == 204, r.text
    assert _cleared_cookie(r), r.headers
    assert (await refresh_with_cookie(client, token)).status_code == 401
    assert await events(event_type="auth.logout", target_id=str(ghost)) == []


async def test_logout_with_non_uuid_subject_clears_cookie(client: AsyncClient):
    """서명은 맞고 sub가 UUID가 아닌 refresh 토큰으로 로그아웃해도 204인지 확인한다."""
    token = security.create_refresh_token("not-a-uuid")
    r = await client.post("/auth/logout", headers=refresh_headers(token))
    assert r.status_code == 204, r.text
    assert _cleared_cookie(r), r.headers


async def test_refresh_reuse_with_unknown_user_is_401(client: AsyncClient, monkeypatch):
    """없는 사용자의 폐기된 refresh 토큰을 유예 뒤에 다시 보내면 500이 아니라 401인지 확인한다."""
    monkeypatch.setattr(auth_service, "REFRESH_REUSE_GRACE_SECONDS", -1)
    ghost = uuid4()
    token = security.create_refresh_token(ghost)
    security.revoke(token)
    r = await refresh_with_cookie(client, token)
    assert r.status_code == 401, r.text
    assert await events(event_type="auth.refresh_reused", target_id=str(ghost)) == []
