"""날짜 집계를 한국 시각 기준으로 자르는지 보는 테스트.

저장은 그대로 aware UTC이고, 날짜를 자르는 기준만 한국 시각이다.
그래서 이 파일의 모든 시각은 `UTC 15:00`(KST 다음 날 00:00) 근처에 둔다.
두 기준이 서로 다른 날짜를 내는 구간은 그곳뿐이다.
그 구간 밖에서 재면 UTC로 잘라도 통과하므로 이 테스트가 결함을 잡지 못한다.

보장하는 것은 두 가지다.

1. 시간대 기준은 한 모듈(`services/operating_time`)에만 둔다.
2. 날짜는 한국 날짜다. UTC로는 같은 날인 두 시각이 한국 날짜로는 다른 날로 나뉜다.
"""
from datetime import UTC, datetime

from api_gateway.services import operating_time

#: 한국 시각 9월 1일 00:30. UTC로는 8월 31일 15:30이다.
#: 한국 날짜 기준과 UTC 날짜 기준의 차이가 이 시각에서 드러난다.
_KST_SEP_1_EARLY = datetime(2026, 8, 31, 15, 30, tzinfo=UTC)
#: 한국 시각 8월 31일 23:30. UTC로도 8월 31일이므로 위 시각과 UTC 날짜가 같다.
_KST_AUG_31_LATE = datetime(2026, 8, 31, 14, 30, tzinfo=UTC)


# ── 시간대 기준 ──────────────────────────────────────────────────────────────


def test_local_date_refuses_a_naive_datetime():
    """시간대 정보가 없는 datetime을 거부하는지 확인한다. 변환하면 9시간이 틀려도 드러나지 않는다."""
    try:
        operating_time.local_date(datetime(2026, 9, 1, 0, 30))
    except ValueError:
        return
    raise AssertionError("naive datetime을 받아들였다")


def test_date_key_moves_the_late_utc_hours_to_the_next_day():
    """UTC 15시 이후가 한국 날짜로 다음 날인지 확인한다. 두 기준이 9시간 차이 나는 경우다."""
    assert operating_time.date_key(_KST_SEP_1_EARLY) == "2026-09-01"
    assert operating_time.date_key(_KST_AUG_31_LATE) == "2026-08-31"
    assert _KST_SEP_1_EARLY.strftime("%Y-%m-%d") == "2026-08-31", (
        "UTC로 자르면 같은 시각이 8월이 된다 — 그래서 이 테스트가 눈금을 잰다"
    )


def test_the_timezone_lives_in_exactly_one_place():
    """서비스 코드에 `Asia/Seoul` 리터럴이 새로 생기면 실패하는지 확인한다.

    시간대를 `grep`으로 찾아 고치는 대신 `operating_time`을 `import`해 쓰게 하는 조건이다.
    시간대가 여러 곳에 흩어지면 한 곳만 UTC로 잘라도 드러나지 않는다.
    celery beat 설정은 스케줄러 자체의 시계라서 이 범위 밖이다.
    그 설정은 `workers/worker-io/worker_io/celery_app.py`에 있다.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "api_gateway"
    hits = [
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if "Asia/Seoul" in f.read_text(encoding="utf-8")
    ]
    assert hits == ["services/operating_time.py"], f"눈금이 또 생겼다: {hits}"
