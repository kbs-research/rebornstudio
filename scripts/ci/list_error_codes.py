"""`raise HTTPException`을 AST로 모두 뽑는다. 에러 코드 사전(`check-error-codes.py`)의 입력이다.

grep을 쓰지 않는 이유는 grep이 아래 세 경우를 놓치기 때문이다.

1. 여러 줄에 걸친 `raise`: `status_code=`와 `detail=`이 다른 줄에 있는 경우다.
2. f-string detail: `f"work_item_is_{item.status}"`. 코드가 런타임에 정해지므로 문자열
   리터럴 검색으로는 잡히지 않는다. 사전에서 빠지면 "없는 코드"로 취급된다
   (`check-event-labels.py`가 다루는 동적 `event_type`과 같은 유형).
3. 변수 detail: `_PWD_POLICY_MSG` · `blocker`처럼 이름만 있는 경우다. grep으로는
   아예 보이지 않는다.

출력: 기본은 사람이 읽는 요약이고, `--json`이면 원자료다.

    uv run --package api-gateway python scripts/ci/list_error_codes.py
    uv run --package api-gateway python scripts/ci/list_error_codes.py --json > /tmp/errors.json

`kind`는 `literal`(고정 문자열) · `fstring`(동적) · `expr`(변수) 중 하나다.
`fstring`·`expr`은 런타임 값을 직접 전개해야 사전이 완성된다.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
import re
import sys

#: 기본 대상. HTTPException은 api-gateway만 던진다(워커는 큐로 회신한다).
DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[2] / "apps/api-gateway/api_gateway"
#: 상태 코드를 스스로 정하는 `HTTPException` 하위 클래스와 그 상태 코드. 첫 인자가 오류 코드다.
#: `connections.InUseError`는 사용 중 거절(409)에 막는 프로젝트 이름을 싣는다.
_FIXED_STATUS: dict[str, str] = {"InUseError": "409"}


def _status_of(node: ast.AST) -> str:
    """상태 코드 인자를 숫자 문자열로 바꾼다. `status.HTTP_404_NOT_FOUND`는 `"404"`가 된다."""
    if isinstance(node, ast.Attribute) and node.attr.startswith("HTTP_"):
        return node.attr.split("_")[1]
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return str(node.value)
    return ast.unparse(node)


def _detail_of(node: ast.AST) -> tuple[str, str]:
    """detail 인자를 `(kind, value)`로 바꾼다. f-string은 치환부를 `{expr}` 형태로 복원한다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "literal", node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                parts.append("{" + ast.unparse(v.value) + "}")
        return "fstring", "".join(parts)
    return "expr", ast.unparse(node)


def collect(root: pathlib.Path) -> list[dict]:
    """`root` 아래 모든 `raise HTTPException(...)`을 찾아 dict 목록으로 돌려준다.

    Args:
        root: 훑을 패키지 디렉터리.

    Returns:
        `{file, line, status, kind, detail}` 목록. 파일·줄 순으로 정렬돼 있다.
    """
    repo = root.parent.parent.parent
    rows: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            fn = node.exc.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "HTTPException" and name not in _FIXED_STATUS:
                continue
            st = det = None
            pos = list(node.exc.args)
            if name in _FIXED_STATUS:
                st = ast.Constant(int(_FIXED_STATUS[name]))
            for kw in node.exc.keywords:
                if kw.arg == "status_code":
                    st = kw.value
                elif kw.arg == "detail":
                    det = kw.value
            if st is None and pos:
                st = pos.pop(0)
            if det is None and pos:
                det = pos.pop(0)
            kind, val = _detail_of(det) if det is not None else ("none", "")
            rows.append({
                "file": str(path.relative_to(repo)),
                "line": node.lineno,
                "status": _status_of(st) if st is not None else "?",
                "kind": kind,
                "detail": val,
            })
    return rows


def _classify(row: dict) -> str:
    """코드 한 건을 사전에서 다르게 다뤄야 하는 네 가지 성격 중 하나로 분류한다."""
    if row["kind"] == "expr":
        return "변수(런타임)"
    if re.search(r"[가-힣]", row["detail"]):
        return "한국어 문장"
    if row["kind"] == "fstring":
        return "동적 코드"
    return "기계 코드"


def main() -> int:
    """요약을 출력한다. `--json`이면 원자료를 stdout으로 출력한다."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true", help="원자료를 JSON으로 출력")
    args = ap.parse_args()

    rows = collect(args.root)
    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
        return 0

    print(f"raise 사이트: {len(rows)}  ·  고유 detail: {len({r['detail'] for r in rows})}")
    print("\n성격별:")
    for k, v in collections.Counter(_classify(r) for r in rows).most_common():
        print(f"  {k}: {v}")
    print("\n상태코드별:")
    for k, v in sorted(collections.Counter(r["status"] for r in rows).items(),
                       key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")

    # 동적 코드와 변수는 직접 전개해야 하므로 따로 출력한다. 놓치면 사전에서 빠진다.
    for label in ("동적 코드", "변수(런타임)"):
        picked = [r for r in rows if _classify(r) == label]
        print(f"\n{label} — 런타임 값 전개 필요 ({len(picked)}곳):")
        for r in picked:
            print(f"  {r['status']}  {r['detail']:<46} {r['file']}:{r['line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
