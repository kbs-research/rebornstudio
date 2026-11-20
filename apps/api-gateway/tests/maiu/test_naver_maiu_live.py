"""NaverMaiuClient가 실제 NCP API를 호출하는 통합 테스트. 환경 변수로 켠다.

켜는 방법: `MAIU_LIVE_TEST=1 pytest tests/maiu/test_naver_maiu_live.py`
평소에는 conftest의 `pytest_collection_modifyitems`가 자동으로 skip한다.

호출은 최소로 한다.
  - 모든 호출은 read-only다. 서버 상태를 바꾸지 않는다.
  - 이미 등록·분석이 끝난 known-good asset/index를 쓴다.
  - 새 register/analyze를 트리거하지 않는다. 목적은 회귀 검사다.

필수 환경 변수:
  MAIU_ENABLED=true
  MAIU_LIVE_CONNECTION  `DATABASE_URL` DB의 자격증명 연결에 등록한 MI 연결 이름
  MAIU_LIVE_WORKSPACE   그 연결로 읽을 워크스페이스 이름
  MAIU_LIVE_PROJECT_ID  그 워크스페이스의 MI 프로젝트 id

자격증명은 등록한 연결에서만 읽는다. 앱 설정에는 NCP 키가 없다.

known-good 자산의 ID는 워크스페이스·프로젝트에 묶여 있다.
설정이 바뀌면 같은 ID가 다른 프로젝트를 가리켜 `10010 에셋 파일이 존재하지 않습니다`가 난다.
새 값을 구하려면 dev DB에서 성공한 잡 하나를 고른다.

    select external_ref from analysis_job
     where adapter_key = 'maiu' and status = 'done' order by created_at desc limit 1;

그 값을 `MAIU_LIVE_ASSET_ID`로 넘기거나 아래 기본값을 갱신한다.
`MAIU_LIVE_INDEX_ID`를 넘기지 않으면 아래 기본값을 쓴다. analyze-status 테스트가 그 값이
자산의 현재 인덱스와 같은지 확인한다.
"""
import os

import pytest
from api_gateway.config import settings
from api_gateway.database import SessionLocal
from api_gateway.models import Connection
from api_gateway.services import platform_credentials
from api_gateway.services.maiu.naver_client import NaverMaiuClient, _to_summary
from sqlalchemy import select

#: 등록·분석이 끝난 자산의 ID(`analysis_job.external_ref`).
#: 프로젝트가 바뀌면 위 쿼리로 새 값을 구한다.
KNOWN_ASSET_ID = os.getenv("MAIU_LIVE_ASSET_ID", "26282049")
KNOWN_INDEX_ID = os.getenv("MAIU_LIVE_INDEX_ID", "26079836")
#: 실호출에 쓸 MI 연결 이름과 그 연결로 읽을 워크스페이스·MI 프로젝트 id.
LIVE_CONNECTION = os.getenv("MAIU_LIVE_CONNECTION", "")
LIVE_WORKSPACE = os.getenv("MAIU_LIVE_WORKSPACE", "")
LIVE_PROJECT_ID = os.getenv("MAIU_LIVE_PROJECT_ID", "")


def _live_env_ok() -> tuple[bool, str]:
    """실호출 조건을 앱이 읽는 값(`settings`)으로 판정한다. `os.getenv`를 쓰지 않는다.

    이 저장소의 설정은 pydantic이 `.env`에서 읽으므로 OS 환경 변수에는 값이 없다.
    가드가 `os.getenv("MAIU_ENABLED")`처럼 앱과 다른 값을 읽으면,
    자격 증명이 모두 있고 `MAIU_LIVE_TEST=1`로 켜도 이 테스트들이 알림 없이 skip된다.
    """
    if not settings.maiu_enabled:
        return False, "MAIU_ENABLED!=true"
    for name, value in (("MAIU_LIVE_CONNECTION", LIVE_CONNECTION),
                        ("MAIU_LIVE_WORKSPACE", LIVE_WORKSPACE),
                        ("MAIU_LIVE_PROJECT_ID", LIVE_PROJECT_ID)):
        if not value:
            return False, f"{name} 미설정"
    return True, ""


@pytest.fixture
async def live_client() -> NaverMaiuClient:
    """실호출 조건이 없으면 skip하고, 있으면 등록한 MI 연결의 키로 실제 클라이언트를 만든다."""
    ok, reason = _live_env_ok()
    if not ok:
        pytest.skip(reason)
    async with SessionLocal() as db:
        conn_id = await db.scalar(select(Connection.id).where(
            Connection.provider == platform_credentials.ANALYSIS_PROVIDER,
            Connection.name == LIVE_CONNECTION,
        ))
        if conn_id is None:
            pytest.skip(f"MI 연결 {LIVE_CONNECTION} 없음")
        await platform_credentials.refresh(db)
    creds = platform_credentials.analysis_for(platform_credentials.AnalysisTarget(
        connection_id=str(conn_id), workspace=LIVE_WORKSPACE, project_id=LIVE_PROJECT_ID,
    ))
    return NaverMaiuClient(
        access_key=creds.access_key, secret_key=creds.secret_key, base_url=creds.base_url,
        workspace=creds.workspace, project_id=creds.project_id,
    )


@pytest.mark.live
async def test_live_import_status_returns_completed(live_client: NaverMaiuClient):
    """known-good asset의 import-status가 COMPLETED이고 metaData를 포함한다(호출 1건)."""
    raw = await live_client.get_import_status(KNOWN_ASSET_ID)
    assert raw["uploadStatus"] in ("COMPLETED", "UPLOAD_COMPLETED")
    assert "metaData" in raw
    meta = raw["metaData"]
    # MAIU가 분석한 메타다. picture_frame.mp4: 1280x720, 약 11초, 약 1MB mp4
    assert meta.get("durationMs", 0) > 0
    assert meta.get("size", 0) > 0
    assert meta.get("format") == "mp4"
    assert meta.get("resolution")  # "1280x720" 형식


@pytest.mark.live
async def test_live_analyze_status_returns_completed_with_index(
    live_client: NaverMaiuClient,
):
    """known-good asset의 analyze-status가 ANALYZE_COMPLETED이고 indexId를 준다(호출 1건)."""
    raw = await live_client.get_analyze_status(KNOWN_ASSET_ID)
    assert raw["status"] in ("ANALYZE_COMPLETED", "COMPLETED")
    assert raw.get("indexId") == int(KNOWN_INDEX_ID)


@pytest.mark.live
async def test_live_index_detail_roundtrip_via_to_summary(
    live_client: NaverMaiuClient,
):
    """known-good index를 get_index_detail로 읽고 _to_summary로 메타를 뽑는다(호출 1건)."""
    raw = await live_client.get_index_detail(KNOWN_ASSET_ID, KNOWN_INDEX_ID)
    # 응답 전체의 raw 형식이다. analysisSetting과 indexDetails가 모두 있다.
    assert "analysisSetting" in raw
    assert "indexDetails" in raw

    summary = _to_summary(raw)
    # reconciler가 의존하는 필수 키가 모두 있다.
    expected_keys = {"segments", "durationMs", "fps", "frameCount", "videoUrl",
                     "indexId", "analysis_setting", "index_meta"}
    assert expected_keys <= set(summary.keys())
    # 인덱스 id가 기준값과 같고 길이와 구간이 비어 있지 않은지 확인한다.
    assert summary["indexId"] == int(KNOWN_INDEX_ID)
    assert summary["durationMs"] > 0
    assert len(summary["segments"]) > 0
    # 첫 세그먼트의 키를 확인한다.
    seg0 = summary["segments"][0]
    for k in ("start", "end", "startMs", "endMs", "scene", "shot",
              "sceneThumbnailUrl", "speeches", "keywords", "people"):
        assert k in seg0, f"segment missing key: {k}"
    # analysis_setting 메타에 분석 엔진과 sceneRange가 있는지 확인한다.
    assert summary["analysis_setting"].get("analysisEngine")
    assert summary["analysis_setting"].get("sceneRange")
