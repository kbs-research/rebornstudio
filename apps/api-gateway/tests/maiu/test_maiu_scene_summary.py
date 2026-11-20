"""MAIU 장면 요약 클라이언트 테스트. NaverMaiuClient 매핑과 FakeMaiuClient를 확인한다."""
import pytest
from api_gateway.services.maiu.client import FakeMaiuClient
from api_gateway.services.maiu.naver_client import _to_scene_summaries


def test_to_scene_summaries_maps_pdf_shape():
    """PDF 예시 응답(sceneSummaries[].sceneSummary.description)을 평탄한 dict 목록으로 바꾼다."""
    resp = {
        "result": {
            "sceneSummaries": [
                {
                    "scene": 1, "startMs": 1000, "endMs": 3000,
                    "sceneSummary": {"description": "진행자가 광화문 광장에서 인사한다.",
                                     "metadataChangedAfterSummary": False},
                },
            ]
        }
    }
    out = _to_scene_summaries(resp)
    assert out == [
        {"scene": 1, "startMs": 1000, "endMs": 3000,
         "description": "진행자가 광화문 광장에서 인사한다."},
    ]


def test_to_scene_summaries_unwrapped_and_empty():
    """result로 감싸지 않은 응답과 빈 응답도 안전하게 처리한다."""
    assert _to_scene_summaries({"sceneSummaries": []}) == []
    assert _to_scene_summaries({}) == []


@pytest.mark.asyncio
async def test_fake_scene_summary_roundtrip():
    """Fake의 요청·상태·조회 응답을 확인한다.

    요청은 id를, 상태 조회는 `SUMMARIZE_COMPLETED`를, 결과 조회는 scene 1·2의 description 2건을 돌려준다.
    """
    fc = FakeMaiuClient()
    sid = await fc.request_scene_summary("fake-analysis-abc")
    assert isinstance(sid, str) and sid
    assert await fc.check_scene_summary_status("fake-analysis-abc", sid) == "SUMMARIZE_COMPLETED"
    summaries = await fc.get_scene_summaries("fake-analysis-abc")
    assert {s["scene"] for s in summaries} == {1, 2}
    assert all(s["description"] for s in summaries)
