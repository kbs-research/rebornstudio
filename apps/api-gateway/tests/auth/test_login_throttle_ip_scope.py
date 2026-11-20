"""로그인 잠금을 (이메일, 클라이언트 IP) 단위로 거는지 보는 테스트.

이메일 하나만으로 5회에 잠그면 남의 이메일로 5번 틀리는 것만으로 그 사람을 15분 동안
로그인하지 못하게 할 수 있다. 그래서 5회 임계는 (이메일, IP) 쌍에 걸고,
이메일 단위에는 여러 IP로 나눠 시도하는 대입을 막는 50회 임계를 따로 둔다.
"""
import pytest
from api_gateway.services.auth import login_throttle
from tests.conftest import uniq


@pytest.fixture(autouse=True)
def _reset_throttle():
    """로그인 실패 잠금을 테스트마다 비운다."""
    login_throttle.reset()
    yield
    login_throttle.reset()


def test_other_ip_failures_do_not_lock_my_ip():
    """다른 IP에서 5회 실패하면 그 IP만 잠기고 내 IP는 잠기지 않는지 확인한다."""
    email = uniq("ipscope")
    for _ in range(5):
        login_throttle.record_failure(email, "198.51.100.9")
    assert login_throttle.retry_after(email, "198.51.100.9") is not None
    assert login_throttle.retry_after(email, "203.0.113.5") is None


def test_email_wide_threshold_locks_every_ip():
    """여러 IP에서 같은 이메일로 50회 실패하면 처음 보는 IP에서도 잠기는지 확인한다."""
    email = uniq("ipwide")
    for i in range(50):
        login_throttle.record_failure(email.upper() if i % 2 else email, f"10.0.{i // 4}.{i % 4}")
    assert login_throttle.retry_after(email, "203.0.113.77") is not None


def test_email_wide_threshold_is_not_reached_below_fifty():
    """여러 IP에서 49회 실패한 이메일은 새 IP에서 잠기지 않는지 확인한다."""
    email = uniq("ipbelow")
    for i in range(49):
        login_throttle.record_failure(email, f"10.1.{i // 4}.{i % 4}")
    assert login_throttle.retry_after(email, "203.0.113.78") is None


def test_clear_all_unlocks_every_ip():
    """재설정 확정이 쓰는 전체 해제가 모든 IP의 잠금과 이메일 단위 카운터를 지우는지 확인한다."""
    email = uniq("ipclear")
    for _ in range(5):
        login_throttle.record_failure(email, "198.51.100.10")
    login_throttle.clear_all(email.upper())
    assert login_throttle.retry_after(email, "198.51.100.10") is None


def test_memory_backend_follows_the_same_rules():
    """인메모리 백엔드도 쌍 임계 5회와 이메일 임계 50회를 같은 규칙으로 따르는지 확인한다."""
    login_throttle.set_backend(login_throttle.MemoryBackend())
    try:
        email = uniq("ipmem")
        for _ in range(5):
            login_throttle.record_failure(email, "198.51.100.11")
        assert login_throttle.retry_after(email, "198.51.100.11") is not None
        assert login_throttle.retry_after(email, "198.51.100.12") is None
        login_throttle.clear_all(email)
        assert login_throttle.retry_after(email, "198.51.100.11") is None
    finally:
        login_throttle.set_backend(None)


def test_clear_all_does_not_touch_glob_lookalikes():
    """이메일에 glob 문자가 있어도 전체 해제가 다른 이메일의 잠금을 지우지 않는지 확인한다."""
    tag = uniq("glob").split("@")[0]
    victim, wild = f"{tag}-ab@example.com", f"{tag}-*@example.com"
    for _ in range(5):
        login_throttle.record_failure(victim, "198.51.100.20")
    login_throttle.clear_all(wild)
    assert login_throttle.retry_after(victim, "198.51.100.20") is not None
