"""MI 클라이언트가 워크스페이스·프로젝트 값을 경로 한 칸으로만 쓰는지 보는 테스트.

워크스페이스 이름과 MI 프로젝트 id는 프로젝트 관리자가 저장하는 값이다. 값에 `/`·`?`·`#`이 있으면
조직 키로 서명한 요청이 다른 MI 경로로 나간다. 값은 경로 한 칸으로 인코딩하고 `.`·`..`은 받지 않는다.
"""
import pytest
from api_gateway.services.maiu.naver_client import NaverMaiuClient


def _client(workspace: str, project_id: str = "123") -> NaverMaiuClient:
    """주어진 워크스페이스·프로젝트로 클라이언트를 만든다."""
    return NaverMaiuClient(
        access_key="ak", secret_key="sk", workspace=workspace, project_id=project_id,
        default_bucket="sample-bucket",
    )


def test_plain_names_stay_unchanged():
    """영문·숫자·하이픈·밑줄 이름은 경로에 그대로 들어가는지 확인한다."""
    c = _client("sample-ws_01", "123")
    assert c._ws() == "/api/v1/workspaces/sample-ws_01"
    assert c._wp() == "/api/v1/workspaces/sample-ws_01/projects/123"


@pytest.mark.parametrize(("workspace", "project_id"), [
    ("ws/../other", "123"),
    ("ws?x=1", "123"),
    ("ws#frag", "123"),
    ("ws", "123/../../workspaces"),
])
def test_path_characters_are_encoded_into_one_segment(workspace: str, project_id: str):
    """경로 구분 문자가 있는 값이 경로 한 칸으로 인코딩되는지 확인한다."""
    path = _client(workspace, project_id)._wp()
    tail = path.removeprefix("/api/v1/workspaces/")
    ws_seg, rest = tail.split("/projects/", 1)
    for seg in (ws_seg, rest):
        assert "/" not in seg and "?" not in seg and "#" not in seg, path


@pytest.mark.parametrize("bad", [".", ".."])
def test_dot_segments_are_rejected(bad: str):
    """`.`·`..` 워크스페이스나 프로젝트 id로는 클라이언트를 만들지 않는지 확인한다."""
    with pytest.raises(RuntimeError):
        _client(bad)
    with pytest.raises(RuntimeError):
        _client("ws", bad)
