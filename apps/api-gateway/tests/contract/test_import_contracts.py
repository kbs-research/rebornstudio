"""import-linter 계약의 모듈 목록이 `services/`의 실제 파일과 맞는지 보는 테스트.

import-linter는 목록에 적힌 모듈만 검사한다.
새 도메인 패키지나 평면 모듈을 목록에 넣지 않으면 그 모듈은 어떤 계약도 적용받지 않고, `lint-imports`는 통과한다.
와일드카드(`services.*`)는 도메인 패키지와 평면 모듈을 구분하지 못해 커널 계약에 쓸 수 없다.
그래서 이 테스트가 목록을 파일 시스템과 대조한다. DB를 쓰지 않는다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

_APP = Path(__file__).resolve().parents[2]
_SERVICES = _APP / "api_gateway" / "services"
_PREFIX = "api_gateway.services."

_INDEPENDENCE = "도메인 독립 — 허용 목록 밖 교차 import 금지"
_KERNEL = "커널 평면 모듈 — 도메인 패키지 import 금지"

# 커널 계약에 넣지 않는 평면 모듈과 그 이유.
# 도메인 패키지를 import하거나 한 기능 영역만 맡는 모듈은 커널로 둘 수 없다.
# 지금은 비어 있다. 그런 모듈은 소유 패키지(`exports`·`ops`·`stacks` 등)에 둔다.
_NON_KERNEL_FLAT: dict[str, str] = {}


def _contracts() -> dict[str, dict]:
    """pyproject.toml의 import-linter 계약을 이름을 키로 하는 사전으로 돌려준다."""
    data = tomllib.loads((_APP / "pyproject.toml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in data["tool"]["importlinter"]["contracts"]}


def _short(modules: list[str]) -> set[str]:
    """`api_gateway.services.` 접두사를 뗀 모듈 이름 집합을 만든다."""
    assert all(m.startswith(_PREFIX) for m in modules), modules
    return {m.removeprefix(_PREFIX) for m in modules}


def _domain_packages() -> set[str]:
    """`services/` 아래 `__init__.py`가 있는 하위 패키지 이름을 모은다."""
    return {p.parent.name for p in _SERVICES.glob("*/__init__.py")}


def _flat_modules() -> set[str]:
    """`services/` 바로 아래 평면 모듈 이름을 모은다. `__init__`은 뺀다."""
    return {p.stem for p in _SERVICES.glob("*.py") if p.stem != "__init__"}


def test_every_domain_package_is_in_independence_contract():
    """도메인 패키지가 모두 도메인 독립 계약에 들어 있는지 확인한다."""
    listed = _short(_contracts()[_INDEPENDENCE]["modules"])
    actual = _domain_packages()
    assert listed == actual, (
        f"계약에 없는 패키지: {sorted(actual - listed)} · "
        f"없는 패키지를 적은 줄: {sorted(listed - actual)}"
    )


def test_kernel_contract_forbids_every_domain_package():
    """커널 계약이 도메인 패키지 전부를 금지 대상으로 두는지 확인한다."""
    forbidden = _short(_contracts()[_KERNEL]["forbidden_modules"])
    actual = _domain_packages()
    assert forbidden == actual, (
        f"금지 목록에 없는 패키지: {sorted(actual - forbidden)} · "
        f"없는 패키지를 적은 줄: {sorted(forbidden - actual)}"
    )


def test_every_flat_module_is_classified():
    """평면 모듈이 커널 계약과 비커널 목록 중 정확히 한 곳에 있는지 확인한다."""
    kernel = _short(_contracts()[_KERNEL]["source_modules"])
    non_kernel = set(_NON_KERNEL_FLAT)
    actual = _flat_modules()
    assert not kernel & non_kernel, f"두 곳에 다 있는 모듈: {sorted(kernel & non_kernel)}"
    unclassified = actual - kernel - non_kernel
    assert not unclassified, (
        f"분류하지 않은 평면 모듈: {sorted(unclassified)}. 도메인을 import하지 않으면 "
        "pyproject.toml 커널 계약의 source_modules에, 아니면 이 파일의 _NON_KERNEL_FLAT에 넣는다."
    )
    stale = (kernel | non_kernel) - actual
    assert not stale, f"없는 모듈을 적은 줄: {sorted(stale)}"

