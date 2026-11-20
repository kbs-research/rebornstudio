"""분석 상태 분류 세 가지의 기준 정의.

통계, 운영 지표, 영상 상세, 보관 정리가 모두 이 세 분류로 상태를 거른다.
튜플을 곳곳에 따로 적으면 상태가 하나 늘 때 한 곳을 빠뜨리기 쉽다.
빠뜨린 곳의 화면만 다른 수를 표시한다.

`partial`(클립 일부만 성공)은 끝났지만 완전하지 않은 상태다.
- `ACTIVE`에 속하지 않는다. 더 기다려도 바뀌지 않는다.
  운영 지표가 이 상태를 실행 중으로 보면 그 영상이 계속 분석 중으로 남는다.
- `PRODUCED`에 속한다. 일부 클립은 실제로 나왔다.
  보관 정리가 이 상태를 빼면 이미 만든 결과를 정리하지 못한다.

`partial`을 만드는 어댑터는 아직 없지만 분류는 미리 정해 둔다.
"""
from typing import Final

#: 실행 중인 상태. 기다리면 바뀌므로 정리·판정 대상에서 뺀다.
ACTIVE: Final[tuple[str, ...]] = ("pending", "running")

#: 끝난 상태. 성공 여부와 관계없이 더 바뀌지 않는다.
SETTLED: Final[tuple[str, ...]] = ("done", "partial", "failed")

#: 산출물이 있는 상태. 결과가 전부 또는 일부 나왔다.
#: `partial`을 넣는 이유는 모듈 docstring에 있다.
PRODUCED: Final[tuple[str, ...]] = ("done", "partial")


# ── 단계를 옮기는 한 곳 ────────────────────────────────────────────────────────


def advance(job, stage: str) -> None:
    """어댑터 단계를 옮기고 중립 상태를 함께 정한다.

    상태 전이는 여러 경로에서 일어난다.
    한 경로라도 `status`를 빠뜨리면 그 경로의 잡만 옛 상태로 남는다.

    주의: `job.stage = …`를 직접 쓰지 않는다. 두 필드를 함께 옮기는 곳은 이 함수 하나다.
    """
    from api_gateway.services.analysis.status_map import neutral_status

    job.stage = stage
    job.status = neutral_status(stage)


def set_status(job, status: str) -> None:
    """외부 단계가 없는 어댑터(서버가 직접 실행하는 스택)의 상태를 바꾼다.

    `stage`는 비운다. 중립 상태를 `stage`에 복사하면 화면이 이 어댑터에도 외부 단계가
    있는 것으로 표시한다.

    `advance`는 외부 분석기의 단계 원값만 받는다. `advance`에 `running`을 넣으면
    "모르는 단계" 경고가 남아 실제 이상과 정상 호출을 가릴 수 없다.
    """
    job.status = status
    job.stage = None
