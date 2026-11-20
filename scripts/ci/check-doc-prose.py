#!/usr/bin/env python3
"""문서 산문에서 `문장 — 문장`으로 이어 붙인 곳을 센다.

보고만 하고 막지 않는다. 화면 문구는 `check-copy.mjs`가 막는다.
문서 산문에서는 문장 잇기와 삽입구를 기계가 항상 정확히 구분하지 못하므로 수를 세어 보여 준다.
세지 않으면 같은 문체가 다시 쌓인다.

판정 기준은 대시 왼쪽이 문장으로 끝났는지 하나다.

    ✗  큐가 곧 마른다 — 라벨러가 논다        (양쪽이 문장이다)
    ✓  docs/ — 무엇을 어디서 찾나            (이름표 + 내용)

명사형(`음`·`함`·`임`)은 개조식 메모의 조각이라 종결로 보지 않는다.
코드펜스·코드 스팬·인용부호·링크 텍스트 안의 대시는 세지 않는다. 그 안의 글은
대개 화면 문구나 출력을 그대로 옮긴 인용이라서 고치면 인용이 틀린다.

    python3 scripts/ci/check-doc-prose.py [--list]
"""
import re
import sys
from pathlib import Path

FINAL = re.compile(r"(다|까|요|죠|네)$")
TAIL = re.compile(r'[*`_"”»\]』」]+$')
PAREN = re.compile(r"\([^()]*\)$")


def _core(left: str) -> str:
    """끝에 붙은 꾸밈(강조·코드·인용 기호, 끝 괄호)을 떼고 문장 본체만 남긴다."""
    s = left.rstrip()
    while True:
        m = TAIL.search(s)
        if m:
            s = s[: m.start()]
            continue
        m = PAREN.search(s)
        if m and re.search(r"[가-힣]", (s[: m.start()][-3:] or "")):
            s = s[: m.start()]
            continue
        return s


def joins(path: Path):
    """이 파일에서 `문장 — 문장`인 곳을 `(줄 번호, 줄)` 목록으로 돌려준다."""
    out, fenced = [], False
    for i, line in enumerate(path.read_text().split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or " — " not in line:
            continue
        for m in re.finditer(" — ", line):
            left = line[: m.start()]
            if left.count("«") > left.count("»") or left.count('"') % 2 == 1:
                continue
            if left.count("`") % 2 == 1 or left.count("[") > left.count("]"):
                continue
            core = _core(left)
            if core and re.search(r"[가-힣]$", core) and FINAL.search(core):
                out.append((i, line.strip()))
    return out


def main() -> int:
    """`docs/` 아래 마크다운에서 이어 붙인 곳의 수를 찍는다. 막지 않으므로 항상 0이다."""
    root = Path(__file__).resolve().parents[2]
    hits = [(f, i, t) for f in sorted((root / "docs").rglob("*.md")) for i, t in joins(f)]
    if not hits:
        print("✅ 문서 산문에 `문장 — 문장` 이어 붙이기 없음")
        return 0
    files = len({f for f, _, _ in hits})
    print(f"ℹ️  `문장 — 문장` {len(hits)}곳 · {files}파일 — 막지 않습니다. 읽고 판단하세요.")
    if "--list" in sys.argv:
        for f, i, t in hits:
            print(f"   {f.relative_to(root)}:{i}  {t[:96]}")
    else:
        print("   자리를 보려면 --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
