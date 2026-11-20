"""`video.extra`·`clip.extra`를 파이썬에서 합쳐 다시 쓰는 코드를 세는 게이트.

배치 워커가 아래처럼 쓰면 ①과 ③ 사이에 다른 워커가 같은 행에 쓴 key가 사라진다
(lost update).

    extra = {**(video.extra or {})}    # ① 읽는다
    ...ffmpeg / ffprobe / S3 / MAIU... # ② 외부 호출 — 수 초~수십 초
    video.extra = extra                # ③ 통째로 되쓴다

그래서 병합은 `merge_extra`로 한다. DB가 `metadata || patch`로 합친다.
이 검사는 그 규칙을 강제한다.
통째로 되쓰면 다른 워커가 쓴 `probe` 메타가 지워지고, `needs_transcode`가 근거를 잃어
변환 사유가 빈다.

통과 조건: `.extra`에 값을 대입하는 곳이 하나도 없어야 한다. 병합은 DB가 한다.

    await merge_extra(db, video_id, {"key": value}, remove=("낡은키",))

읽기(`video.extra.get(...)`)와 질의(`Video.extra["k"].astext`)는 세지 않는다.
다시 쓰는 대입만 문제가 된다.

한계: `.extra`만 본다. 다른 JSONB(`payload`·`settings`·`config`)는 보지 않는다.
그쪽은 통째로 바꾸는 것이 정당한 경우가 있다(사용자가 설정을 통째로 저장한다).
`merge_extra` 같은 표준 함수도 없어서 세면 오탐이 된다. `job.payload`는
reconciler와 웹훅 모두 `with_for_update` 행 잠금 아래에서 써서 안전하다. 여러
writer가 쓰는 새 JSONB 컬럼이 생기면 그때 표준 병합 함수를 먼저 만들고 여기 추가한다.

    python3 scripts/ci/check-jsonb-merge.py
"""
from __future__ import annotations

import ast
import pathlib

ROOTS = ("apps/api-gateway/api_gateway", "workers/worker-io/worker_io",
         "workers/worker-jobs/worker_jobs")

#: 통째 대입이 옳은 위치. 이유를 함께 적는다. 지금은 없다.
EXPECTED: dict[str, str] = {}


def offenders() -> list[tuple[str, int, str]]:
    """`X.extra = …` 대입을 모은다. models는 컬럼 선언이라 제외한다."""
    found = []
    for root in ROOTS:
        for p in pathlib.Path(root).rglob("*.py"):
            if "/models/" in str(p):
                continue
            src = p.read_text(encoding="utf-8")
            if ".extra" not in src:
                continue
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Assign):
                    continue
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "extra":
                        owner = ast.get_source_segment(src, t.value) or "?"
                        found.append((str(p), node.lineno, f"{owner}.extra"))
    return sorted(found)


def main() -> int:
    """다시 쓰는 대입과 낡은 제외 항목을 찍는다. 하나라도 있으면 1을 돌려준다."""
    bad = [f for f in offenders() if f"{f[0]}:{f[1]}" not in EXPECTED]
    stale = [k for k in EXPECTED if k not in {f"{f[0]}:{f[1]}" for f in offenders()}]
    if stale:
        print("제외 목록에 있는데 그 자리에 대입이 없다 — EXPECTED에서 빼주세요:")
        for k in stale:
            print(f"  {k}")
    if bad:
        print(f"파이썬에서 합쳐 되쓰는 `.extra` 대입 {len(bad)}건:")
        for path, line, expr in bad:
            print(f"  {path}:{line}  {expr} = …")
        print("\n  → 셋 중 하나입니다.")
        print("     ① `merge_extra(db, id, patch, remove=(...))`로 바꾼다.")
        # 이미 merge_extra를 쓰는 코드도 이 검사에 걸릴 수 있다.
        # `if x.extra is None: x.extra = {}`로 NULL을 막고 바로 아래에서 병합하는 코드도 걸린다.
        # 컬럼이 `NOT NULL DEFAULT '{}'`라서 그 분기는 실행되지 않는다.
        # ①만 안내하면 그 코드를 쓴 사람은 이미 병합을 쓰고 있다고 읽는다.
        print("     ② 이미 병합을 쓰는데 걸렸다면 **NULL 초기화가 죽은 줄**일 수 있습니다 —")
        print("        `extra`는 `NOT NULL DEFAULT '{}'`라 DB에서 읽은 행은 언제나 dict입니다."
              " 그 줄을 지우세요.")
        print("     ③ 통째 교체가 옳으면 이 스크립트의 EXPECTED에 이유와 함께 적어주세요.")
        return 1
    print(f"`.extra` 통째 대입 없음 (제외 {len(EXPECTED)}건)")
    return 0 if not stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
