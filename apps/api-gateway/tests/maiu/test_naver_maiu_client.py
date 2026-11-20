"""NaverMaiuClient 단위 테스트.

외부 API는 호출하지 않고 서명·파싱·매핑·경로 게이트만 검증한다.
"""
import asyncio
import base64
import hashlib
import hmac

import pytest
from api_gateway.services.maiu.naver_client import (
    NaverMaiuClient,
    _parse_uri,
    _split_analysis_id,
    _to_summary,
)


@pytest.fixture
def cli() -> NaverMaiuClient:
    """기본 설정의 NaverMaiuClient를 만든다."""
    return NaverMaiuClient(
        access_key="ak", secret_key="sk",
        workspace="ws", project_id="123",
        default_bucket="my-bucket",
    )


def test_sign_matches_hmac_sha256_base64(cli: NaverMaiuClient):
    """_sign은 메서드·경로·타임스탬프·액세스 키를 HMAC-SHA256으로 서명해 base64로 돌려준다."""
    ts = "1700000000000"
    path = "/api/v1/workspaces/ws/projects/123/video-assets"
    expected_msg = f"GET {path}\n{ts}\nak"
    expected = base64.b64encode(
        hmac.new(b"sk", expected_msg.encode(), hashlib.sha256).digest()
    ).decode()
    assert cli._sign("GET", path, ts) == expected


def test_headers_include_required_ncp_fields(cli: NaverMaiuClient):
    """요청 헤더에 NCP 필수 필드(액세스 키·타임스탬프·서명)가 들어간다."""
    h = cli._headers("POST", "/x")
    assert h["x-ncp-iam-access-key"] == "ak"
    assert h["x-ncp-apigw-timestamp"].isdigit()
    assert h["x-ncp-apigw-signature-v2"]
    assert h["Content-Type"] == "application/json"


def test_parse_uri_s3_scheme():
    """s3:// URI를 버킷과 키로 나눈다."""
    assert _parse_uri("s3://bkt/a/b/c.mp4") == ("bkt", "a/b/c.mp4")


def test_parse_uri_ncp_scheme():
    """ncp:// URI를 버킷과 키로 나눈다."""
    assert _parse_uri("ncp://bkt/x.mp4") == ("bkt", "x.mp4")


def test_parse_uri_bare_key_uses_default_bucket():
    """scheme 없는 키는 기본 버킷을 쓴다."""
    assert _parse_uri("videos/x.mp4", default_bucket="db") == ("db", "videos/x.mp4")


def test_parse_uri_bare_key_without_default_raises():
    """scheme 없는 키에 기본 버킷도 없으면 RuntimeError를 낸다."""
    with pytest.raises(RuntimeError):
        _parse_uri("videos/x.mp4")


def test_split_analysis_id_pair():
    """분석 ID "asset:index"를 두 값으로 나눈다. index는 비어 있어도 된다."""
    assert _split_analysis_id("5678:1001") == ("5678", "1001")
    assert _split_analysis_id("5678:") == ("5678", "")


def test_to_summary_extracts_segments():
    """_to_summary는 인덱스 응답에서 세그먼트를 뽑아낸다."""
    raw = {
        "result": {
            "indexId": 1001,
            "indexDetails": {
                "durationMs": 20054, "fps": 30, "videoUrl": "https://x",
                "segments": [
                    {
                        "start": 0, "end": 275, "startMs": 0, "endMs": 9176,
                        "scene": 1, "shot": 1,
                        "sceneThumbnailUrl": "https://thumb/1",
                        "speeches": [{"text": "안녕", "pid": "1", "name": "화자1"}],
                        "keywords": [{"category": "행동", "text": "뉴스", "score": 87}],
                        "people": [],
                    }
                ],
            },
        }
    }
    out = _to_summary(raw)
    assert out["indexId"] == 1001
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["startMs"] == 0 and s["endMs"] == 9176
    assert s["scene"] == 1 and s["shot"] == 1
    assert s["speeches"][0]["text"] == "안녕"
    assert s["keywords"][0]["text"] == "뉴스"


def test_constructor_requires_credentials():
    """액세스 키가 없으면 생성자가 RuntimeError를 낸다. 서버 환경 설정 값으로 대신 채우지 않는다."""
    with pytest.raises(RuntimeError, match="access_key"):
        NaverMaiuClient(access_key="", secret_key="sk", workspace="w", project_id="1")


def test_wp_path(cli: NaverMaiuClient):
    """_wp는 워크스페이스·프로젝트 경로 접두사를 만든다."""
    assert cli._wp() == "/api/v1/workspaces/ws/projects/123"


# --- 경로 게이트(index_api): 구 경로 video-assets와 신 경로 assets/index ----------

@pytest.fixture
def cli_v1() -> NaverMaiuClient:
    """구 경로(video-assets)를 쓰는 클라이언트를 만든다."""
    return NaverMaiuClient(
        access_key="ak", secret_key="sk", workspace="ws", project_id="123",
        default_bucket="my-bucket", use_index_api=False,
    )


@pytest.fixture
def cli_v2() -> NaverMaiuClient:
    """신 경로(assets/index)를 쓰는 클라이언트를 만든다."""
    return NaverMaiuClient(
        access_key="ak", secret_key="sk", workspace="ws", project_id="123",
        default_bucket="my-bucket", use_index_api=True,
    )


def test_paths_v1_use_video_assets(cli_v1: NaverMaiuClient):
    """구 경로 모드는 모든 경로에 video-assets를 쓴다."""
    c = cli_v1
    wp = "/api/v1/workspaces/ws/projects/123"
    assert c._asset_seg() == "video-assets"
    assert c._register_path() == f"{wp}/video-assets/import/object-storage"
    assert c._asset_base("5") == f"{wp}/video-assets/5"
    assert c._analysis_request_path("5") == f"{wp}/video-assets/5/analyze"
    assert c._analysis_status_path("5") == f"{wp}/video-assets/5/analyze-status"
    assert c._index_get_path("5", "9") == f"{wp}/video-assets/5/indexes/9"
    assert c._cancel_path("5") == f"{wp}/video-assets/5/analyze"


def test_paths_v2_use_assets_index(cli_v2: NaverMaiuClient):
    """신 경로 모드는 assets와 index 경로를 쓴다."""
    c = cli_v2
    wp = "/api/v1/workspaces/ws/projects/123"
    assert c._asset_seg() == "assets"
    assert c._register_path() == f"{wp}/assets/import/object-storage"
    assert c._asset_base("5") == f"{wp}/assets/5"
    assert c._analysis_request_path("5") == f"{wp}/assets/5/index"
    assert c._analysis_status_path("5") == f"{wp}/assets/5/index/status"
    # 신 모델은 에셋당 최신 인덱스 1건이라 index_id를 줘도 경로에 넣지 않는다.
    assert c._index_get_path("5", "9") == f"{wp}/assets/5/index"
    assert "indexes" not in c._index_get_path("5", "9")
    assert c._cancel_path("5") == f"{wp}/assets/5/index/cancel"


def test_scene_summary_base_v2_drops_index(cli_v2: NaverMaiuClient):
    """신 모델의 장면 요약 경로에는 index가 들어가지 않는다."""
    # 신 모델은 에셋 단위라 index_id를 해석하지 않고 경로를 만든다. 네트워크를 쓰지 않는다.
    base = asyncio.run(cli_v2._scene_summary_base("5678:"))
    assert base == "/api/v1/workspaces/ws/projects/123/assets/5678/scene-summary"


def test_default_index_api_follows_settings(monkeypatch):
    """use_index_api를 주지 않으면 settings.maiu_use_index_api를 따른다."""
    from api_gateway import config as cfg
    monkeypatch.setattr(cfg.settings, "maiu_use_index_api", True)
    c = NaverMaiuClient(access_key="ak", secret_key="sk", workspace="w", project_id="1")
    assert c.index_api is True
    monkeypatch.setattr(cfg.settings, "maiu_use_index_api", False)
    c2 = NaverMaiuClient(access_key="ak", secret_key="sk", workspace="w", project_id="1")
    assert c2.index_api is False


class _PagedMI:
    """MI 목록 API를 흉내 내는 `_request`. `pageNum`이 없으면 10001로 거절하고 페이지로 나눠 돌려준다."""

    def __init__(self, total: int, key: str) -> None:
        """항목 `total`개를 `key` 이름으로 돌려주도록 준비한다."""
        self.total = total
        self.key = key
        self.paths: list[str] = []

    async def __call__(self, method: str, path: str, *, json=None) -> dict:
        """페이지 번호와 크기에 맞는 항목을 돌려준다."""
        self.paths.append(path)
        _, _, query = path.partition("?")
        args = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
        if "pageNum" not in args:
            raise RuntimeError(f'MAIU GET {path} → 400 {{"code":"10001"}}')
        page, size = int(args["pageNum"]), int(args.get("pageSize", "1000"))
        items = [{"workspaceName": f"w{i}", "projectId": str(i)}
                 for i in range((page - 1) * size, min(page * size, self.total))]
        return {"result": {self.key: items, "totalCount": self.total}}


@pytest.mark.parametrize("method,key", [("list_workspaces", "content"), ("list_projects", "content")])
def test_list_calls_send_page_args_and_read_every_page(cli: NaverMaiuClient, method, key):
    """목록 조회가 `pageNum`·`pageSize`를 붙이고 `totalCount`만큼 다음 페이지까지 읽는지 확인한다."""
    mi = _PagedMI(total=150, key=key)
    cli._request = mi
    rows = asyncio.run(getattr(cli, method)())
    assert len(rows) == 150
    assert [p.split("?")[1] for p in mi.paths] == [
        "pageNum=1&pageSize=100", "pageNum=2&pageSize=100"]
