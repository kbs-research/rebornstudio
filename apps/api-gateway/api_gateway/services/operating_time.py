"""운영 시간대. 날짜와 달의 경계를 정하는 단일 기준이다.

시각은 모두 timezone-aware UTC로 저장한다. 이 모듈은 날짜를 자르는 기준을 정한다.
예를 들어 한 제출이 어느 날짜, 어느 달에 속하는지를 정한다. 이 기준이 두 곳에 있으면 같은
제출이 화면마다 다른 날로 잡힌다. 집계만 UTC로 자르면 한국 시각 00:00~08:59의 제출이
전날 집계에 들어간다.

앱 코드에서 `Asia/Seoul` 문자열은 이 모듈과 celery beat 설정(스케줄러 자체의 시계) 두 곳에만 둔다.

이 기준을 쓰는 곳은 다음과 같다.

- `maiu.service`: 월별 분석 사용량.
- `export_service.export_filename`: 납품 zip 파일 이름의 시각.

시간대는 설정값이 아닌 상수다. 프로젝트마다 시간대를 다르게 두면 같은 플랫폼의 두 집계가
다른 날짜 경계를 써서 같은 제출이 서로 다른 날로 잡힌다.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func

#: 운영 시간대의 IANA 이름. 계약에 실어 화면이 그대로 읽는다.
#: 화면이 `(KST)`를 직접 적으면 서버가 시간대를 바꿀 때 화면만 이전 값을 표시한다.
OPERATING_TZ_NAME = "Asia/Seoul"

#: 파이썬 쪽 시간대. SQL 쪽은 아래 `month_key_expr`가 같은 이름을 넘긴다.
OPERATING_TZ = ZoneInfo(OPERATING_TZ_NAME)


def today() -> date:
    """운영 시간대의 오늘 날짜를 돌려준다. 날짜 키를 만들거나 기간을 판정할 때 기준일로 쓴다."""
    return datetime.now(OPERATING_TZ).date()


def local_date(at: datetime) -> date:
    """UTC로 저장된 시각을 운영 시간대의 날짜로 바꾼다.

    aware datetime만 받는다. 시간대 정보가 없는 값을 변환하면 9시간이 어긋나도 드러나지 않는다.
    저장 경로가 모두 aware라 naive 값이 들어오면 호출 쪽의 결함이다.

    Args:
        at: timezone-aware datetime.

    Raises:
        ValueError: naive datetime인 경우.
    """
    if at.tzinfo is None:
        raise ValueError("naive datetime은 운영 날짜로 옮길 수 없다 — 저장은 aware UTC다")
    return at.astimezone(OPERATING_TZ).date()


def today_key() -> str:
    """운영 시간대 오늘의 `YYYY-MM-DD` 문자열을 돌려준다. 스냅숏 키 형식의 기준이다."""
    return today().isoformat()


def date_key(at: datetime) -> str:
    """aware datetime을 운영 시간대 날짜의 `YYYY-MM-DD` 문자열로 바꾼다."""
    return local_date(at).isoformat()


def month_key_expr(column):
    """열의 운영 시간대 기준 `YYYY-MM` 문자열을 구하는 SQL 식을 만든다.

    `WHERE ... == month` 비교에 쓴다. 식은 `to_char(timezone('Asia/Seoul', col), 'YYYY-MM')`이다.

    주의: `GROUP BY` 키로 쓰지 않는다. 포맷 문자열이 바인드 파라미터로 나가서 Postgres가
    GROUP BY의 식을 SELECT의 식과 다른 식으로 판단한다. 그러면
    `must appear in the GROUP BY clause` 오류가 난다.
    """
    return func.to_char(func.timezone(OPERATING_TZ_NAME, column), "YYYY-MM")
