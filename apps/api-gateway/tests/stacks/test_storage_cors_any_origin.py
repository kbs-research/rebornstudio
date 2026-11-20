"""저장소 점검이 버킷 CORS의 모든 출처 허용을 경고로 표시하는지 보는 테스트.

보장하는 것은 다음과 같다.
- 업로드 방법(PUT)을 모든 출처(`*`)에 허용하는 CORS 규칙은 정상이 아니라 경고다. 사유와 함께 허용 주소를
  서비스의 웹 주소로 좁히라는 안내를 싣는다. 다른 규칙이 화면 출처를 따로 적어도 경고다.
- 화면 출처를 적은 규칙은 정상이고, CORS 규칙이 없으면 업로드가 막힌다는 경고다.
- 업로드 방법이 없는 규칙은 판정에 쓰지 않는다.
"""
from api_gateway.services.stacks import project_storage

#: 브라우저 업로드가 쓰는 방법 목록.
_UPLOAD = ["PUT"]
#: 화면 출처.
_APP = "https://app.example"


def test_any_origin_rule_is_a_warning_without_origin():
    """주기 점검(출처 없음)이 모든 출처 규칙을 경고로 표시하고 좁히는 방법을 싣는지 확인한다."""
    r = project_storage._cors_result([{"AllowedOrigins": ["*"], "AllowedMethods": _UPLOAD}], None)
    assert r.check.status == "warn", r.check
    assert "모든 주소" in r.check.detail
    assert r.check.hint and "웹 주소" in r.check.hint


def test_any_origin_rule_is_a_warning_with_origin():
    """점검 버튼(화면 출처 있음)도 모든 출처 규칙을 경고로 표시하는지 확인한다. 다른 규칙이 출처를 적어도 같다."""
    rules = [{"AllowedOrigins": [_APP], "AllowedMethods": _UPLOAD},
             {"AllowedOrigins": ["*"], "AllowedMethods": ["PUT", "GET"]}]
    r = project_storage._cors_result(rules, _APP)
    assert r.check.status == "warn", r.check
    assert "모든 주소" in r.check.detail


def test_named_origin_rule_is_ok():
    """화면 출처를 적은 규칙은 정상이고 다른 출처는 경고인지 확인한다. 출처가 없으면 업로드 규칙이 있는지만 본다."""
    rules = [{"AllowedOrigins": [_APP], "AllowedMethods": _UPLOAD}]
    assert project_storage._cors_result(rules, f"{_APP}/").check.status == "ok"
    assert project_storage._cors_result(rules, None).check.status == "ok"
    other = project_storage._cors_result(rules, "https://other.example").check
    assert other.status == "warn" and "모든 주소" not in other.detail


def test_missing_rules_keep_the_blocked_upload_warning():
    """CORS 규칙이 없으면 업로드가 막힌다는 경고인지 확인한다."""
    for rules in (None, []):
        r = project_storage._cors_result(rules, _APP)
        assert r.check.status == "warn"
        assert r.check.detail == "CORS 규칙이 없어 브라우저 업로드가 막힙니다"


def test_any_origin_rule_without_upload_methods_is_not_counted():
    """업로드 방법이 없는 모든 출처 규칙은 판정에 쓰지 않는지 확인한다."""
    r = project_storage._cors_result([{"AllowedOrigins": ["*"], "AllowedMethods": ["GET"]}], None)
    assert r.check.status == "warn" and "모든 주소" not in r.check.detail
