"""검색어를 LIKE 패턴으로 바꾸는 규칙. 모든 도메인의 부분 일치 검색이 함께 쓴다.

사용자가 입력한 글자는 글자 그대로 찾아야 한다.
`%`·`_`를 그대로 넣으면 와일드카드가 되어 `q=%`가 모든 행과 일치한다.
그러면 "검색 결과 N건"이 전체 수가 되어 찾는 글자가 없다는 결과를 표시할 수 없다.
백슬래시도 이스케이프 문자라 함께 처리한다.

NUL(`\\x00`)은 PostgreSQL 텍스트에 들어갈 수 없다.
그대로 바인딩하면 드라이버가 `CharacterNotInRepertoireError`를 내고 요청이 500이 된다.
화면이 보내지 않는 글자이므로 지우고 나머지 글자로 찾는다.

쓰는 법:

    from api_gateway.services import text_search
    like = text_search.contains(q_text)
    if like:
        stmt = stmt.where(Video.title.ilike(like, escape=text_search.ESCAPE))

`escape=`를 빼면 이스케이프 문자가 패턴의 글자로 읽혀 아무것도 찾지 못한다.
"""
from __future__ import annotations

#: LIKE 이스케이프 문자. `ilike(..., escape=ESCAPE)`로 함께 넘긴다.
ESCAPE = "\\"


def clean(text: str | None) -> str:
    """검색어에서 NUL을 지우고 앞뒤 공백을 뗀 값을 돌려준다. 입력이 비었으면 빈 문자열이다."""
    return (text or "").replace("\x00", "").strip()


def escape(text: str) -> str:
    """LIKE 메타 문자(`\\`·`%`·`_`)를 글자로 읽히게 이스케이프한다.

    백슬래시를 먼저 바꾼다. 나중에 바꾸면 `%`를 이스케이프하며 넣은 백슬래시가 두 번 바뀐다.
    """
    return text.replace(ESCAPE, ESCAPE * 2).replace("%", ESCAPE + "%").replace("_", ESCAPE + "_")


def contains(text: str | None) -> str | None:
    """부분 일치 패턴(`%검색어%`)을 돌려준다. NUL과 공백을 지우고 남은 글자가 없으면 `None`이다.

    빈 패턴(`%%`)은 모든 행과 일치하므로 `None`이면 호출자가 조건을 걸지 않는다.
    """
    term = clean(text)
    return f"%{escape(term)}%" if term else None


def prefix(text: str | None) -> str | None:
    """앞자리 일치 패턴(`검색어%`)을 돌려준다. NUL과 공백을 지우고 남은 글자가 없으면 `None`이다."""
    term = clean(text)
    return f"{escape(term)}%" if term else None
