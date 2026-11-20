"""워크스페이스 패키지와 api 앱의 버전 표기가 모두 같은지 확인하는 검사.

릴리스 버전은 pyproject.toml 7개, package.json 3개, FastAPI 앱 버전, uv.lock, openapi.json에
따로 적혀 있다. 한 곳만 올리면 `/api/healthz`의 `version`과 릴리스 태그가 어긋나고,
운영 서버가 어느 릴리스인지 응답으로 확인할 수 없게 된다.

기준은 루트 `pyproject.toml`의 `version`이다. 표기가 다른 파일이 있으면 그 파일과 값을 찍고
1을 돌려준다.

    python3 scripts/ci/check-versions.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 버전을 적는 워크스페이스 pyproject.toml. 루트가 기준이다.
PYPROJECTS = (
    "pyproject.toml",
    "apps/api-gateway/pyproject.toml",
    "workers/worker-io/pyproject.toml",
    "workers/worker-jobs/pyproject.toml",
    "packages/reborn-contracts/python/pyproject.toml",
    "packages/reborn-transcode/pyproject.toml",
    "packages/deident/pyproject.toml",
)

#: 버전을 적는 package.json.
PACKAGE_JSONS = (
    "package.json",
    "apps/web/package.json",
    "packages/reborn-contracts/typescript/package.json",
)

#: FastAPI 앱 생성자의 버전 인자. `/api/healthz`가 이 값을 응답한다.
MAIN_PY = "apps/api-gateway/api_gateway/main.py"
MAIN_VERSION = re.compile(r'^\s*version="([^"]+)",\s*$', re.M)

#: 생성된 OpenAPI 문서. `make openapi`를 빠뜨리면 이 값만 남는다.
OPENAPI = "docs/api/openapi.json"


def _collect() -> list[tuple[str, str | None]]:
    """파일마다 적힌 버전을 읽어 (경로, 버전) 목록을 만든다.

    Returns:
        버전을 찾지 못한 파일은 값이 None이다.
    """
    found: list[tuple[str, str | None]] = []
    names: set[str] = set()
    for rel in PYPROJECTS:
        data = tomllib.loads((ROOT / rel).read_text(encoding="utf-8"))
        project = data.get("project", {})
        found.append((rel, project.get("version")))
        if "name" in project:
            names.add(project["name"])
    for rel in PACKAGE_JSONS:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        found.append((rel, data.get("version")))
    main_match = MAIN_VERSION.search((ROOT / MAIN_PY).read_text(encoding="utf-8"))
    found.append((MAIN_PY, main_match.group(1) if main_match else None))
    openapi = json.loads((ROOT / OPENAPI).read_text(encoding="utf-8"))
    found.append((OPENAPI, openapi.get("info", {}).get("version")))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for pkg in lock.get("package", []):
        if pkg.get("name") in names:
            found.append((f"uv.lock ({pkg['name']})", pkg.get("version")))
    return found


def main() -> int:
    """기준 버전과 다른 표기를 찾아 찍는다.

    Returns:
        모두 같으면 0, 하나라도 다르거나 비어 있으면 1.
    """
    found = _collect()
    expected = found[0][1]
    bad = [(rel, ver) for rel, ver in found if ver != expected]
    if expected is None or bad:
        print(f"버전 표기 불일치 (기준 pyproject.toml = {expected})")
        for rel, ver in bad:
            print(f"  {rel}: {ver}")
        return 1
    print(f"버전 표기 일치: {expected} ({len(found)}곳)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
