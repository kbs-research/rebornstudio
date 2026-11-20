"""저장된 품질검수 기록을 화면용 DTO로 바꿀 때 지금 문구가 나오는지 DB 없이 보는 테스트.

실행 기록은 실행 당시의 문장을 저장한다. 읽는 쪽이 옮기지 않으면 지난 실행이 이전 용어로 보인다.
"""
from api_gateway.services.caption_qc import (
    check_metadata,
    qc_finding_dto,
    qc_stages_dto,
    settings,
)
from api_gateway.services.deident_gap import DEIDENT_OFF


def test_stage_reason_uses_current_text_by_cause() -> None:
    """원인 코드가 있으면 저장된 문장과 관계없이 지금 문장을 쓰는지 확인한다."""
    stages = qc_stages_dto([
        {"key": "spelling", "status": "skipped", "reason": "규칙에 걸려 모델을 부르지 않았습니다",
         "cause": "rules_flagged"},
        {"key": "deident", "status": "skipped", "reason": "이 프로젝트는 비식별화를 쓰지 않습니다",
         "cause": "deident_off"},
        {"key": "profanity", "status": "skipped", "reason": "비속어 사전이 비어 있습니다",
         "cause": "profanity_empty"},
    ])
    assert stages is not None
    assert [s.reason for s in stages] == [settings.RULES_FLAGGED, DEIDENT_OFF, settings.PROFANITY_EMPTY]


def test_stage_reason_maps_legacy_text_without_cause() -> None:
    """원인 코드가 없던 기록은 뜻이 하나인 이전 문장만 옮기고 나머지는 그대로 두는지 확인한다."""
    stages = qc_stages_dto([
        {"key": "tone", "status": "skipped", "reason": "규칙에 걸려 모델을 부르지 않았습니다"},
        {"key": "tone", "status": "skipped", "reason": "모델을 부르지 못했습니다"},
        {"key": "deident", "status": "skipped", "reason": "비식별화가 아직 돌지 않았습니다"},
        {"key": "deident", "status": "skipped", "reason": "비식별화가 아직 실행되지 않았습니다",
         "cause": "deident_not_run"},
        {"key": "caption", "status": "passed"},
    ])
    assert stages is not None
    assert [s.reason for s in stages] == [
        settings.RULES_FLAGGED,
        settings.MODEL_FAILED,
        "비식별화가 아직 돌지 않았습니다",
        "비식별화가 아직 실행되지 않았습니다",
        None,
    ]


def test_stages_none_stays_none() -> None:
    """단계 기록이 없던 실행이 빈 목록이 아니라 None(정보 없음)으로 남는지 확인한다."""
    assert qc_stages_dto(None) is None


def test_finding_message_renames_legacy_terms() -> None:
    """이전 지적 문장의 `부가 라벨`·`영상 메타데이터`를 라벨링 화면 이름으로 옮기는지 확인한다."""
    labels = qc_finding_dto({"check": "labels_missing", "severity": "info",
                             "message": "부가 라벨 3개가 비어 있습니다(인물 · 장소 · 동작)"})
    meta = qc_finding_dto({"check": "metadata_missing", "severity": "info",
                           "message": "영상 메타데이터에 방영일이 없습니다"})
    assert labels.message == "세부 묘사 중 3개 항목이 비어 있습니다(인물 · 장소 · 동작)"
    assert meta.message == "영상 메타에 방영일이 없습니다"


def test_finding_message_matches_current_rules() -> None:
    """옮긴 메타 문장이 지금 규칙이 내는 문장과 같은 모양인지 확인한다. 둘이 다르면 같은 사실이 두 문장으로 표시된다."""
    now_meta = check_metadata({"broadcaster": "샘플 방송", "program_name": "9시 뉴스 샘플", "genre": "보도"})[0]["message"]
    old_meta = now_meta.replace("영상 메타에 ", "영상 메타데이터에 ", 1)
    assert qc_finding_dto({"check": "metadata_missing", "message": old_meta}).message == now_meta


def test_current_message_passes_through() -> None:
    """지금 문장은 바꾸지 않는지 확인한다."""
    f = qc_finding_dto({"check": "caption_empty", "severity": "error", "message": "캡션이 비어 있습니다"})
    assert f.message == "캡션이 비어 있습니다"
    assert f.check_label == "캡션"
