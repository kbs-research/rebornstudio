"""프로젝트 처리 옵션을 고정 기본값으로 되돌린다. RebornStudio는 이 옵션들을 화면에서 고치지 않는다.

## 무엇이 바뀌나

컬럼은 남기고 값만 기본값으로 맞춘다. 컬럼을 남겨 RebornStudio Pro와 같은 스키마를 유지한다.

- 자동 반납 24시간, 캡션 최소 50토큰, 문장별 시각 표시 켬, 원본 보관 안 함, 비식별화 목표 90%
- `settings`: 클립 길이는 기본값(MEDIUM), 장면 요약 다듬기와 저장된 반려 사유 목록은 지워 기본값을 쓰게
  하고, 캡션 품질검수의 프롬프트·용어집·비속어 사전은 비운다. 읽는 곳이 없는 월간 개선사항 키도 지운다.

관리자가 고치는 값(이름·가이드·마감·목표 분량·상태·동시 보유 한도·비식별화와 품질검수의 켜기와 연결)은
그대로 둔다.

## 되돌리기

하지 않는다. 지운 값은 돌아오지 않는다.
"""
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

_SCENE_DEFAULT = '{"scene_range": "MEDIUM", "min_seconds": null, "max_seconds": null}'


def upgrade() -> None:
    """처리 옵션 컬럼과 `settings` 키를 고정 기본값으로 맞춘다."""
    op.execute(
        "UPDATE projects SET default_assignment_ttl_hours = 24, caption_min_tokens = 50, "
        "sentence_timecode_enabled = true, retain_original = false, target_deident_pct = 90"
    )
    op.execute(
        "UPDATE projects SET settings = "
        "(coalesce(settings, '{}'::jsonb) - 'transform_options' - 'reject_reasons' - '_monthly_notes') "
        f"|| jsonb_build_object('scene_options', '{_SCENE_DEFAULT}'::jsonb)"
    )
    op.execute(
        "UPDATE projects SET settings = jsonb_set(settings, '{caption_qc}', "
        "(settings->'caption_qc') - 'prompts' - 'glossary' - 'profanity') "
        "WHERE jsonb_typeof(settings->'caption_qc') = 'object'"
    )


def downgrade() -> None:
    """되돌리지 않는다. 지운 옵션 값은 복원할 수 없다."""
