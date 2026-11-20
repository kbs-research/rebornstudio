"""비식별화 결과가 없는 이유의 판정. 영상 여정과 품질 검사가 함께 쓴다.

두 화면이 같은 영상을 같은 사유로 표시하도록 판정을 한 곳에 둔다.
비식별화를 쓰지 않는 프로젝트에서는 결과가 앞으로도 생기지 않는다.
그런 영상에 "아직 실행되지 않았다"고 표시하면 틀리므로 결과가 없는 이유를 다섯 경우로 나눈다.

판정은 매체 쪽 사실이지만 `media` 밖의 평면 모듈에 두고 앱 모듈을 import하지 않는다.
`media.video`는 `work.service`를, `work.service`는 품질 검사 러너를, 러너는 이 판정을 부른다.
`media` 안에 두면 이 호출이 순환 import가 되어 모듈 로드 중에 `partially initialized module`
오류가 난다. 러너가 `media`를 import하면 `work`가 `media`에 의존하게 되어 도메인 독립
계약(`lint-imports`)도 깨진다. 이 규칙은 `services/README.md`의 범도메인 표에 있다.
"""

from __future__ import annotations

from typing import Literal

#: 비식별화를 쓰지 않는 프로젝트의 영상에 표시하는 문장. 영상 여정과 품질 검사가 함께 쓴다.
#: 설정 스위치의 상태 이름(`사용 중`·`사용 안 함`)과 같은 말을 쓴다.
DEIDENT_OFF = "이 프로젝트는 비식별화를 사용하지 않습니다."
#: 지금은 비식별화가 켜져 있지만 켜기 전에 처리된 영상에 표시하는 문장.
#: `skipped` 하나가 두 경우를 함께 뜻하므로 따로 구분한다.
#: 가린 영상의 화면 용어는 `비식별화본`이다. `마스킹`은 쓰지 않는다.
DEIDENT_BEFORE_ON = "비식별화를 켜기 전에 처리된 영상이라 비식별화본이 없습니다."

DeidentGap = Literal["off", "before_on", "waiting", "running", "failed"]

#: 비식별화 단계가 아직 열리지 않았다는 뜻의 변환 상태. 변환이 진행 중이거나 실패하면
#: 파이프라인이 비식별화 앞에서 멈춰 있다(`pipeline.open_next`).
_UPSTREAM_OPEN = ("pending", "queued", "running", "failed")


def reached_deident(
    *, probe_status: str | None, transcode_status: str | None,
    publish_status: str | None, downstream: bool,
) -> bool:
    """파이프라인이 비식별화 단계를 열지 이미 정했으면 True를 돌려준다.

    `deident_status`의 열 기본값이 `skipped`라서 규격 검증 전이나 변환 중인 영상도 `skipped`다.
    파이프라인은 검증 통과 뒤 변환이 끝나야 비식별화를 열지 정한다(`pipeline.open_next`).
    그 전의 `skipped`는 정해지지 않은 상태라서 "켜기 전에 처리됨"으로 읽으면 틀린다.
    분류 SQL(`classify._unmasked_expr`)은 같은 경우를 분기 순서로 거른다.
    검증 전·변환 중 분기가 가리지 않은 영상 분기보다 앞에 있다.

    Args:
        probe_status: `Video.probe_status`.
        transcode_status: `Video.transcode_status`.
        publish_status: `Video.publish_status`. 재생본 단계는 비식별화 단계가 정해진 뒤에 열린다.
        downstream: 분석 잡이나 클립이 있으면 True. 규격 검증 기록이 없는 영상도 분석까지 간
            영상은 준비를 마친 영상이다.

    Returns:
        비식별화 단계를 지났으면 True. 앞 단계를 기다리는 중이면 False.
    """
    if transcode_status in _UPSTREAM_OPEN:
        return False
    if downstream or transcode_status == "done":
        return True
    if publish_status not in (None, "skipped"):
        return True
    return probe_status == "passed"


def deident_gap(status: str | None, deident_on: bool, *, reached: bool) -> DeidentGap | None:
    """영상에 비식별화 결과가 없는 이유를 돌려준다. 결과가 있으면(`done`) `None`을 돌려준다.

    `skipped` 하나가 "쓰지 않음", "켜기 전에 처리됨", "앞 단계를 기다림"을 함께 뜻하므로
    지금 설정과 파이프라인 위치를 같이 본다.
    설정을 보지 않으면 나중에 비식별화를 켠 프로젝트에서 "쓰지 않습니다"가 잘못 표시된다.
    위치를 보지 않으면 새로 올린 영상이 검증을 마치기 전까지 "켜기 전에 처리됨"으로 표시된다.

    Args:
        status: `Video.deident_status`.
        deident_on: 이 프로젝트가 지금 비식별화를 쓰면 True(`Project.deident_enabled`).
        reached: 파이프라인이 비식별화 단계를 지났으면 True(`reached_deident`).

    Returns:
        `off`·`before_on`·`waiting`·`running`·`failed` 중 하나. 결과가 있으면 `None`.
    """
    if status == "done":
        return None
    if status == "skipped":
        if not deident_on:
            return "off"
        return "before_on" if reached else "waiting"
    if status in ("pending", "queued", "running"):
        return "running"
    return "failed"
