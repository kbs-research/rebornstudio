"""주석·docstring만 바뀐 변경인지 확인한다.

주석 정리가 코드와 정보를 건드리지 않았는지 기계로 본다.

사용법:
    python3 scripts/ci/check-comment-only.py [BASE]      # 기본 BASE = origin/main

BASE와 지금 작업 트리(커밋 안 한 변경 포함)를 파일마다 비교해 세 가지를 본다.

1. 코드가 그대로인가. 파이썬은 docstring을 뺀 AST를, TS·JS는 주석을 뺀 출력을 비교한다.
2. 도구 지시 주석이 그대로인가. `# noqa`, `# type: ignore`, `// @ts-expect-error` 같은 주석은
   도구 동작을 바꾸므로 한 글자도 바뀌면 안 된다.
3. 주석 속 참조가 줄었는가. 파일마다 사라진 PR 번호, Step 번호,
   결정·검토 ID(대문자 접두, 하이픈, 숫자)를 경고로 보여 준다. 참조는 결정을 찾아갈 하나로 줄인다.
   그래서 줄어드는 것은 정상이다.
   사라진 경위가 커밋 기록에 남았는지는 리뷰에서 본다. 백틱으로 쓴 식별자가 빠져도 경고한다.
4. 주석 속 수치가 줄었는가. 단위가 붙은 수(`25면`, `3회`, `168시간`)와 분수(`25/44`)가 빠지면
   경고로 보여 준다. 상수를 고른 이유가 된 수치는 남기고 경위를 설명하는 통계는 뺀다.
   끝에 실행 전체의 합계(사실이 든 주석 줄 수, 수치 수, 참조 수)를 전후로 찍는다.
5. 주석이 대는 이름이 실제로 있는가. 백틱 안의 이름 중 `_`나 `.`가 든 것은
   코드 파일의 낱말에서 찾는다. 백틱 안의 API 경로(`/projects/{id}/events`)는
   `docs/api/openapi.json`에서 찾는다. 새로 생긴 것 중 없는 것을 경고한다.
   주석을 고치다 없는 이름을 지어내거나 경로를 잘못 적는 것을 잡는다.
   있는 이름을 틀리게 설명한 경우는 잡지 못한다. 그 경우는 사람이 코드와 대조해 확인한다.

생성물(`docs/api/openapi.json`, `packages/reborn-contracts/typescript/src/api.ts`)은
비교하지 않는다. docstring이 바뀌면 다시 생성해야 하므로 바뀌는 것이 정상이다.

종료 코드: 0 = 통과, 1 = 오류 있음, 2 = 실행 실패.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 주석만 바뀌어도 다시 생성되는 파일. 비교하지 않는다.
GENERATED = {
    "docs/api/openapi.json",
    "packages/reborn-contracts/typescript/src/api.ts",
}
#: 실행 전체의 사실 수 합계(`facts`). 비교한 파일마다 `compare`가 더한다.
TOTAL_BEFORE: Counter[str] = Counter()
TOTAL_AFTER: Counter[str] = Counter()
PY_EXT = (".py",)
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

#: 도구 동작을 바꾸는 주석. 파이썬·TS 공통으로 이 패턴이 든 주석은 그대로여야 한다.
DIRECTIVE = re.compile(
    r"noqa|type:\s*ignore|pragma|fmt:\s*(?:off|on|skip)|isort:|pylint:|mypy:|pyright:|ruff:"
    r"|@ts-(?:ignore|expect-error|nocheck|check)|eslint-|prettier-ignore|biome-ignore"
    r"|istanbul|c8 ignore|@vite-ignore|webpack|@jsx|@refresh|^///\s*<reference|^#!|coding[:=]",
)

#: 지켜야 하는 참조. PR 번호(PR # 뒤 숫자), Step 번호, 대문자 ID(대문자 접두, 하이픈, 숫자).
REF_PATTERNS = (
    re.compile(r"#(\d{2,4})\b"),
    re.compile(r"\b[Ss]tep[\s-]?(\d{1,3})\b"),
    re.compile(r"\b((?:[A-Z]{1,4}-)+\d{1,4}[a-z]?)\b"),
)
BACKTICK = re.compile(r"`([^`\n]{1,80})`")

#: 단위별로 뒤에 오면 단위가 아니게 되는 글자. 그 글자가 오면 단위가 아니라 다른 낱말의 앞 글자다.
#: 예: `3분류`, `0 분모`, `30 배치`, `p95 초과`.
#: 수 바로 뒤에서 수치로 읽히는 것(`50개인`, `8자리`)은 넣지 않는다.
_NOT_UNIT = {
    "분": "류모자석리할기산량포", "배": "치포정경지열분", "회": "피귀수차전원의",
    "초": "과기안당", "일": "부괄정치반단관", "명": "시령확칭단", "행": "동렬",
    "열": "람거", "종": "류료속", "자": "동원체료", "개": "선수발요편념별",
    "편": "집의", "번": "호역", "건": "너", "곳": "곳",
}
_UNITS = [
    "개월", "시간", "토큰", "건", "개", "회", "초", "분", "일", "주", "%", "px", "ms",
    "MB", "GB", "KB", "배", "명", "편", "줄", "곳", "종", "번", "자", "면", "행", "열", "쌍",
]
#: 주석 속 수치. 단위가 붙은 수와 분수(`25/44`)를 센다.
#: 단위 없는 수는 버전·줄 번호와 섞여서 세지 않는다.
NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s?(?:"
    + "|".join(u + (f"(?![{_NOT_UNIT[u]}])" if u in _NOT_UNIT else "") for u in _UNITS)
    + r")|\b\d+\s?/\s?\d+\b"
)
#: 백틱 안의 이름. `_`나 `.`가 든 것만 코드 낱말과 대조한다(평범한 낱말은 코드에 없어도 된다).
_IDENT = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(\))?$")
#: 백틱 안의 API 경로. 앞에 HTTP 메서드가 붙을 수 있다.
_API_PATH = re.compile(r"^(?:(?:GET|POST|PUT|PATCH|DELETE)\s+)?(/[A-Za-z0-9_\-{}/]+)$")
#: OpenAPI 스키마 밖에 있는 실제 경로(Prometheus 계측 등).
_PATHS_OUTSIDE_OPENAPI = {"/metrics", "/health", "/docs", "/openapi.json"}
#: 경위를 적었다는 표시. 작성 기준이 경위를 빼게 하므로 전후 수가 달라지는 것이 정상이다.
#: 그래서 경고에는 쓰지 않고 합계에만 쓴다.
MARKER = re.compile(r"배경:|실측|실화면|실제로")


def run(*cmd: str, stdin: str | None = None) -> str:
    """명령을 실행하고 표준 출력을 돌려준다. 실패하면 예외를 낸다."""
    res = subprocess.run(cmd, cwd=ROOT, input=stdin, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} 실패: {res.stderr.strip()[:500]}")
    return res.stdout


def changed_files(base: str) -> list[tuple[str, str]]:
    """BASE 대비 바뀐 파일 목록 `(상태, 경로)`. 작업 트리의 커밋 안 한 변경도 포함한다."""
    out = run("git", "diff", "--name-status", "--no-renames", base)
    files = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        files.append((status.strip()[:1], path.strip()))
    return files


def base_text(base: str, path: str) -> str:
    """BASE 시점의 파일 내용."""
    return run("git", "show", f"{base}:{path}")


# ── 파이썬 ────────────────────────────────────────────────────────────────


def py_code(src: str) -> str:
    """docstring과 문자열만 있는 문장을 뺀 AST 덤프. 주석(`#`)은 원래 AST에 없다."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            kept = [
                s for s in stmts
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                        and isinstance(s.value.value, str))
            ]
            if stmts and not kept and field == "body":
                kept = [ast.Pass()]
            setattr(node, field, kept)
    return ast.dump(tree, include_attributes=False)


def py_hash_comments(src: str) -> list[str]:
    """`#` 주석 원문 목록. 도구 지시 주석은 여기에만 있다(docstring은 도구가 읽지 않는다)."""
    return [
        tok.string
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type == tokenize.COMMENT
    ]


def py_comments(src: str) -> list[str]:
    """`#` 주석과 docstring(문자열만 있는 문장) 원문 목록."""
    out = py_hash_comments(src)
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out.append(node.value.value)
    return out


# ── TS·JS ────────────────────────────────────────────────────────────────


def ts_path() -> str:
    """TypeScript 모듈 경로. 작업 트리에 없으면 `git worktree` 원본 체크아웃의 것을 쓴다."""
    candidates = [ROOT / "apps/web/node_modules/typescript"]
    common = Path(run("git", "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    candidates.append(common.parent / "apps/web/node_modules/typescript")
    for c in candidates:
        if (c / "package.json").exists():
            return str(c)
    raise RuntimeError("typescript 모듈을 찾지 못했다. apps/web에서 의존성을 설치한다.")


def ts_analyze(items: list[dict]) -> dict:
    """`strip-comments.mjs`로 주석을 뺀 출력과 주석 목록을 한 번에 받는다."""
    if not items:
        return {}
    env = {**os.environ, "TS_PATH": ts_path()}
    res = subprocess.run(
        ["node", str(ROOT / "scripts/ci/strip-comments.mjs")],
        cwd=ROOT, input=json.dumps(items), capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        raise RuntimeError(f"strip-comments.mjs 실패: {res.stderr.strip()[:500]}")
    return json.loads(res.stdout)


# ── 비교 ──────────────────────────────────────────────────────────────────


def refs(comments: list[str]) -> set[str]:
    """주석 속 참조(PR·Step·ID)를 모은다."""
    found: set[str] = set()
    for c in comments:
        for pat in REF_PATTERNS:
            for m in pat.finditer(c):
                found.add(m.group(0).replace("step", "Step"))
    return found


def normalize_ref(r: str) -> str:
    """PR 번호는 PR 접두가 있든 없든, Step 번호는 공백으로 잇든 하이픈으로 잇든 같은 참조로 본다."""
    m = re.search(r"#(\d+)", r)
    if m:
        return f"#{m.group(1)}"
    m = re.match(r"[Ss]tep[\s-]?(\d+)", r)
    if m:
        return f"Step {m.group(1)}"
    return r


_REPO_TOKENS: set[str] | None = None
_OPENAPI_PATHS: list[str] | None = None
#: 비교 기준 커밋. `main()`이 인자로 정한다. 낱말 집합을 이 커밋에서 모은다.
_BASE = "origin/main"


def repo_tokens() -> set[str]:
    """기준 커밋의 코드 파일에 나오는 모든 낱말(식별자 모양). 처음 부를 때 한 번 모은다.

    작업 트리가 아니라 기준 커밋에서 모은다. 작업 트리에서 모으면 주석에 새로 지어낸 이름도
    그 주석 때문에 "있는 이름"이 되어 잡히지 않는다.
    """
    global _REPO_TOKENS
    if _REPO_TOKENS is None:
        out = run("git", "grep", "-h", "-o", "-E", r"[A-Za-z_][A-Za-z0-9_]*", _BASE,
                  "--", "*.py", "*.ts", "*.tsx", "*.js", "*.mjs")
        _REPO_TOKENS = set(out.split())
    return _REPO_TOKENS


def openapi_paths() -> list[str]:
    """OpenAPI 경로 목록. `{project_id}` 같은 자리는 모두 `{}`로 맞춘다."""
    global _OPENAPI_PATHS
    if _OPENAPI_PATHS is None:
        spec = json.loads((ROOT / "docs/api/openapi.json").read_text(encoding="utf-8"))
        _OPENAPI_PATHS = [re.sub(r"\{[^}]*\}", "{}", p) for p in spec.get("paths", {})]
    return _OPENAPI_PATHS


def web_routes() -> set[str]:
    """웹 라우트 경로(`app/router.tsx`·`features/*/routes.tsx`의 `path`)를 모은다.

    `:slug` 같은 자리는 `{}`로 맞춘다.
    `/setup`처럼 API와 첫 마디가 같은 화면 주소를 API 경로로 잘못 보지 않으려고 쓴다.
    """
    web = ROOT / "apps/web/src"
    files = [web / "app/router.tsx", *sorted(web.glob("features/*/routes.tsx"))]
    text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return {re.sub(r":\w+", "{}", p) for p in re.findall(r'path:\s*"([^"]+)"', text)}


def unknown_names(comments: list[str]) -> tuple[list[str], list[str]]:
    """주석이 백틱으로 대는 이름과 API 경로 중 저장소에 없는 것.

    Returns:
        (코드에 없는 이름, OpenAPI에 없는 경로).
    """
    tokens = repo_tokens()
    paths = openapi_paths()
    screens = web_routes()
    first_segments = {p.split("/")[1] for p in paths if p.count("/") >= 1}
    names, bad_paths = set(), set()
    for c in comments:
        for tick in BACKTICK.findall(c):
            tick = tick.strip()
            m = _IDENT.match(tick)
            if m and ("_" in m.group(1) or "." in m.group(1)):
                if m.group(1).split(".")[-1] not in tokens:
                    names.add(m.group(1))
                continue
            m = _API_PATH.match(tick)
            if not m or ":" in tick:
                continue
            path = re.sub(r"\{[^}]*\}", "{}", m.group(1).rstrip("/"))
            if not path:
                continue  # 슬래시만 있는 표기(`//`)는 건너뛴다.
            if path in _PATHS_OUTSIDE_OPENAPI or path in screens:
                continue
            if path.split("/")[1] not in first_segments:
                continue
            # 줄여 쓴 경로(`/me/leave` = `/projects/{}/me/leave`)는 뒤쪽이 맞으면 있는 것으로 본다.
            if not any(p == path or p.endswith(path) for p in paths):
                bad_paths.add(m.group(1))
    return sorted(names), sorted(bad_paths)


def numbers(comments: list[str]) -> list[str]:
    """주석 속 수치 목록. 공백을 빼서 `25 면`과 `25면`을 같은 수치로 본다."""
    return [re.sub(r"\s", "", m.group(0)) for c in comments for m in NUMBER.finditer(c)]


def fact_lines(comments: list[str]) -> int:
    """참조·수치·경위 표시 중 하나라도 든 주석 줄 수."""
    lines = [line for c in comments for line in c.splitlines()]
    return sum(
        1 for line in lines
        if NUMBER.search(line) or MARKER.search(line) or any(p.search(line) for p in REF_PATTERNS)
    )


def facts(comments: list[str]) -> dict[str, int]:
    """실행 합계에 더할 한 파일의 사실 수."""
    return {
        "lines": fact_lines(comments),
        "numbers": len(numbers(comments)),
        "refs": len({normalize_ref(r) for r in refs(comments)}),
    }


def directives(comments: list[str]) -> list[str]:
    """도구 지시 주석만 골라 정렬한다(개수까지 비교하려고 목록으로 둔다)."""
    return sorted(c.strip() for c in comments if DIRECTIVE.search(c.strip()))


def compare(path: str, before: dict, after: dict) -> tuple[list[str], list[str]]:
    """한 파일의 오류·경고 목록."""
    errors, warnings = [], []
    if before["code"] != after["code"]:
        errors.append("코드가 바뀌었다(주석 밖의 변경)")
    # 파이썬은 `#` 주석만 본다. docstring은 지시어를 설명하는 문장이 있어도 도구가 읽지 않는다.
    if directives(before.get("hash", before["comments"])) != directives(
        after.get("hash", after["comments"])
    ):
        errors.append("도구 지시 주석이 바뀌었다")
    lost = {normalize_ref(r) for r in refs(before["comments"])} - {
        normalize_ref(r) for r in refs(after["comments"])
    }
    if lost:
        warnings.append("참조가 빠졌다: " + ", ".join(sorted(lost)))
    ticks_before = {m for c in before["comments"] for m in BACKTICK.findall(c)}
    ticks_after = {m for c in after["comments"] for m in BACKTICK.findall(c)}
    if ticks_before - ticks_after:
        warnings.append("식별자가 빠졌다: " + ", ".join(sorted(ticks_before - ticks_after)[:12]))
    lost_numbers = Counter(numbers(before["comments"])) - Counter(numbers(after["comments"]))
    if lost_numbers:
        warnings.append("수치가 빠졌다: " + ", ".join(sorted(lost_numbers.elements())[:12]))
    # 새로 생긴 이름만 본다. 원래 있던 틀린 이름까지 보면 이 정리와 무관한 경고가 쌓인다.
    old_names, old_paths = unknown_names(before["comments"])
    new_names, new_paths = unknown_names(after["comments"])
    added_names = sorted(set(new_names) - set(old_names))
    added_paths = sorted(set(new_paths) - set(old_paths))
    if added_names:
        warnings.append("코드에 없는 이름: " + ", ".join(added_names[:12]))
    if added_paths:
        warnings.append("OpenAPI에 없는 경로: " + ", ".join(added_paths[:12]))
    TOTAL_BEFORE.update(facts(before["comments"]))
    TOTAL_AFTER.update(facts(after["comments"]))
    return errors, warnings


def main() -> int:
    """변경된 파일마다 비교하고 결과를 찍는다."""
    global _BASE
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    _BASE = base
    files = [(s, p) for s, p in changed_files(base) if p not in GENERATED]
    if not files:
        print("바뀐 파일이 없다.")
        return 0

    problems: dict[str, tuple[list[str], list[str]]] = {}
    ts_items = []
    for status, path in files:
        if status != "M":
            problems[path] = ([f"주석 정리에서 파일을 추가·삭제하지 않는다(상태 {status})"], [])
            continue
        if path.endswith(PY_EXT):
            b, a = base_text(base, path), (ROOT / path).read_text(encoding="utf-8")
            problems[path] = compare(
                path,
                {"code": py_code(b), "comments": py_comments(b), "hash": py_hash_comments(b)},
                {"code": py_code(a), "comments": py_comments(a), "hash": py_hash_comments(a)},
            )
        elif path.endswith(TS_EXT):
            ts_items.append({"key": f"B:{path}", "path": path, "text": base_text(base, path)})
            ts_items.append({"key": f"A:{path}", "path": path,
                             "text": (ROOT / path).read_text(encoding="utf-8")})
        else:
            problems[path] = (["코드 파일이 아니다. 주석 정리 대상이 아니다"], [])

    analyzed = ts_analyze(ts_items)
    for item in ts_items[::2]:
        path = item["path"]
        problems[path] = compare(path, analyzed[f"B:{path}"], analyzed[f"A:{path}"])

    n_err = 0
    for path in sorted(problems):
        errors, warnings = problems[path]
        n_err += bool(errors)
        mark = "✗" if errors else ("!" if warnings else "✓")
        print(f"{mark} {path}")
        for e in errors:
            print(f"    오류: {e}")
        for w in warnings:
            print(f"    경고: {w}")
    print(f"\n{len(problems)}개 파일 · 오류 {n_err}개 파일")
    b, a = TOTAL_BEFORE, TOTAL_AFTER
    print(
        f"사실이 든 주석 줄 {b['lines']} → {a['lines']} · 수치 {b['numbers']} → {a['numbers']}"
        f" · 참조 {b['refs']} → {a['refs']}"
    )
    return 1 if n_err else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        sys.exit(2)
