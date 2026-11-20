"""환경변수 사전(`docs/env-catalog.md`)을 config·prod compose·개발 판·운영 판과 대조하는 게이트.

환경변수 이름은 여러 곳에 따로 적힌다. config.Settings 필드, 사전 `docs/env-catalog.md`,
prod compose의 `x-app-env`와 `${VAR:?}`, 개발 판 루트 `.env.example`, 운영 판
`infra/deploy/env.example`이다.
어느 한 곳만 고치면 오류 없이 어긋난다. pydantic은 정의되지 않은 키를 `extra="ignore"`로 버린다.
prod 프로파일은 `env_file` 대신 `environment:`만 쓰므로, `x-app-env`에 없는 키는
서버 `.env`에 넣어도 컨테이너로 가지 않는다. validator가 요구하는 시크릿이 빠지면
api와 워커 전체가 import 시점에 `ValidationError`로 비정상 종료된다. 배포는 헬스 확인 180초를
다 쓴 뒤 실패한다. web(nginx)은 정상이라 원인이 잘 보이지 않는다.
tsc와 pytest는 이 어긋남을 잡지 않는다.

검사 항목
  ① Settings 필드가 모두 사전에 있다. 사전의 키가 Settings 필드가 아니면 범위 표시
     (worker 전용·os.environ·배포 전용·uvicorn)가 있어야 한다. 오타와 지운 필드를 잡는다.
  ② prod `x-app-env`의 키가 모두 Settings 필드이거나, 사전에서 worker 전용·os.environ·uvicorn으로
     표시된 키다.
  ③ prod compose(공통 compose.yaml과 compose.prod.yaml)가 `${VAR:?}`로 요구하는 키가 사전에
     `prod 필수`로 있다. 반대로 `prod 필수`로 표시된 키는 `${VAR:?}`이거나 validator 필수 키다.
  ④ validator(`_enforce_prod_secrets`)가 요구하는 시크릿이 `x-app-env`로 전달된다.
  ⑤ 운영 판의 키가 모두 사전에 있고, `${VAR:?}` 키와 사전의 `prod 필수` 키가 모두 운영 판에 있다.
     deploy.sh가 넘기는 `IMAGE_TAG`는 예외다.
  ⑥ 개발 판의 키(주석 처리한 선택 키 포함)가 모두 사전에 있고,
     사전의 `local` 키가 모두 개발 판에 있다.

사전의 형식은 `docs/env-catalog.md` 머리에 있다. 항목은 표의 한 줄이고 첫 칸이 백틱으로 감싼 키,
둘째 칸이 `·`로 나눈 표시, 셋째 칸이 뜻이다.

한계: 이름과 전달만 본다. 값이 32자인지, 서버 `.env`에 채워져 있는지는 `${VAR:?}`가 배포 시점에,
validator가 기동 시점에 잡는다. config 밖에서 os.environ을 읽는 코드가 사전에 있는지는 보지 않는다.

    python3 scripts/ci/check-env-catalog.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "apps/api-gateway/api_gateway/config.py"
CATALOG = ROOT / "docs/env-catalog.md"
DEV_ENV = ROOT / ".env.example"
# prod는 공통 형상 위에 운영 덮어쓰기를 얹어 뜬다. `x-app-env`는 덮어쓰기에 있고,
# `${VAR:?}`는 어느 파일에 있든 배포를 막으므로 둘 다 읽는다.
COMPOSE = ROOT / "infra/compose/compose.prod.yaml"
COMPOSE_FILES = (ROOT / "infra/compose/compose.yaml", COMPOSE)
DEPLOY_ENV = ROOT / "infra/deploy/env.example"
VALIDATOR = "_enforce_prod_secrets"

# 사전에 쓸 수 있는 표시. `docs/env-catalog.md`의 표시 표와 같이 고친다.
TAGS = {"local", "prod 필수", "prod", "선택", "worker 전용", "os.environ", "배포 전용", "uvicorn"}
# Settings 필드가 아닌 키에 붙는 범위 표시.
SCOPE_TAGS = {"worker 전용", "os.environ", "배포 전용", "uvicorn"}
# `x-app-env`로 컨테이너에 넘겨도 되는 Settings 밖 범위. 배포 전용은 컨테이너로 가지 않는다.
APP_ENV_SCOPES = {"worker 전용", "os.environ", "uvicorn"}
# 서버 `.env`에 적지 않고 deploy.sh가 넘기는 키.
DEPLOY_PASSED = {"IMAGE_TAG"}

_ENTRY = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=")
_ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|\s*([^|]*?)\s*\|")
_REQUIRED = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")


def _fail(msg: str) -> SystemExit:
    """형식을 읽지 못하면 실패로 종료한다. 찾지 못한 항목을 통과로 세면 어긋남을 놓친다."""
    return SystemExit(f"✗ {msg}\n  형식이 바뀌었으면 이 스크립트도 같이 고친다.")


def settings_fields() -> set[str]:
    """config.Settings의 필드 이름을 대문자로 모은다. import 없이 AST로 읽는다."""
    tree = ast.parse(CONFIG.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            names = {
                b.target.id.upper()
                for b in node.body
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name)
                and b.target.id != "model_config"
            }
            if names:
                return names
    raise _fail(f"{CONFIG.relative_to(ROOT)}에서 Settings 필드를 찾지 못했다.")


def validator_keys() -> set[str]:
    """`_enforce_prod_secrets` 안의 `for key in (...)` 튜플을 대문자로 읽는다.

    목록이 여러 줄에 걸쳐 있어 grep 대신 AST를 쓴다.
    """
    tree = ast.parse(CONFIG.read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == VALIDATOR):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.For) and isinstance(sub.iter, (ast.Tuple, ast.List)):
                names = {e.value.upper() for e in sub.iter.elts if isinstance(e, ast.Constant)}
                if names:
                    return names
    raise _fail(f"{CONFIG.relative_to(ROOT)}에서 {VALIDATOR}의 키 목록을 찾지 못했다.")


def catalog() -> tuple[dict[str, set[str]], list[str]]:
    """사전 표의 키와 표시를 읽는다.

    Returns:
        키별 표시 집합과 형식 오류 목록. 표시가 없거나 모르는 표시를 쓴 항목, 두 번 적힌 키가
        오류다.
    """
    entries: dict[str, set[str]] = {}
    errors: list[str] = []
    name = CATALOG.relative_to(ROOT)
    for no, line in enumerate(CATALOG.read_text().splitlines(), 1):
        m = _ROW.match(line)
        if not m:
            continue
        key = m.group(1)
        tags = {t.strip() for t in re.split(r"[·,]", m.group(2)) if t.strip()}
        unknown = tags - TAGS
        if unknown:
            errors.append(f"{name}:{no} 모르는 표시 {sorted(unknown)}")
        if key in entries:
            errors.append(f"{name}:{no} {key}가 두 번 적혀 있다")
        if not tags:
            errors.append(f"{name}:{no} {key}에 표시가 없다")
        entries[key] = tags
    if not entries:
        raise _fail(f"{name}에서 항목을 찾지 못했다.")
    return entries, errors


def dev_env_keys() -> set[str]:
    """개발 판에 적힌 키를 모은다. 주석 처리한 선택 키도 센다."""
    keys = {
        m.group(1) for ln in DEV_ENV.read_text().splitlines() if (m := _ENTRY.match(ln))
    }
    if not keys:
        raise _fail(f"{DEV_ENV.relative_to(ROOT)}에서 키를 찾지 못했다.")
    return keys


def _compose_lines() -> list[str]:
    """prod가 쓰는 compose 파일들에서 주석 줄을 뺀 줄 목록을 돌려준다."""
    return [
        ln
        for f in COMPOSE_FILES
        for ln in f.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]


def app_env_keys() -> set[str]:
    """prod compose의 `x-app-env` 앵커 블록이 넘기는 키를 모은다."""
    lines = COMPOSE.read_text().splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("x-app-env:"))
    except StopIteration:
        raise _fail(f"{COMPOSE.relative_to(ROOT)}에 x-app-env 앵커가 없다.") from None
    keys: set[str] = set()
    for ln in lines[start + 1:]:
        if ln and not ln[0].isspace():
            break
        m = re.match(r"\s+([A-Z][A-Z0-9_]*):", ln)
        if m:
            keys.add(m.group(1))
    if not keys:
        raise _fail(f"{COMPOSE.relative_to(ROOT)}의 x-app-env가 비어 있다.")
    return keys


def required_compose_keys() -> set[str]:
    """prod compose가 `${VAR:?}`로 요구하는 키를 모은다. 주석 속 예시는 세지 않는다."""
    keys = {m for ln in _compose_lines() for m in _REQUIRED.findall(ln)}
    if not keys:
        raise _fail(f"{COMPOSE.relative_to(ROOT)}에서 ${{VAR:?}}를 찾지 못했다.")
    return keys


def deploy_env_keys() -> set[str]:
    """서버용 판에 적힌 키를 모은다. 주석 처리된 줄은 세지 않는다."""
    keys = {
        m.group(1)
        for ln in DEPLOY_ENV.read_text().splitlines()
        if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", ln))
    }
    if not keys:
        raise _fail(f"{DEPLOY_ENV.relative_to(ROOT)}에서 키를 찾지 못했다.")
    return keys


def check() -> list[str]:
    """여섯 검사를 돌려 어긋난 곳을 한 줄씩 돌려준다."""
    fields = settings_fields()
    secrets = validator_keys()
    entries, problems = catalog()
    app_env = app_env_keys()
    required = required_compose_keys()
    deploy = deploy_env_keys()
    dev = dev_env_keys()

    def tagged(*wanted: str) -> set[str]:
        """주어진 표시 중 하나라도 붙은 사전 키."""
        return {k for k, t in entries.items() if t & set(wanted)}

    for k in sorted(fields - entries.keys()):
        problems.append(f"① Settings 필드 {k}가 사전에 없다")
    for k in sorted(entries.keys() - fields):
        if not entries[k] & SCOPE_TAGS:
            problems.append(
                f"① 사전의 {k}는 Settings 필드가 아닌데 범위 표시가 없다(오타이거나 지운 필드)"
            )
    for k in sorted(fields & tagged(*SCOPE_TAGS)):
        scope = sorted(entries[k] & SCOPE_TAGS)
        problems.append(f"① {k}는 Settings 필드인데 범위 표시 {scope}가 붙어 있다")

    for k in sorted(app_env - fields - tagged(*APP_ENV_SCOPES)):
        problems.append(
            f"② x-app-env의 {k}는 Settings 필드도, 사전의 worker 전용·os.environ·uvicorn 키도"
            " 아니다"
        )

    must = tagged("prod 필수")
    for k in sorted(required - must):
        problems.append(f"③ prod compose가 ${{{k}:?}}로 요구하는데 사전에 prod 필수로 없다")
    for k in sorted(must - required - secrets):
        problems.append(
            f"③ 사전이 {k}를 prod 필수로 표시하는데 compose도 validator도 요구하지 않는다"
        )

    for k in sorted(secrets - app_env):
        problems.append(f"④ validator 필수 시크릿 {k}가 x-app-env로 전달되지 않는다")

    for k in sorted(deploy - entries.keys()):
        problems.append(f"⑤ 서버용 판의 {k}가 사전에 없다")
    for k in sorted((required | must) - deploy - DEPLOY_PASSED):
        problems.append(f"⑤ 운영 필수 키 {k}가 운영 판에 없다. 서버 .env에 채울 자리가 안 보인다")

    for k in sorted(dev - entries.keys()):
        problems.append(f"⑥ 개발 판의 {k}가 사전에 없다")
    for k in sorted(tagged("local") - dev):
        problems.append(f"⑥ 사전이 {k}를 local로 표시하는데 개발 판에 없다")

    print(
        f"사전 {len(entries)}종 · Settings {len(fields)}종 · x-app-env {len(app_env)}종 · "
        f"${{VAR:?}} {len(required)}종 · validator {len(secrets)}종 · 운영 판 {len(deploy)}종 · "
        f"개발 판 {len(dev)}종"
    )
    return problems


def main() -> int:
    """어긋난 곳을 보고한다. 하나라도 있으면 1을 돌려준다."""
    problems = check()
    if problems:
        print(f"\n✗ 환경변수 이름이 {len(problems)}곳에서 어긋난다:")
        for p in problems:
            print(f"  {p}")
        print(
            "\n  → 사전은 docs/env-catalog.md다. 키를 더하거나 지웠으면 거기에 표시와 함께 적고,"
            "\n     로컬에서 채우는 키면 루트 .env.example(개발 판)에, prod에서 쓰는 키면"
            "\n     infra/compose/compose.prod.yaml의 x-app-env와"
            "\n     infra/deploy/env.example(운영 판)에도 적는다."
        )
        return 1
    print("\n환경변수 사전이 config·prod compose·개발 판·운영 판과 맞는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
