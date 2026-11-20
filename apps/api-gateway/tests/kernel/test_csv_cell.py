"""CSV 내보내기의 문자열 칸이 스프레드시트 수식으로 실행되지 않는지 확인하는 테스트.

행위자 이름은 본인이 `PATCH /users/me`로 바꾸는 값이다. `=`로 시작하는 이름이 감사 CSV에 그대로
실리면 파일을 연 관리자의 스프레드시트가 수식으로 실행한다.
"""

from api_gateway.services.csv_cell import safe_cell


def test_formula_leads_get_a_quote():
    """수식 시작 글자로 시작하는 문자열에만 작은따옴표를 붙이는지 확인한다."""
    for lead in ("=", "+", "-", "@", "\t", "\r"):
        assert safe_cell(f"{lead}1+1") == f"'{lead}1+1"
    assert safe_cell("김라벨") == "김라벨"
    assert safe_cell("a=b") == "a=b"
    assert safe_cell("") == ""
    assert safe_cell(-3) == -3, "수는 그대로 둔다"
    assert safe_cell(None) is None

