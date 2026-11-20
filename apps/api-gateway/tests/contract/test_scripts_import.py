"""`scripts/`가 없는 모듈·심볼을 부르지 않는지 보는 테스트.

앱 코드는 `ruff`·`import-linter`·`pytest`가 검사하지만 스크립트에는 검사가 없다.
그래서 기능을 제거하면서 그 기능을 부르는 스크립트를 함께 고치지 않으면 스크립트가 깨진 채로 남는다.
그러면 `make seed-dev` 같은 로컬 준비가 실패한다.

이 파일은 import 시점에 없는 모듈·심볼을 잡는다.
스크립트는 모두 `if __name__ == "__main__"` 가드가 있고 top-level 실행이 없어서 import만으로 안전하게 검사할 수 있다.
실행 중에만 드러나는 값 변경(예: 이름이 바뀐 `copyright_policy` 값)은 import로 잡지 못한다.
그 경우는 아래 `test_scripts_use_current_copyright_policy`가 따로 검사한다.
허용값은 CHECK 제약이 기준이므로 모델에서 읽어 대조한다. 목록을 이 파일에 또 적으면 두 목록 중 한쪽이 낡는다.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"

#: 스크립트를 담는 하위 폴더. 폴더를 더하면 이 목록에도 더한다.
_SCRIPT_DIRS = ("ci", "dev", "ops")


def _script_paths() -> list[Path]:
    """`scripts/<폴더>/`의 파이썬 파일을 모은다. `_`로 시작하는 파일과 `__pycache__`는 뺀다."""
    # `test_` 접두사가 붙은 파일은 스크립트가 아니라 테스트다.
    # 그런 파일은 잘못된 위치에 있으므로 옮긴다. 이 함수는 그 파일을 import하지 않는다.
    return sorted(
        p for d in _SCRIPT_DIRS for p in (_SCRIPTS / d).glob("*.py")
        if not p.stem.startswith(("_", "test_"))
    )


def test_script_dirs_hold_every_script() -> None:
    """`scripts/` 바로 아래에 스크립트가 없고 하위 폴더마다 스크립트가 있는지 확인한다.

    폴더 목록과 실제 배치가 어긋나면 아래 매개변수가 빈 채로 수집된다.
    그러면 import 검사가 한 건도 돌지 않고 통과한다.
    """
    stray = sorted(p.name for p in _SCRIPTS.glob("*.py"))
    assert not stray, f"scripts/ 바로 아래 파이썬 파일은 ci·dev·ops 중 하나로 옮긴다 → {stray}"
    unknown = sorted(
        p.name for p in _SCRIPTS.iterdir()
        if p.is_dir() and p.name not in _SCRIPT_DIRS and not p.name.startswith((".", "_"))
    )
    assert not unknown, f"_SCRIPT_DIRS에 없는 폴더 → {unknown}"
    for d in _SCRIPT_DIRS:
        assert any((_SCRIPTS / d).glob("*.py")), f"scripts/{d}/에 파이썬 스크립트가 없다"


@pytest.mark.parametrize("path", _script_paths(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_script_imports(path: Path) -> None:
    """스크립트가 import되는지 확인한다. 없는 모듈·심볼을 부르면 이 테스트가 실패한다.

    DB·네트워크·NCP 자격 증명이 필요한 스크립트가 있어서 실행은 하지 않는다.
    이 테스트는 동작이 아니라 참조가 유효한지만 확인한다.
    같은 폴더의 도우미를 부르는 스크립트가 있어 그 폴더를 경로에 넣고 import한다.
    """
    folder = str(path.parent)
    sys.path.insert(0, folder)
    try:
        importlib.import_module(path.stem)
    finally:
        sys.path.remove(folder)


def test_scripts_use_current_copyright_policy() -> None:
    """스크립트의 `copyright_policy=` 리터럴이 모델이 허용하는 값인지 확인한다.

    허용값은 이 파일에 적지 않고 `Video`의 CHECK 제약에서 읽는다.
    목록을 두 곳에 두면 한쪽만 낡는다.
    """
    from api_gateway.models.video import Video

    check = next(
        c.sqltext.text for c in Video.__table__.constraints
        if getattr(c, "name", None) == "ck_video_copyright_policy"
    )
    allowed = set(re.findall(r"'([a-z_]+)'", check))
    assert allowed, "CHECK 제약에서 허용값을 못 읽었다 — 제약 이름이 바뀌었나"

    bad: list[str] = []
    for p in _script_paths():
        for value in re.findall(r'copyright_policy\s*=\s*"([a-z_]+)"', p.read_text(encoding="utf-8")):
            if value not in allowed:
                bad.append(f"{p.name}: {value!r}")
    assert not bad, f"허용값 {sorted(allowed)} 밖의 정책을 쓴다 → {bad}"
