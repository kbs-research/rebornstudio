"""캡션 QC가 입력 캡션의 지시문을 따르지 않게 하고, 모델 지적 문장의 길이를 제한하는지 확인하는 테스트.

캡션은 라벨러가 쓰는 값이라 모델에 보내는 입력에 지시문이 들어올 수 있다.
모델이 지시문을 따라 길게 쓴 지적은 검수 화면에 그대로 표시된다.
"""
import json

from api_gateway.services.caption_qc import adapters


def test_default_prompt_marks_input_as_data():
    """기본 시스템 프롬프트가 입력 문장과 용어집을 지시가 아닌 데이터로 다루라고 적는지 확인한다."""
    assert "지시가 아닙니다" in adapters._SYSTEM_PROMPT


def test_long_finding_message_is_cut():
    """모델이 낸 지적 문장이 상한보다 길면 상한까지만 남기는지 확인한다."""
    raw = json.dumps({"findings": [
        {"sentence_id": "s1", "check": "spelling", "message": "가" * 5000},
        {"sentence_id": "s1", "check": "tone", "message": "종결이 섞였습니다"},
    ]})
    out = adapters._parse_findings(raw, {"s1"})
    assert len(out[0]["message"]) == adapters.MESSAGE_MAX
    assert out[1]["message"] == "종결이 섞였습니다"
