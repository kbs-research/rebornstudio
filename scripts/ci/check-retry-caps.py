"""재시도 상한을 둔 모듈이 일시 오류를 구분하는지 세는 게이트.

일시 오류를 영구 실패로 만들지 않는다. 429·5xx·타임아웃은 재시도 대상이다.
폴링은 여러 번 요청하는 일이라 일시 오류를 실패로 세면 오래 걸리는 작업일수록 실패로 끝난다.

`services/transient.py`가 판정 기준이다. `transcode`·`deident`·`publish`·`scene_split`은
그 규칙대로 일시 오류에는 재시도 횟수를 차감하지 않는다.
presigned URL을 ffmpeg에 직접 넘기는 경로는 네트워크 상태가 실패에 섞인다.
ffmpeg 실패를 하나로 묶으면 깨진 파일과 일시 단절이 구분되지 않는다.
그러면 잠깐 연결이 흔들린 영상이 상한에 걸려 영구 실패로 고정된다.

통과 조건: `_MAX_RETRIES` · `MAX_ATTEMPTS` 같은 상한 상수를 둔 모듈은 `transient`를
언급해야 한다. 상한이 있으면 몇 번 실패한 뒤 포기한다. 그래서 무엇을 실패로 셀지 정한
코드가 그 모듈 안에 있어야 한다.

한계: 그 낱말이 있는지만 본다. 주석에 `transient`라고 써 두고 실제로 구분하지 않아도
통과한다. 그 경우는 사람이 리뷰에서 확인한다. 이 게이트가 막는 것은 상한만 두고
일시 오류는 고려하지 않은 새 워커다.
판정이 맞는지는 `services/transient.py`의 단위 테스트가 확인한다.

    python3 scripts/ci/check-retry-caps.py
"""
from __future__ import annotations

import pathlib
import re

ROOTS = ("apps/api-gateway/api_gateway", "workers/worker-io/worker_io",
         "workers/worker-jobs/worker_jobs", "packages/reborn-transcode/src/reborn_transcode")

CAP = re.compile(r"^_?MAX_(?:RETRIES|ATTEMPTS)\s*=", re.M)

#: 상한은 있지만 일시 오류 판정이 필요 없는 모듈. 이유를 함께 적는다. 지금은 없다.
EXPECTED: dict[str, str] = {}


def main() -> int:
    """재시도 상한을 둔 모듈을 훑어 결과를 찍는다.

    일시 오류를 구분하지 않는 모듈이나 낡은 제외 항목이 있으면 1을 돌려준다.
    """
    capped, blind = [], []
    for root in ROOTS:
        for p in sorted(pathlib.Path(root).rglob("*.py")):
            src = p.read_text(encoding="utf-8")
            if not CAP.search(src):
                continue
            name = str(p)
            capped.append(name)
            if "transient" not in src.lower() and name not in EXPECTED:
                blind.append(name)

    stale = [k for k in EXPECTED if k not in capped]
    print(f"재시도 상한을 둔 모듈 {len(capped)}종 · 제외 {len(EXPECTED)}종")
    if stale:
        print("\n제외 목록에 있는데 상한이 없는 모듈 — EXPECTED에서 빼주세요:")
        for k in stale:
            print(f"  {k}")
    if blind:
        print(f"\n일시 오류를 안 가리는 모듈 {len(blind)}종:")
        for name in blind:
            print(f"  {name}")
        print("\n  → 일시(429·5xx·타임아웃·presign 만료)는 상한을 깎지 않게 하거나,"
              " 그럴 필요가 없으면 이 스크립트의 EXPECTED에 이유와 함께 적어주세요."
              "\n     판정 기준은 `api_gateway/services/transient.py`에 있습니다.")
        return 1
    print("\n상한을 둔 모듈은 전부 일시 오류를 가린다")
    return 0 if not stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
