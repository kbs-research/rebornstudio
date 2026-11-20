"""서버가 던지는 에러 코드가 화면 사전에 있는지 세는 게이트.

추출기(`list_error_codes.py`)와 사전(`apps/web/src/shared/api/error_text.ts`)을 잇는다.
이 검사가 없으면 새 `raise`를 쓸 때 사전에 넣는 일이 기억에만 달린다. 사전에 없는 코드는
폴백이 원문을 막아 "처리하지 못했습니다"라는 일반 문구로 표시된다. 남는 신호는
`console.warn`뿐이라 누락이 드러나지 않는다.

서버에서 사전 방향으로만 센다. 반대 방향(사전에만 있는 키)은 셀 수 없다.
추출기는 `raise` 위치의 문자열 리터럴만 보는데, 역할 게이트 코드는 `require_*`
헬퍼의 인자를 거쳐 변수로 던져진다. 그 방향을 게이트로 만들면
살아 있는 키(`not_reviewer` 등)가 첫 실행부터 쓰이지 않는 키로 나온다.
그 결과를 믿고 지우면 해당 화면들이 일반 문구를 보여 주게 된다.

통과 조건: 리터럴 코드는 EXACT에 있거나, PREFIX 하나에 걸리거나, 아래 EXPECTED에
이유와 함께 적혀 있어야 한다. 목록에서 그냥 빼지 않는다. 다음 사람이 지워도 되는지
알 수 없게 된다(`check-orphan-routes`와 같은 방식).

    uv run --package api-gateway python scripts/ci/check-error-codes.py
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from list_error_codes import DEFAULT_ROOT, collect  # noqa: E402

DICT = pathlib.Path(__file__).resolve().parents[2] / "apps/web/src/shared/api/error_text.ts"

#: 화면에 닿지 않아 사전이 필요 없는 코드. 이유를 함께 적는다.
EXPECTED: dict[str, str] = {
    "csp_report_invalid": (
        "CSP 위반 보고 수집 경로(`POST /csp-report`)가 보고 형식이 아닌 본문에 낸다. "
        "브라우저가 배경에서 보내는 요청이라 화면에 닿지 않는다"
    ),
}


def dictionary() -> tuple[set[str], list[str]]:
    """사전에서 EXACT 키와 PREFIX 접두사를 뽑는다."""
    s = DICT.read_text(encoding="utf-8")
    exact_body = s.split("const EXACT")[1].split("const PREFIX")[0]
    exact = set(re.findall(r"^\s{2}([a-z0-9_]+):", exact_body, re.M))
    prefixes = re.findall(r'^\s*\["([a-z0-9_]+)",', s.split("const PREFIX")[1], re.M)
    return exact, prefixes


def main() -> int:
    """빠진 코드와 낡은 제외 항목을 찍는다. 둘 중 하나라도 있으면 1을 돌려준다."""
    exact, prefixes = dictionary()
    rows = collect(DEFAULT_ROOT)

    # 한국어 문장은 서버가 화면에 그대로 표시하라고 보낸 문구라 세지 않는다. 변수(expr)와
    # f-string은 리터럴이 아니라 여기서 판정할 수 없다. 셀 수 있는 것은 기계 코드 리터럴뿐이다.
    literals = {
        r["detail"] for r in rows
        if r["kind"] == "literal" and re.fullmatch(r"[a-z0-9_]+", r["detail"])
    }
    where = {r["detail"]: f"{r['file']}:{r['line']}" for r in rows}

    missing = sorted(
        c for c in literals
        if c not in exact and c not in EXPECTED and not any(c.startswith(p) for p in prefixes)
    )
    stale = sorted(c for c in EXPECTED if c not in literals)

    print(f"서버 리터럴 코드 {len(literals)}종 · 사전 EXACT {len(exact)}종 "
          f"· PREFIX {len(prefixes)}개 · 제외 {len(EXPECTED)}종")
    if stale:
        print("\n제외 목록에 있는데 서버엔 없는 코드 — EXPECTED에서 빼주세요:")
        for c in stale:
            print(f"  {c}")
    if missing:
        print(f"\n사전에 없는 코드 {len(missing)}종 — 화면이 맹탕 문구를 냅니다:")
        for c in missing:
            print(f"  {c:<34} {where.get(c, '')}")
        print("\n  → error_text.ts에 문구를 넣거나, 화면에 안 닿으면 "
              "이 스크립트의 EXPECTED에 이유와 함께 적어주세요.")
        return 1
    print("\n빠진 코드 없음")
    return 0 if not stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
