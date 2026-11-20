#!/usr/bin/env python3
"""독스트링처럼 보이지만 파서가 버리는 문자열을 찾는다(파이썬 쪽 검사).

FE의 `check-orphan-docstrings.mjs`와 같은 종류의 검사다. 그쪽은 대상이 없는 독스트링을,
이쪽은 애초에 독스트링이 아닌 문자열을 본다.

파이썬은 모듈·클래스·함수의 첫 문장일 때만 문자열을 독스트링으로 삼는다.
함수 본문 중간에 놓인 `\"\"\"…\"\"\"`는 값으로 평가된 뒤 버려진다.
`help()`에 나오지 않고 IDE도 보여 주지 않는다. `ruff` 기본 규칙도 경고하지 않는다.
그래서 읽는 사람만 문서라고 믿게 된다.
그런 문자열은 주석(`#`)으로 바꾸면 같은 위치에서 같은 역할을 한다.

판정: `ast`로 모듈·클래스·함수의 첫 문장이 아닌 문자열 표현식 문장
(`Expr(Constant(str))`)을 찾는다. 정규식으로는 할 수 없다. 여러 줄 문자열과 실제
독스트링을 구분할 수 없기 때문이다.

한계: 위치만 보므로 내용이 낡은 독스트링은 잡지 못한다. 내용은 사람이 읽고 판단한다.

사용법:  python3 scripts/ci/check-dead-docstrings.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOTS = ("apps/api-gateway/api_gateway", "apps/api-gateway/tests", "packages", "scripts")


def _is_str_expr(node: ast.stmt) -> bool:
    """문자열 하나만 있는 문장이면 True. 독스트링과 같은 형태다."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def main() -> int:
    """`ROOTS` 아래 파이썬 파일에서 독스트링 위치가 아닌 문자열을 찍는다. 있으면 1을 돌려준다."""
    hits: list[tuple[str, int, str]] = []
    for root in ROOTS:
        base = pathlib.Path(root)
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if not isinstance(body, list) or not body:
                    continue
                # 첫 문장이 문자열이면 실제 독스트링이다. 그다음 문장부터 센다.
                start = 1 if _is_str_expr(body[0]) else 0
                for st in body[start:]:
                    if _is_str_expr(st):
                        gist = st.value.value.strip().replace("\n", " ")[:60]  # type: ignore[attr-defined]
                        hits.append((str(f), st.lineno, gist))

    if hits:
        print(f"❌ 독스트링 자리가 아닌 문자열 {len(hits)}건:\n", file=sys.stderr)
        for f, line, gist in hits:
            print(f"  {f}:{line}\n      {gist}", file=sys.stderr)
        print(
            "\n  파이썬은 **첫 문장일 때만** 문자열을 독스트링으로 삼습니다.\n"
            "  그 자리가 아니면 값으로 버려집니다 — 주석(`#`)으로 바꾸세요.",
            file=sys.stderr,
        )
        return 1
    print("✅ 독스트링 자리가 아닌 문자열 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
