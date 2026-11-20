"""CSV로 내보내는 문자열 칸을 스프레드시트가 수식으로 실행하지 않게 바꾼다."""

#: 스프레드시트가 칸의 첫 글자로 보고 수식으로 읽는 글자.
FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> object:
    """문자열 칸이 수식 시작 글자로 시작하면 앞에 작은따옴표를 붙여 돌려준다.

    이름처럼 사용자가 정한 값이 `=HYPERLINK(...)`로 시작하면 내려받은 사람이 파일을 열 때 실행된다.
    문자열이 아닌 값(수·`None`)은 그대로 둔다. 음수가 문자로 바뀌면 합계를 다시 낼 수 없다.
    """
    if isinstance(value, str) and value.startswith(FORMULA_LEADS):
        return "'" + value
    return value
