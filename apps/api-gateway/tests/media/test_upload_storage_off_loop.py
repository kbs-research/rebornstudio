"""업로드 경로의 저장소 호출이 이벤트 루프 스레드 밖에서 도는지 보는 테스트.

멀티파트 시작·파트 목록·합치기·HEAD는 boto3 동기 호출이라 한 번에 수백 ms가 걸릴 수 있다.
API는 uvicorn 프로세스 하나라서 루프에서 부르면 그동안 다른 요청이 모두 멈춘다.
"""
import threading

from api_gateway.services.media import storage
from httpx import AsyncClient
from tests._helpers import new_project, put_video_file


async def test_upload_storage_calls_run_off_the_loop(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """업로드 시작과 완료의 저장소 호출이 모두 루프 스레드가 아닌 스레드에서 도는지 확인한다."""
    seen: dict[str, list[int]] = {}
    for name in ("create_multipart", "list_parts", "complete_multipart", "head"):
        real = getattr(storage, name)

        def wrapper(*args, _real=real, _name=name, **kwargs):
            """호출 스레드를 기록하고 실제 함수를 부른다."""
            seen.setdefault(_name, []).append(threading.get_ident())
            return _real(*args, **kwargs)

        monkeypatch.setattr(storage, name, wrapper)

    pid = await new_project(client, admin_token, "upload-thread")
    vid = (await client.post(f"/projects/{pid}/videos",
                             headers={"Authorization": f"Bearer {admin_token}"},
                             json={"title": "t", "copyright_policy": "pre_cleared"})).json()["video_id"]
    r = await put_video_file(client, admin_token, vid)
    assert r.status_code == 200, r.text

    loop_thread = threading.get_ident()
    assert set(seen) >= {"create_multipart", "list_parts", "complete_multipart", "head"}, seen
    on_loop = {name for name, threads in seen.items() if loop_thread in threads}
    assert on_loop == set(), f"루프 스레드에서 부른 저장소 호출: {sorted(on_loop)}"
