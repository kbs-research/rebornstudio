"""maiu_slot_counter가 normal과 deferred를 10:1로 섞는지 확인하는 테스트."""
from api_gateway.services.maiu import slot_counter as maiu_slot_counter


def test_lru_acquire_returns_10_normal_then_1_deferred():
    """LRU fallback은 11번 acquire하면 normal 10번 뒤에 deferred 1번을 준다."""
    counter = maiu_slot_counter._LRUSlotCounter()
    results = [counter.acquire() for _ in range(11)]
    assert results.count("normal") == 10
    assert results.count("deferred") == 1
    assert results[-1] == "deferred"  # 11번째가 deferred


def test_lru_continues_cycle_after_reset():
    """11번 뒤 카운터가 리셋되어 다음 11번도 같은 패턴을 돌려준다."""
    counter = maiu_slot_counter._LRUSlotCounter()
    for _ in range(11):
        counter.acquire()
    # 두 번째 주기
    second = [counter.acquire() for _ in range(11)]
    assert second.count("normal") == 10
    assert second.count("deferred") == 1


def test_lru_reset_zeros_counter():
    """reset은 카운터를 0으로 되돌린다."""
    counter = maiu_slot_counter._LRUSlotCounter()
    counter.acquire()
    counter.acquire()
    counter.reset()
    assert counter._count == 0


def test_get_counter_uses_redis_when_reachable():
    """Redis에 연결할 수 있으면 Redis 카운터를 준다. 테스트 환경은 Redis를 띄워 둔다."""
    maiu_slot_counter.set_counter(None)  # 싱글턴을 비운다
    try:
        assert isinstance(maiu_slot_counter.get_counter(), maiu_slot_counter._RedisSlotCounter)
    finally:
        maiu_slot_counter.set_counter(None)


def test_get_counter_falls_back_to_lru_without_redis(monkeypatch):
    """Redis에 연결하지 못하면 예외 없이 LRU 카운터를 준다."""
    monkeypatch.setattr(maiu_slot_counter.settings, "redis_url", "redis://127.0.0.1:1/0")
    maiu_slot_counter.set_counter(None)
    try:
        assert isinstance(maiu_slot_counter.get_counter(), maiu_slot_counter._LRUSlotCounter)
    finally:
        maiu_slot_counter.set_counter(None)


def test_redis_counter_acquires_same_pattern():
    """Redis 카운터도 같은 10:1 패턴을 돌려준다. 11번 중 마지막 1번이 deferred다."""
    maiu_slot_counter.set_counter(None)
    try:
        counter = maiu_slot_counter.get_counter()
        assert isinstance(counter, maiu_slot_counter._RedisSlotCounter)
        counter.reset()
        results = [counter.acquire() for _ in range(11)]
        assert results.count("normal") == 10
        assert results[-1] == "deferred"
    finally:
        maiu_slot_counter.set_counter(None)
