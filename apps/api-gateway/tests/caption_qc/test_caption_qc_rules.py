"""캡션 품질검수 규칙의 경계값 테스트. DB를 쓰지 않는다.

규칙 검사는 경계값이 핵심이라 매개변수 표로 테스트한다.
"""
import pytest
from api_gateway.services.caption_qc import (
    check_caption,
    check_clip_length,
    check_deident,
    check_pii,
    check_profanity,
    from_delivery_blockers,
    qc_status_of,
    rules,
    verdict_of,
)

# 사용자에게 보이는 이름은 기준 사전(`_PII_KO`)으로 확인한다.
# 메시지에서 영어 키(`resident_id` 등)를 찾으면 그 키가 화면에 나가도 테스트가 통과한다.
from api_gateway.services.caption_qc.rules import _PII_KO


def _s(text: str, sid: str = "s1", deleted: bool = False) -> dict:
    """캡션 문장 하나를 만든다."""
    return {"id": sid, "text": text, "deleted": deleted}


# 캡션 문장 규칙


def test_empty_caption_is_error():
    """캡션이 비어 있으면 가장 강한 신호(error)를 내는지 확인한다. 캡션은 유일한 제출 게이트다."""
    assert check_caption([])[0]["check"] == "caption_empty"
    assert check_caption([_s("   ")])[0]["severity"] == "error"


def test_deleted_sentences_are_ignored():
    """삭제 표시한 문장을 검사하지 않는지 확인한다. 삭제 표시한 문장은 최종 캡션에 없다.

    화면(`liveSentences`)과 같은 규칙이어야 두 곳의 결과가 달라지지 않는다.
    """
    findings = check_caption([_s("앵커가 뉴스를 전합니다.", "s1"), _s("짧다", "s2", deleted=True)])
    assert findings == []


def test_short_sentence_and_missing_ending_are_warnings():
    """짧은 문장이 `warn`이고 문장 지적에 문장 id가 붙는지 확인한다."""
    out = check_caption([_s("네.", "s1")])
    checks = {f["check"] for f in out}
    assert "sentence_too_short" in checks
    assert all(f["severity"] == "warn" for f in out)
    assert all(f["sentence_id"] == "s1" for f in out)  # 문장 지적에는 id가 붙는다


@pytest.mark.parametrize("text", [
    "앵커가 스튜디오에서 뉴스를 전합니다",   # 종결어미는 있고 부호는 없다
    "화면에 자막이 표시되어 있음",
    "기자가 현장을 취재했다",
    "자막이 표시됩니다.",
])
def test_valid_endings_pass(text):
    """부호가 없어도 한국어 종결어미면 통과하는지 확인한다. 캡션에 마침표가 없는 경우가 흔하다."""
    assert [f for f in check_caption([_s(text)]) if f["check"] == "sentence_ending"] == []


def test_unterminated_sentence_is_flagged():
    """종결어미도 부호도 없는 문장을 `sentence_ending`으로 지적하는지 확인한다."""
    out = check_caption([_s("앵커가 스튜디오에서 뉴스를 전하는")])
    assert [f["check"] for f in out] == ["sentence_ending"]


#: MI 장면 요약이 캡션 초안에 넣은 모델의 안내 문장(컬러바 시험 영상).
_META_SENTENCES = [
    "제공된 정보가 부족하여 영상의 구체적인 내용을 파악하기 어려우나, "
    "입력된 메타데이터와 프레임을 바탕으로 확인 가능한 내용을 서술하겠습니다.",
    "제공된 정보에는 인물, 음성, 자막 데이터가 포함되어 있지 않습니다.",
    "입력된 프레임은 색상 막대로만 구성되어 있습니다.",
    "현재 정보로는 장면 요약을 작성하기 어렵습니다.",
]


def test_model_meta_sentences_are_info_with_the_sentence_number():
    """모델의 안내 문장을 문장 번호와 함께 참고(info)로 지적하고 판정은 통과 그대로인지 확인한다."""
    sents = [_s("앵커가 스튜디오에서 오늘의 주요 뉴스를 전합니다.", "s0")] + [
        _s(t, f"s{i}") for i, t in enumerate(_META_SENTENCES, start=1)]
    out = [f for f in check_caption(sents) if f["check"] == "meta_sentence"]
    assert [f["sentence_id"] for f in out] == ["s1", "s2", "s3", "s4"]
    assert all(f["severity"] == "info" for f in out)
    assert [f["message"].split("번째")[0] for f in out] == ["2", "3", "4", "5"], "문장 번호를 안 적었다"
    assert verdict_of(check_caption(sents)) == "pass", "참고 지적이 판정에 들어갔다"
    assert rules.label_of("meta_sentence") == "캡션"


@pytest.mark.parametrize("text", [
    "경찰은 제보자가 건넨 정보를 바탕으로 수사를 이어 가고 있습니다.",
    "기자가 입력된 자료를 확인하며 현장 상황을 전합니다.",
    "앵커는 사고 원인을 파악하기 위해 조사가 진행 중이라고 전했습니다.",
    "정부는 다음 달까지 대책을 작성할 계획이라고 밝혔습니다.",
    "화면 하단에 자막이 표시되어 있습니다.",
])
def test_ordinary_news_sentences_are_not_meta(text):
    """비슷한 낱말이 있는 보도 문장을 안내 문장으로 지적하지 않는지 확인한다. 문구 사전은 좁게 둔다."""
    assert [f for f in check_caption([_s(text)]) if f["check"] == "meta_sentence"] == []


# 비속어(사전)


def test_profanity_uses_given_vocabulary_only():
    """비속어 검사가 코드에 고정한 사전 없이 주어진 사전만 쓰는지 확인한다.

    프로젝트마다 기준이 다르고, 사전을 코드에 두면 낱말을 더할 때마다 배포가 필요해진다.
    """
    sentences = [_s("그 사람이 비속어1 라고 말했습니다.")]
    assert check_profanity(sentences, []) == []           # 사전이 비면 검사하지 않는다
    out = check_profanity(sentences, ["비속어1"])
    assert out[0]["check"] == "profanity"
    # 보도 캡션은 발언을 인용할 수 있다. 자동 반려가 아니라 사람이 판단한다.
    assert out[0]["severity"] == "warn"


# 개인정보(정규식)


@pytest.mark.parametrize("text,kind", [
    ("주민번호 900101-1234567 이 화면에 보입니다.", "resident_id"),
    ("연락처 010-1234-5678 이 자막에 있습니다.", "phone"),
    ("메일 hong@example.com 이 노출됩니다.", "email"),
    ("카드 1234-5678-9012-3456 이 보입니다.", "card"),
])
def test_pii_formats_are_errors(text, kind):
    """형식이 정해진 개인정보가 `error`이고 메시지에 화면 이름이 들어가는지 확인한다."""
    out = check_pii([_s(text)])
    assert out and out[0]["check"] == "pii" and _PII_KO[kind] in out[0]["message"]
    assert out[0]["severity"] == "error"


@pytest.mark.parametrize("text,kind", [
    ("주민번호 900101-1234567입니다.", "resident_id"),
    ("제보는 010-1234-5678로 연락 바랍니다.", "phone"),
    ("문의는 news@example.co.kr로 보내세요.", "email"),
    ("카드 1234-5678-9012-3456으로 결제했습니다.", "card"),
])
def test_pii_catches_values_with_korean_particles_attached(text, kind):
    """조사가 값에 바로 붙은 표기도 잡는지 확인한다.

    파이썬 `re`의 `\\w`는 한글도 단어 문자로 본다.
    그래서 `5678로`처럼 조사가 붙으면 `\\b`가 경계를 만들지 못한다.
    위 `test_pii_formats_are_errors`는 값 뒤에 띄어쓰기를 두므로 이 경우를 잡지 못한다.
    """
    out = check_pii([_s(text)])
    assert out and _PII_KO[kind] in out[0]["message"], f"{kind}: {text}"


def test_pii_email_stops_before_the_particle():
    """이메일 값에 뒤의 조사까지 붙여 잡지 않는지 확인한다. `[\\w.-]`는 한글 조사까지 포함한다."""
    out = check_pii([_s("문의는 news@example.co.kr로 보내세요.")])
    assert out and "news@example.co.kr로" not in str(out)


def test_pii_does_not_flag_ordinary_numbers():
    """형식이 정해진 값만 잡고 일반 숫자는 지적하지 않는지 확인한다. 이름·주소는 오탐이 많아 모델이 맡는다."""
    assert check_pii([_s("통행료는 1만 3200원이고 2019년 사건입니다.")]) == []


# 클립 길이


def test_clip_length_flags_out_of_range_as_warning():
    """범위를 벗어난 클립 길이를 `warn`으로 지적하고 범위 안은 지적하지 않는지 확인한다."""
    assert check_clip_length(3.0, 10, 40)[0]["check"] == "clip_too_short"
    assert check_clip_length(90.0, 10, 40)[0]["check"] == "clip_too_long"
    # 검수자가 확인할 대상으로 `warn`을 낸다. 반려는 검수자의 판정으로만 생긴다.
    assert check_clip_length(90.0, 10, 40)[0]["severity"] == "warn"
    assert check_clip_length(25.0, 10, 40) == []


def test_clip_length_without_limits_is_not_checked():
    """기준을 정하지 않은 프로젝트(분석 서비스 기본값)에 임의 기준을 적용하지 않는지 확인한다."""
    assert check_clip_length(3.0, None, None) == []
    assert check_clip_length(None, 10, 40) == []


# 납품 관문 재사용


def test_delivery_blockers_become_errors():
    """필수값·스키마 검사를 다시 구현하지 않고 납품 관문의 결과를 error로 옮기는지 확인한다.

    두 곳에 구현하면 결과가 서로 달라진다.
    """
    out = from_delivery_blockers(["caption 필수", "aired_at 없음"])
    assert [f["check"] for f in out] == ["delivery_blocker"] * 2
    assert all(f["severity"] == "error" for f in out)


# 판정 도출(한 곳에서만 한다)


def test_verdict_and_gate_derivation():
    """지적의 심각도에서 판정과 게이트 상태를 도출하는지 확인한다."""
    warn = {"check": "x", "severity": "warn", "message": ""}
    err = {"check": "y", "severity": "error", "message": ""}

    assert verdict_of([]) == "pass"
    assert verdict_of([warn]) == "needs_review"
    assert verdict_of([warn, err]) == "reject_suggested"   # error 하나면 충분하다

    assert qc_status_of("pass") == "passed"
    # QC는 반려를 만들지 않는다. `held`는 사람이 다시 검토할 대상이라는 뜻이다.
    assert qc_status_of("needs_review") == "held"
    assert qc_status_of("reject_suggested") == "held"


# 비식별화 누락 의심


def test_deident_not_run_says_nothing():
    """비식별화가 돌지 않았으면 지적하지 않는지 확인한다.

    비식별화를 쓰지 않는 프로젝트의 모든 건이 보류되면 안 된다.
    결과가 없는 것과 결과가 나쁜 것은 다르다.
    """
    assert check_deident(None, reported=False) == []


def test_deident_clean_report_passes():
    """마스킹이 되고 확인이 필요 없는 비식별화 보고를 지적하지 않는지 확인한다."""
    ok = {"masked": True, "review_needed": False, "detection_rate": 0.55}
    assert check_deident(ok, reported=False) == []


def test_human_report_is_the_strongest_signal():
    """사람이 직접 올린 신고를 기계 판단보다 강한 신호인 error로 내는지 확인한다."""
    out = check_deident(None, reported=True)
    assert out[0]["check"] == "deident_reported" and out[0]["severity"] == "error"


def test_review_needed_is_surfaced_with_the_rate():
    """확인이 필요한 비식별화 보고를 검출률과 함께 지적하는지 확인한다."""
    out = check_deident({"review_needed": True, "detection_rate": 0.0}, reported=False)
    assert out[0]["check"] == "deident_review"
    assert "0.0%" in out[0]["message"]      # 분모가 되는 수를 함께 적는다


def test_no_face_masked_is_not_read_as_clean():
    """얼굴 0건을 문제없음으로 판정하지 않는지 확인한다. 얼굴이 실제로 없거나 모델이 찾지 못한 경우다.

    조용히 통과시키면 비식별화가 실패한 영상이 납품된다.
    """
    out = check_deident({"masked": False}, reported=False)
    assert [f["check"] for f in out] == ["deident_unmasked"]
    assert out[0]["severity"] == "warn"     # 검수자가 확인할 대상이다


# 단계 이름: 화면이 걸린 단계를 표시할 수 있는지 확인한다


def test_every_check_the_rules_emit_has_a_stage_name():
    """규칙이 내는 모든 검사 이름에 단계 이름이 있는지 확인한다.

    검사를 더하면서 사전에 넣지 않으면 그 지적만 화면에 영어 키로 뜬다.
    사전이 `get`으로 폴백하므로 `tsc`도 다른 테스트도 이 누락을 잡지 못한다.
    이름 목록은 손으로 적지 않고 소스에서 뽑는다. 손으로 적으면 그 목록도 낡는다.
    """
    import re
    from pathlib import Path

    src = Path(rules.__file__).read_text()
    # `_finding("이름", "info"|"warn"|"error"` 형식을 모두 모은다. 줄바꿈이 끼어도 잡는다.
    emitted = set(re.findall(r'_finding\(\s*"([a-z_]+)"\s*,\s*"(?:info|warn|error)"', src))
    assert emitted, "검사 이름을 하나도 못 뽑았다 — 정규식이 소스와 어긋났다"
    missing = {c for c in emitted if rules.label_of(c) == c}
    assert not missing, f"단계 이름이 없는 검사: {sorted(missing)}"


def test_model_checks_are_labelled_as_model():
    """모델이 내는 검사 네 가지의 출처가 model인지 확인한다. 모델 검사는 규칙과 신뢰도가 다르다."""
    from api_gateway.services.caption_qc import adapters

    for c in adapters._ALLOWED_CHECKS:
        assert rules.source_of(c) == "model", c
        assert rules.label_of(c) != c, f"{c}에 단계 이름이 없다"


def test_rule_checks_are_labelled_as_rules():
    """규칙이 내는 검사의 출처가 `rules`인지 확인한다. 실행 단위(`adapter_key`)가 bedrock이어도 같다."""
    for c in ("pii", "profanity", "delivery_blocker", "clip_too_long"):
        assert rules.source_of(c) == "rules", c


def test_unknown_check_falls_back_to_the_raw_name():
    """모르는 검사가 원래 이름을 그대로 보여 주는지 확인한다. 비워 두면 그 지적만 단계 이름 없이 뜬다."""
    assert rules.label_of("아직_없는_검사") == "아직_없는_검사"


def test_every_stage_the_runner_records_has_a_name():
    """러너가 기록하는 단계 이름도 모두 사전에 있는지 확인한다. 단계는 지적 이름과 다른 분류다.

    `clip_too_short`·`clip_too_long`은 둘 다 `clip_length` 단계에서 나온다.
    `deident_*` 세 가지는 `deident` 한 단계다.
    지적 이름만 사전에 넣으면 체크리스트에 `clip_length`가 영문으로 뜨고,
    지적만 보는 위 `test_every_check_the_rules_emit_has_a_stage_name`은 이 누락을 잡지 못한다.
    이름 목록은 손으로 적지 않고 러너 소스에서 뽑는다.
    """
    import re
    from pathlib import Path

    from api_gateway.services.caption_qc import runner

    src = Path(runner.__file__).read_text()
    keys = set(re.findall(r'_(?:ran|skipped)\(\s*"([a-z_]+)"', src))
    keys |= set(runner._MODEL_STAGES)
    assert keys, "단계 이름을 하나도 못 뽑았다 — 정규식이 소스와 어긋났다"
    missing = {k for k in keys if rules.label_of(k) == k}
    assert not missing, f"단계 이름이 없는 키: {sorted(missing)}"


# 메타데이터 누락
#
# 메타데이터 검사는 빈 필드를 `info` 지적 한 줄로 합쳐 낸다.
# `info`는 판정에 들어가지 않아 제출과 납품을 막지 않는다. 제출 게이트는 캡션뿐이다.
# 메시지는 화면 용어로 쓰고 `broadcaster` 같은 내부 키를 싣지 않는다.

#: 영상 메타 네 필드. 라벨이 아니라 영상의 값이다.
#: 키는 영상 컬럼·납품 스키마와 같은 이름이다.
_FULL_META = {
    "broadcaster": "샘플 방송", "program_name": "9시 뉴스 샘플", "genre": "보도",
    "aired_at": "2026-09-01T00:00:00+09:00",
}


def test_metadata_all_filled_is_silent():
    """영상 메타를 모두 채우면 지적이 없는지 확인한다."""
    assert rules.check_metadata(_FULL_META) == []


def test_metadata_missing_is_one_info_that_points_at_the_video():
    """빈 영상 메타 필드를 한 줄의 info로 지적하고 문구가 고칠 곳(영상)을 가리키는지 확인한다.

    라벨러 제출본이 아니라 영상 값을 본다.
    지적은 한 줄이고 `info`라서 판정에 들어가지 않는다. 그래서 반려나 보류로 이어지지 않는다.
    """
    found = rules.check_metadata({"broadcaster": "샘플 방송", "genre": ""})
    assert len(found) == 1 and found[0]["severity"] == "info"
    msg = found[0]["message"]
    assert msg == "영상 메타에 프로그램명 · 장르 · 방영일이 없습니다"
    assert "방송사" not in msg, "채운 것은 안 적는다"
    for key in ("broadcaster", "program_name", "genre", "aired_at"):
        assert key not in msg


@pytest.mark.parametrize(("blank", "tail"), [
    ("broadcaster", "방송사가 없습니다"),
    ("program_name", "프로그램명이 없습니다"),
    ("genre", "장르가 없습니다"),
    ("aired_at", "방영일이 없습니다"),
])
def test_metadata_particle_follows_the_last_name(blank, tail):
    """조사가 마지막 이름의 받침을 따르는지(`장르가`, `방영일이`) 확인한다."""
    assert rules.check_metadata({**_FULL_META, blank: None})[0]["message"].endswith(tail)


def test_labeler_payload_meta_is_no_longer_read():
    """라벨러 제출본의 이전 `meta` 모양을 넘기면 영상 키가 없으므로 모두 빈 것으로 보는지 확인한다."""
    legacy = {"meta": {"broadcaster": "샘플 방송", "program": "9시 뉴스 샘플", "genre": "보도"}}
    assert rules.check_metadata(legacy)[0]["message"] == (
        "영상 메타에 방송사 · 프로그램명 · 장르 · 방영일이 없습니다"
    )


def test_metadata_set_matches_what_the_operator_fills():
    """검사하는 네 필드가 운영자가 채울 수 있는 네 필드와 같은지 확인한다.

    둘이 다르면 고칠 수 없는 지적이 생긴다.
    기준은 영상이다. 라벨러 화면의 `REQUIRED_META`는 기본 라벨링 화면에 없다.
    이 테스트는 서버 기준(`video_meta.FIELDS`), 계약(`VideoMetaDTO`), 영상 수정 계약(`VideoUpdateRequest`), 화면(`lib/video_meta.ts`)을 이 검사와 비교한다.
    """
    import re
    from pathlib import Path

    from api_gateway.services import video_meta
    from reborn_contracts import VideoMetaDTO, VideoUpdateRequest

    checked = {k for k, _ko in rules._VIDEO_META}
    assert dict(rules._VIDEO_META) == dict(video_meta.FIELDS), "키나 화면 이름이 갈린다"
    assert checked == set(VideoMetaDTO.model_fields)
    assert checked <= set(VideoUpdateRequest.model_fields), "운영자가 채울 수 없는 칸을 검사한다"
    src = Path(__file__).resolve().parents[4] / "apps/web/src/lib/video_meta.ts"
    m = re.search(r"export const VIDEO_META_FIELDS = \[([^\]]*)\]", src.read_text())
    assert m, "VIDEO_META_FIELDS를 못 찾았다 — 이름이 바뀌었으면 이 시험도 같이 고친다"
    screen = {x.strip().strip('"') for x in m.group(1).split(",") if x.strip()}
    assert screen == checked


def test_metadata_check_has_a_name():
    """메타데이터 검사에 한국어 이름이 있는지 확인한다. 없으면 화면이 영문 키를 표시한다."""
    assert rules.label_of("metadata") != "metadata", "한국어 이름이 있어야 한다"


def test_old_runs_keep_stage_names():
    """실행하지 않는 세부 묘사 단계도 지난 실행의 이름은 남는지 확인한다."""
    assert rules.label_of("labels") == "세부 묘사"
    assert rules.label_of("speaker_unnamed") == "화자명"


def test_info_does_not_hold_delivery():
    """`info`만 있으면 통과인지 확인한다. 이 규칙이 깨지면 선택 항목이 사실상 필수가 된다.

    `warn`으로 내면 라벨을 채우지 않은 깨끗한 제출이 모두 보류함으로 간다.
    """
    only_info = rules.check_metadata({})
    assert only_info, "지적 자체는 나야 한다 — 안 나면 검수자가 못 본다"
    assert all(f["severity"] == "info" for f in only_info)
    assert rules.verdict_of(only_info) == "pass"
    assert rules.qc_status_of(rules.verdict_of(only_info)) == "passed", "납품을 막지 않는다"


def test_info_mixed_with_warn_still_needs_review():
    """지적이 섞이면 무거운 쪽을 따르는지 확인한다. `info`가 다른 지적을 덮지 않는다."""
    mixed = rules.check_metadata({}) + [
        rules._finding("sentence_ending", "warn", "문장이 종결되지 않았습니다", "s1")
    ]
    assert rules.verdict_of(mixed) == "needs_review"


def test_info_mixed_with_error_is_still_reject_suggested():
    """`info`와 `error`가 섞이면 반려추천인지 확인한다."""
    mixed = rules.check_metadata({}) + rules.check_caption([])
    assert rules.verdict_of(mixed) == "reject_suggested"
