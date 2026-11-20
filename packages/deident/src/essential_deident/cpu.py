"""잡 하나가 쓸 CPU 스레드 수를 정한다. 추론(ONNX Runtime)과 인코딩(ffmpeg)이 같은 값을 쓴다.

컨테이너 안의 `os.cpu_count()`는 CPU 상한(`cpus:`) 대신 호스트 코어 수를 돌려준다.
그 값으로 스레드를 만들면 동시에 도는 잡들이 상한보다 몇 배 많은 스레드를 만들어
문맥 전환으로 시간을 쓴다. 그래서 cgroup의 CPU 상한을 먼저 읽는다.
"""

from __future__ import annotations

import os
from pathlib import Path

#: cgroup v2의 CPU 상한 파일. 값은 `<할당 마이크로초> <주기 마이크로초>` 또는 `max <주기>`다.
_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
#: cgroup v1의 CPU 상한 파일 두 개. 할당이 -1이면 상한이 없다.
_CGROUP_V1 = (Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
              Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))


def _cgroup_limit() -> float | None:
    """cgroup이 정한 CPU 상한(코어 수)을 읽는다. 상한이 없거나 읽지 못하면 None이다."""
    try:
        quota, period = _CGROUP_V2.read_text().split()[:2]
        if quota != "max":
            return int(quota) / int(period)
        return None
    except (OSError, ValueError):
        pass
    try:
        quota_us = int(_CGROUP_V1[0].read_text())
        period_us = int(_CGROUP_V1[1].read_text())
    except (OSError, ValueError):
        return None
    return quota_us / period_us if quota_us > 0 and period_us > 0 else None


def available_cpus() -> int:
    """이 프로세스가 쓸 수 있는 CPU 수를 돌려준다. cgroup 상한, CPU 친화도, 코어 수 순으로 본다."""
    limit = _cgroup_limit()
    if limit is not None:
        return max(1, int(limit))
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def job_threads() -> int:
    """잡 하나가 쓸 스레드 수를 돌려준다. 최소 1이다.

    `DEIDENT_THREADS`가 있으면 그 값을 쓴다.
    없으면 쓸 수 있는 CPU 수를 워커 동시 실행 수(`GPU_WORKER_CONCURRENCY`, 기본 2)로 나눈다.
    동시에 도는 잡들이 CPU 상한을 나눠 쓰게 하려는 값이다.
    """
    raw = (os.environ.get("DEIDENT_THREADS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        concurrency = max(1, int(os.environ.get("GPU_WORKER_CONCURRENCY") or 2))
    except ValueError:
        concurrency = 2
    return max(1, available_cpus() // concurrency)
