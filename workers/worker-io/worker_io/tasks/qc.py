"""캡션 품질검수를 요청 밖에서 실행하는 태스크. 서비스 함수를 부르는 얇은 셸이다.

제출·승인은 검사를 기다리지 않고 커밋된다. `dispatch_caption_qc`가 주기마다 검사를 받지 못한
건을 찾아 `caption_qc_run`으로 보낸다. 대상 판정과 중복 방지는 `caption_qc.backlog`·`runner`가 한다.
api는 브로커에 보내지 않는다. 투입이 주기 태스크 하나라서 api 재시작·메시지 유실·워커 중단이
모두 다음 주기에 같은 경로로 회수된다.
"""
from reborn_contracts.queues import CAPTION_QC_RUN, DISPATCH_CAPTION_QC, QUEUE_QC

from ..celery_app import app
from ._session import _run_with_fresh_session


@app.task(name=DISPATCH_CAPTION_QC)
def dispatch_caption_qc() -> int:
    """5초마다 호출된다. 검사를 받지 못한 제출·승인을 q.qc로 보낸다.

    이미 보낸 건은 `backlog.LEASE_SECONDS` 동안 다시 보내지 않는다. 그 시간이 지나도 기록이
    없으면 다시 보낸다. 실행 중 워커가 중단된 건도 이 경로로 회수된다.

    Returns:
        이번 주기에 보낸 건수.
    """
    from api_gateway.services.caption_qc import backlog

    owed = _run_with_fresh_session(backlog.find_owed)
    sent = backlog.lease(owed)
    for wid, trigger in sent:
        # 같은 앱에 등록된 태스크라 함수 참조로 보낸다. 테스트의 eager 실행도 이 경로를 탄다.
        caption_qc_run.apply_async(
            kwargs={"work_item_id": str(wid), "trigger": trigger}, queue=QUEUE_QC,
        )
    return len(sent)


@app.task(
    name=CAPTION_QC_RUN, acks_late=True, reject_on_worker_lost=True, ignore_result=True,
)
def caption_qc_run(work_item_id: str, trigger: str) -> str:
    """작업 한 건을 검사하고 기록한다. 실행 직전 확인과 중복 기록 방지는 `runner.run_owed`가 한다.

    워커가 실행 중에 중단되면 메시지를 다시 받는다(`acks_late`). 두 번 실행돼도 기록은 하나다.

    Returns:
        `ran` · `skipped` · `duplicate` · `retry` · `failed` 중 하나.
    """
    from uuid import UUID

    from api_gateway.services.caption_qc.runner import run_owed

    return _run_with_fresh_session(lambda s: run_owed(s, UUID(work_item_id), trigger))
