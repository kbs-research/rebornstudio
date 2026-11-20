#!/usr/bin/env python3
"""주석·docstring에서 쓰지 않는 말투를 영역별로 센다.

주석과 docstring만 본다. 문자열 리터럴과 화면 문구는 보지 않는다.
화면 문구는 `apps/web/scripts/check-copy.mjs`가 본다.

사용법:
    python3 scripts/ci/check-comment-style.py              # 영역별·규칙별 건수
    python3 scripts/ci/check-comment-style.py --list       # 위치와 줄까지
    python3 scripts/ci/check-comment-style.py --rule 지시어 --area web
    python3 scripts/ci/check-comment-style.py --strict     # 1건이라도 있으면 종료 코드 1
    python3 scripts/ci/check-comment-style.py PATH...      # 지정한 파일만

기본은 보고만 하고 종료 코드 0을 낸다. 문맥을 보지 않는 패턴 검사라 맞는 표현도 걸린다.
`--strict`는 전수 정리를 마친 규칙만 골라 켜도록 `--rule`과 함께 쓴다.

종료 코드: 0 = 보고 완료(또는 `--strict`에서 0건), 1 = `--strict`에서 1건 이상, 2 = 실행 실패.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PY_EXT = (".py",)
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
#: 생성물. 주석을 생성기(openapi-typescript)가 만들므로 보지 않는다.
GENERATED = {"packages/reborn-contracts/typescript/src/api.ts"}

#: 도구 동작을 바꾸는 주석. 말투 규칙을 적용하지 않는다(`check-comment-only.py`와 같은 목록).
DIRECTIVE = re.compile(
    r"noqa|type:\s*ignore|pragma|fmt:\s*(?:off|on|skip)|isort:|pylint:|mypy:|pyright:|ruff:"
    r"|@ts-(?:ignore|expect-error|nocheck|check)|eslint-|prettier-ignore|biome-ignore"
    r"|istanbul|c8 ignore|@vite-ignore|webpack|@jsx|@refresh|^///\s*<reference|^#!|coding[:=]",
)

#: 영역 이름과 경로 접두사. 위에서부터 처음 맞는 것을 쓴다.
AREAS: list[tuple[str, str]] = [
    ("api-services", "apps/api-gateway/api_gateway/services/"),
    ("api-routes", "apps/api-gateway/api_gateway/api/"),
    ("api-other", "apps/api-gateway/api_gateway/"),
    ("api-tests", "apps/api-gateway/tests/"),
    ("alembic", "apps/api-gateway/alembic/"),
    ("web", "apps/web/src/"),
    ("web-scripts", "apps/web/scripts/"),
    ("transcode", "packages/reborn-transcode/"),
    ("contracts", "packages/reborn-contracts/"),
    ("workers", "workers/"),
    ("scripts", "scripts/"),
]


def area_of(path: str) -> str:
    """경로가 속한 영역 이름. 어느 접두사에도 맞지 않으면 `etc`."""
    for name, prefix in AREAS:
        if path.startswith(prefix):
            return name
    return "etc"


# ── 규칙 ──────────────────────────────────────────────────────────────────

#: 한글 낱말의 앞뒤 경계. 파이썬 `\b`는 한글도 낱말 글자로 보므로 직접 쓴다.
_H = "(?<![가-힣])"
_E = "(?![가-힣])"


def _any(*parts: str) -> str:
    """정규식 조각을 `|`로 잇는다. 긴 패턴을 한 줄 100자 안에서 나눠 쓰려고 둔다."""
    return "|".join(parts)


@dataclass(frozen=True)
class Rule:
    """한 줄에 적용하는 말투 규칙."""

    name: str
    pattern: re.Pattern[str]
    hint: str


#: 이 저장소에서 굳은 말과 기술 용어 대신 쓴 일상어.
_COINED = _any(
    "정본", "한 벌", "갈래", "짚(?:다|는|은|어|고|을|었)", "물(?:들|든)(?:다|어|면|었)?", "사연",
    "못박", "걷어(?:낸|냈|내)", "쥐(?:고|는|었)", "쥔" + _E,
    "쉼(?:으로|이|을|은|의|과|에|마다|이다)?" + _E, "꼭지", "땜질", "두드리", "맹탕",
    "구멍(?:이|을|난)", "거짓말", "(?:값|수)이 새", "샌다", "새어 나", "굶(?:는|긴|어)",
)
_TRANSLATED = _any(
    "에 대해서?" + _E, "에 대한" + _E, "에 있어서" + _E,
    "(?:을|를) 가(?:진다|지는|졌다)" + _E, "(?:을|를) 갖(?:는|고|는다)" + _E,
    "되어진", "되어졌", "(?:을|를) 통해서?" + _E, "(?:을|를) 수행",
    "하는 것이 가능", "할 수 있게 된다", "에 의해서?" + _E,
)
_SPEAKING = _any(
    "말한다", "말하게", "말하고", "답한다", "답하게", "답하고",
    "안다", "모른다", "믿는다", "속는다", "기억한다",
)
_HISTORY = _any(
    "종전(?:엔|에는|에)", "예전(?:엔|에는)", "실제로 (?:겪|났|봤|있었)", "배경:", "실측[:은 ]",
    "사용자 (?:지적|결정|지시)", r"\(20\d\d-\d\d-\d\d\)", r"20\d\d-\d\d-\d\d에",
)
#: 개조식 종결로 보지 않는 명사의 앞 글자(다음·처음·무음·소음·마음·포함·묶음·모음·걸음·얼음).
_NOUN_HEAD = "다처무소마포묶모걸얼"

RULES: list[Rule] = [
    Rule("지시어",
         re.compile(_H + "(?:그것|이것|저것|그건|이건)(?:은|이|을|는|도|만|으로|과|에)?" + _E),
         "가리키는 대상을 명사로 다시 쓴다"),
    Rule("것이다", re.compile(r"것이다(?:[.)」]|\s*$)"),
         "~하는 것이다 대신 ~한다, ~라는 뜻이다"),
    Rule("부정설명",
         re.compile(_any(r"(?:이|가|은|는) 아니다(?:[.(]|$)",
                         "(?:값|함수|목적|용도|뜻|의미|일)이 아니다")),
         "무엇인지를 긍정문으로 먼저 쓰고, 아닌 것은 꼭 필요할 때만 한 번 쓴다"),
    Rule("번역투", re.compile(_TRANSLATED), "~를 처리한다·~로·~이 있다처럼 한국어 구조로"),
    Rule("의인화",
         re.compile(_any(f"(?:이|가|은|는) (?:{_SPEAKING})",
                         "에(?:게)? (?:묻는다|물어본다|물어 본다|물어서)")),
         "표시한다·돌려준다·조회한다·알 수 없다처럼 실제 동작으로"),
    Rule("화살표문장", re.compile(r"[가-힣][^\n→]{0,40}\s→\s[^\n]*[가-힣]"),
         "화살표 대신 동사로 잇는다(그래서·그러면·~하면 ~한다)"),
    Rule("조어", re.compile(_H + f"(?:{_COINED})"), "표준어로"),
    Rule("강조기호", re.compile(r"«|»|\*\*[^*\s][^*]*\*\*"), "강조 기호를 빼고 인용은 큰따옴표로"),
    Rule("이력", re.compile(_HISTORY), "경위는 패치 문서로, 주석엔 지금 막는 실패와 이유만"),
    Rule("1인칭", re.compile(_H + "(?:우리|저희|내가)(?:가|는|의|도|를|에게)?" + _E),
         "주어는 서버·화면·호출한 사용자처럼"),
    Rule("존댓말·개조식",
         re.compile(_any(r"(?<!아)(?:니다|세요)[.)]?\s*$",
                         rf"(?<=[가-힣])(?<![{_NOUN_HEAD}])(?:함|음|됨|임)\.?\s*$")),
         "평서문 ~한다로"),
    Rule("대시잇기", re.compile(r"[가-힣](?:다|요)[.)」]?\s+—\s+\S"),
         "대시로 문장을 잇지 않고 문장을 나눈다"),
]

#: 규칙마다 걸려야 하는 줄과 걸리면 안 되는 줄. 실행할 때마다 먼저 확인한다.
#: 패턴을 고치다 맞는 문장을 잡거나 잡을 문장을 놓치면 보고 전에 실패한다.
SAMPLES: list[tuple[str, str, str]] = [
    ("지시어", "이 스택은 그것을 하지 못한다.", "이 스택은 요약을 만들지 않는다."),
    ("것이다", "비어 있으면 초안이 없는 것이다.", "비어 있으면 초안이 없다."),
    ("부정설명", "꼭지가 바뀌는 곳을 찾는 값이 아니다.", "발화 사이의 짧은 무음을 잡는 값이다."),
    ("번역투", "각 단계는 prefix를 가진다.", "각 단계에 prefix가 있다."),
    ("의인화", "무엇을 검수하는지는 영상 제목이 말한다.", "영상 제목으로 검수 대상을 표시한다."),
    ("화살표문장", "포맷이 취약 → 컬럼으로 대체.", "형식이 깨지기 쉬워 컬럼으로 바꾼다."),
    ("조어", "쉼으로 볼 최소 길이(초).", "무음 구간으로 판정할 최소 길이(초)."),
    ("강조기호", "그 결과 **화면이 잘못 표시했다**.", "그 결과 화면이 잘못 표시했다."),
    ("이력", "종전엔 예외 하나로 실패가 확정됐다.", "일시 오류는 pending으로 되돌린다."),
    ("1인칭", "우리가 분석한 파일과 같은지 확인한다.", "분석한 파일과 같은지 확인한다."),
    ("존댓말·개조식", "파일을 읽지 못함", "대상이 아니다."),
    ("대시잇기", "되돌리지 않는다 — 표식은 지웠다.", "되돌리지 않는다. 표식은 지웠다."),
]


def self_test() -> list[str]:
    """`SAMPLES`로 규칙 패턴을 확인하고 어긋난 것을 돌려준다."""
    by_name = {r.name: r for r in RULES}
    errors = []
    for name, bad, good in SAMPLES:
        rule = by_name[name]
        if not rule.pattern.search(bad):
            errors.append(f"{name}: 잡아야 할 문장을 놓쳤다: {bad}")
        if rule.pattern.search(good):
            errors.append(f"{name}: 맞는 문장을 잡았다: {good}")
    missing = set(by_name) - {n for n, _, _ in SAMPLES}
    errors.extend(f"{n}: 표본이 없다" for n in sorted(missing))
    return errors


#: 이 줄 수를 넘는 블록 주석. 넘으면 설계 문서로 옮길지 본다.
#: 줄 단위가 아니라 주석 하나에 적용한다. docstring은 Args 등이 있어 세지 않는다.
LONG_BLOCK_LINES = 6
LONG_RULE = "긴블록"


# ── 주석 추출 ──────────────────────────────────────────────────────────────


@dataclass
class Comment:
    """주석 하나. `#` 연속 줄, docstring, `/* */`, `//` 연속 줄 단위."""

    path: str
    start: int
    end: int
    kind: str
    lines: list[str]


_PY_PREFIX = re.compile(r"^\s*#[:!]?\s?")


def py_comments(path: str, src: str) -> list[Comment]:
    """파이썬 파일의 `#` 주석(연속 줄은 하나로)과 docstring을 뽑는다."""
    out: list[Comment] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        tree = ast.parse(src)
    except (SyntaxError, tokenize.TokenError):
        return out
    src_lines = src.splitlines()
    cur: Comment | None = None
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        line_no, col = tok.start
        text = tok.string
        if DIRECTIVE.search(text):
            cur = None
            continue
        full_line = src_lines[line_no - 1][:col].strip() == ""
        body = _PY_PREFIX.sub("", text)
        if full_line and cur is not None and cur.kind == "#" and cur.end == line_no - 1:
            cur.lines.append(body)
            cur.end = line_no
            continue
        cur = Comment(path, line_no, line_no, "#" if full_line else "#inline", [body])
        out.append(cur)
        if not full_line:
            cur = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out.append(Comment(path, node.lineno, node.end_lineno or node.lineno, "docstring",
                               node.value.value.splitlines()))
    return sorted(out, key=lambda c: c.start)


_TS_LINE = re.compile(r"^\s*//+\s?")
_TS_BLOCK_EDGE = re.compile(r"^\s*/\*+!?\s?|\s*\*+/\s*$")
_TS_STAR = re.compile(r"^\s*\*(?!/)\s?")


def ts_path() -> str:
    """TypeScript 모듈 경로. 작업 트리에 없으면 `git worktree` 원본 체크아웃의 것을 쓴다."""
    candidates = [ROOT / "apps/web/node_modules/typescript"]
    res = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         cwd=ROOT, capture_output=True, text=True)
    if res.returncode == 0:
        candidates.append(Path(res.stdout.strip()).parent / "apps/web/node_modules/typescript")
    for c in candidates:
        if (c / "package.json").exists():
            return str(c)
    raise RuntimeError("typescript 모듈을 찾지 못했다. apps/web에서 의존성을 설치한다.")


def ts_comments(paths: list[str]) -> list[Comment]:
    """TS·JS 파일의 주석을 `list-comments.mjs`로 뽑아 연속한 `//` 줄을 하나로 합친다."""
    if not paths:
        return []
    res = subprocess.run(
        ["node", str(ROOT / "scripts/ci/list-comments.mjs")], cwd=ROOT,
        input=json.dumps(paths), capture_output=True, text=True,
        env={**os.environ, "TS_PATH": ts_path()},
    )
    if res.returncode != 0:
        raise RuntimeError(f"list-comments.mjs 실패: {res.stderr.strip()[:500]}")
    out: list[Comment] = []
    for path, items in json.loads(res.stdout).items():
        cur: Comment | None = None
        for start, end, raw in items:
            if DIRECTIVE.search(raw):
                cur = None
                continue
            if raw.startswith("//"):
                body = _TS_LINE.sub("", raw)
                if cur is not None and cur.kind == "//" and cur.end == start - 1:
                    cur.lines.append(body)
                    cur.end = start
                    continue
                cur = Comment(path, start, end, "//", [body])
                out.append(cur)
                continue
            cur = None
            lines = [_TS_STAR.sub("", _TS_BLOCK_EDGE.sub("", ln)) for ln in raw.splitlines()]
            kind = "/**" if raw.startswith("/**") else "/*"
            out.append(Comment(path, start, end, kind, lines))
    return sorted(out, key=lambda c: (c.path, c.start))


def tracked_files() -> list[str]:
    """검사 대상 코드 파일. git이 추적하는 파일 중 생성물을 뺀다."""
    res = subprocess.run(["git", "ls-files", "--", *[f"*{e}" for e in PY_EXT + TS_EXT]],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [p for p in res.stdout.splitlines() if p not in GENERATED]


def all_comments(paths: list[str]) -> list[Comment]:
    """경로 목록의 주석을 모두 뽑는다."""
    out: list[Comment] = []
    for p in paths:
        if p.endswith(PY_EXT):
            out.extend(py_comments(p, (ROOT / p).read_text(encoding="utf-8")))
    out.extend(ts_comments([p for p in paths if p.endswith(TS_EXT)]))
    return out


# ── 판정 ──────────────────────────────────────────────────────────────────


@dataclass
class Hit:
    """규칙에 걸린 곳 하나."""

    rule: str
    path: str
    line: int
    text: str


_CODE_SPAN = re.compile(r"`[^`\n]*`")
_FENCE = re.compile(r"^\s*(?:```|>>>|\.\.\.|\$ |from \S+ import |import \S|[✗✓] )")


def check(comment: Comment) -> list[Hit]:
    """주석 하나에 규칙을 적용한다. 코드 스팬(백틱)과 예시 코드 줄은 보지 않는다."""
    hits: list[Hit] = []
    fenced = False
    for i, raw in enumerate(comment.lines):
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or _FENCE.match(raw):
            continue
        text = _CODE_SPAN.sub("`…`", raw)
        if not re.search(r"[가-힣]", text):
            continue
        for rule in RULES:
            if rule.pattern.search(text):
                hits.append(Hit(rule.name, comment.path, comment.start + i, raw.strip()))
    if comment.kind in ("#", "//", "/*") and len(comment.lines) > LONG_BLOCK_LINES:
        hits.append(Hit(LONG_RULE, comment.path, comment.start,
                        f"{len(comment.lines)}줄: {comment.lines[0].strip()}"))
    return hits


def main(argv: list[str] | None = None) -> int:
    """규칙별·영역별 건수를 찍는다. `--strict`가 아니면 항상 0을 돌려준다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="검사할 파일. 없으면 저장소 전체")
    ap.add_argument("--list", action="store_true", help="걸린 곳을 줄까지 찍는다")
    ap.add_argument("--rule", action="append", help="이 규칙만 본다(여러 번 줄 수 있다)")
    ap.add_argument("--area", action="append", help="이 영역만 본다(여러 번 줄 수 있다)")
    ap.add_argument("--strict", action="store_true", help="1건이라도 있으면 종료 코드 1")
    args = ap.parse_args(argv)

    errors = self_test()
    if errors:
        print("규칙 표본 확인 실패:", *errors, sep="\n  ", file=sys.stderr)
        return 2
    names = [r.name for r in RULES] + [LONG_RULE]
    for r in args.rule or []:
        if r not in names:
            print(f"알 수 없는 규칙: {r} (가능: {', '.join(names)})", file=sys.stderr)
            return 2
    paths = args.paths or tracked_files()
    if args.area:
        paths = [p for p in paths if area_of(p) in args.area]
    try:
        comments = all_comments(paths)
    except RuntimeError as e:
        print(f"실행 실패: {e}", file=sys.stderr)
        return 2
    hits = [h for c in comments for h in check(c)]
    if args.rule:
        hits = [h for h in hits if h.rule in args.rule]

    by_area: dict[str, Counter[str]] = defaultdict(Counter)
    comment_count: Counter[str] = Counter(area_of(c.path) for c in comments)
    for h in hits:
        by_area[area_of(h.path)][h.rule] += 1
    shown = [n for n in names if not args.rule or n in args.rule]
    order = [a for a, _ in AREAS] + ["etc"]
    width = max(len(a) for a in order)
    print(f"{'영역':<{width}}  주석  " + "  ".join(shown) + "  합계")
    total: Counter[str] = Counter()
    for a in order:
        if not comment_count[a]:
            continue
        row = by_area[a]
        total.update(row)
        cells = "  ".join(f"{row[n]:>{len(n)}}" for n in shown)
        print(f"{a:<{width}}  {comment_count[a]:>4}  {cells}  {sum(row.values()):>4}")
    cells = "  ".join(f"{total[n]:>{len(n)}}" for n in shown)
    print(f"{'합계':<{width}}  {sum(comment_count.values()):>4}  {cells}  {sum(total.values()):>4}")

    if args.list:
        for h in sorted(hits, key=lambda h: (h.path, h.line, h.rule)):
            print(f"{h.path}:{h.line}  [{h.rule}]  {h.text[:110]}")
    elif hits:
        print("위치는 --list 로 본다.")
    if args.strict:
        return 1 if hits else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
