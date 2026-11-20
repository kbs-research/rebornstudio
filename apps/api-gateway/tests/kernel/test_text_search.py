"""검색어를 LIKE 패턴으로 바꾸는 규칙(`services/text_search`) 테스트."""
from api_gateway.services import text_search


def test_escape_wildcards_and_backslash():
    """와일드카드와 이스케이프 문자가 글자로 읽히게 바뀌고 백슬래시가 두 번 바뀌지 않는지 확인한다."""
    assert text_search.escape("50%") == "50\\%"
    assert text_search.escape("a_b") == "a\\_b"
    assert text_search.escape("c:\\x%") == "c:\\\\x\\%"


def test_contains_strips_nul_and_blank():
    """NUL과 앞뒤 공백을 지우고, 남은 글자가 없으면 조건을 걸지 않도록 `None`을 돌려주는지 확인한다."""
    assert text_search.contains(" 뉴스 ") == "%뉴스%"
    assert text_search.contains("\x00'") == "%'%"
    assert text_search.contains("\x00") is None
    assert text_search.contains("   ") is None
    assert text_search.contains(None) is None


def test_prefix_pattern():
    """앞부분 일치 패턴이 뒤에만 `%`를 붙이는지 확인한다."""
    assert text_search.prefix("ab_") == "ab\\_%"
    assert text_search.prefix("") is None
