"""서버가 남기는 이벤트 종류가 화면 사전에 있는지 세는 게이트.

감사 로그·최근 활동은 `event_type`("domain.action")을 한국어로 바꿔 그린다
(`apps/web/src/features/audit/lib/event_label.ts`의 `EVENTS` 표).
표에 없으면 원문이 그대로 화면에 나간다.
`humanizeEvent`는 표에 도메인만 있으면 `작업 restored_by_qa`처럼 일부만 바꾼다.
도메인도 없으면 `work.restored_by_qa`를 그대로 낸다.
눈으로 훑으면 드물게 나는 이벤트가 그대로 남는다. 그래서 기계가 센다.
`tsc`와 테스트는 이 문제를 잡지 않는다. 표에 없는 키는 문법 오류가 아니라 빠진
항목이다.

서버에서 표 방향으로만 센다. 반대 방향(표에만 있는 종류)은 셀 수 없다.
표에는 서버가 더는 내지 않지만 DB에 남은 옛 종류(`work.restored_by_qa` 등)와
동적 이벤트(아래)가 내는 종류가 있다. 그 방향을 게이트로 만들면 살아 있는 항목이
쓰이지 않는 항목으로 나온다.

동적 이벤트는 `f"rights.bulk_{payload.decision}"`처럼 런타임에 값이 정해지는 이벤트다.
동적 이벤트는 판정하지 않는다. 도메인도 확인하지 않고 판정하지 못한 개수만 출력에 남긴다.
`check-error-codes`가 `fstring`·`expr`을 다루는 방식과 같다.

통과 조건: 이벤트 종류가 `EVENTS` 표의 키로 그대로 있어야 한다.
`humanizeEvent`와 같은 규칙이다.
없으면 화면에 원문 조각이 나가므로 실패다. 화면에 닿지 않는 이벤트는 아래
EXPECTED에 이유와 함께 적는다. 그냥 빼지 않는다(`check-error-codes`와 같은 방식).

    python3 scripts/ci/check-event-labels.py
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DICT = ROOT / "apps/web/src/features/audit/lib/event_label.ts"
SOURCES = [ROOT / "apps/api-gateway", ROOT / "workers"]

#: 화면 사전이 필요 없는 이벤트. 이유를 함께 적는다.
EXPECTED: dict[str, str] = {}


def _emitting_sources() -> list[pathlib.Path]:
    """이벤트를 찾을 소스 루트를 돌려준다. 테스트 파일은 부르는 쪽이 `_is_test`로 뺀다.

    `SOURCES`는 `apps/api-gateway` 전체라서 테스트 파일을 빼지 않으면 테스트가 직접 만든
    `DomainEvent`까지 센다. 테스트 픽스처는 화면에 닿지 않는다. 그런데도 세면 게이트가 늘 실패해
    실제 누락이 묻힌다. 예를 들어 `tests/kernel/test_event_actor_name.py`는 `with_actor_names`를
    시험하려고 저장하지 않는 `x.test` 이벤트를 만든다.

    테스트 픽스처는 `EXPECTED`에 넣지 않는다. `EXPECTED`는 화면에 닿지 않는 서버 이벤트를 적는
    곳이고, 픽스처를 넣으면 새 테스트가 가짜 이벤트를 쓸 때마다 한 줄씩 늘어난다.

    테스트에만 있는 리터럴은 `x.test`·`review.approve` 둘이다. `review.approve`는 제품 코드가
    `f"review.{payload.decision}"`로 내는 이벤트라서 테스트 파일을 빼도 잃는 커버리지가 없다.
    """
    return SOURCES


def _is_test(py: pathlib.Path) -> bool:
    """테스트 파일이면 True. `tests/` 아래에 있거나 이름이 `test_`로 시작하는 파일이다."""
    rel = py.relative_to(ROOT).as_posix()
    return "/tests/" in f"/{rel}" or py.name.startswith("test_")


def literals() -> dict[str, str]:
    """서버가 내는 고정 문자열 이벤트 종류와 처음 나온 위치(`파일:줄`)를 돌려준다."""
    found: dict[str, str] = {}
    pat = re.compile(r'event_type\s*=\s*"([a-z0-9_.]+)"')
    for root in _emitting_sources():
        for py in root.rglob("*.py"):
            if _is_test(py):
                continue
            text = py.read_text(encoding="utf-8")
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.setdefault(m.group(1), f"{py.relative_to(ROOT)}:{line}")
    return found


def dynamic() -> list[str]:
    """런타임에 정해지는 event_type(f-string) 목록을 돌려준다. 판정하지 않고 개수만 센다."""
    pat = re.compile(r'event_type\s*=\s*f"([^"]+)"')
    out: list[str] = []
    for root in _emitting_sources():
        for py in root.rglob("*.py"):
            if _is_test(py):
                continue
            out += pat.findall(py.read_text(encoding="utf-8"))
    return out


def event_keys(src: str) -> set[str]:
    """`EVENTS` 표의 키(이벤트 종류)를 뽑는다. 키는 `"domain.action": {` 꼴이다."""
    body = src.split("export const EVENTS: Record<string, EventDef> = {", 1)[1]
    depth, end = 1, 0
    for i, ch in enumerate(body):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            end = i
            break
    return set(re.findall(r'^\s*"([a-z0-9_.]+)"\s*:\s*\{', body[:end], re.M))


def main() -> int:
    """표에 없는 이벤트와 낡은 제외 항목을 찍는다. 하나라도 있으면 1을 돌려준다."""
    src = DICT.read_text(encoding="utf-8")
    known = event_keys(src)

    found = literals()
    dyn = dynamic()

    missing = sorted(t for t in found if t not in EXPECTED and t not in known)
    stale = sorted(t for t in EXPECTED if t not in found)

    print(
        f"서버 이벤트 리터럴 {len(found)}종 · 표의 종류 {len(known)}"
        f" · 못 세는 동적 자리 {len(dyn)}곳 · 제외 {len(EXPECTED)}종"
    )
    if stale:
        print("\n제외 목록에 있는데 서버엔 없는 이벤트 — EXPECTED에서 빼주세요:")
        for t in stale:
            print(f"  {t}")
    if missing:
        print(f"\n사전에 없는 이벤트 {len(missing)}종 — 화면에 원문이 그대로 나갑니다:")
        for t in missing:
            print(f"  {t:<38} {found[t]}")
        print("\n  → event_label.ts의 EVENTS 표에 화면이 쓰는 이름과 심각도를 넣거나,")
        print("     화면에 안 닿으면 이 스크립트의 EXPECTED에 이유와 함께 적어주세요.")
        return 1
    print("\n원문이 새는 이벤트 없음")
    return 0 if not stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
