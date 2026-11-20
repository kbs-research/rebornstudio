"""라벨링 작업 배정 서비스.

다음 작업 배정(한 사람에 한 건), 제출, 호출한 사용자의 보유 작업 조회를 맡는다.
작업 생성·닫기와 가져갈 수 있는 작업의 조건도 이 패키지가 정한다.

이 모듈은 하위 모듈의 이름을 다시 내보내는 파사드다. 호출하는 모듈은 다음처럼 가져온다.

    from api_gateway.services.work import service as work_service
    from api_gateway.services.work.service import _to_dto

하위 모듈과 의존 방향은 같은 폴더의 `README.md`에 있다.
"""
from api_gateway.services.work.service.classify import (
    _CLOSABLE,
    HELD_ORDER,
    _claimable_where,
    _held_by,
    _human_workable,
    held_order,
    holder_left,
    needs_admin_check_where,
    reject_if_not_claimable,
)
from api_gateway.services.work.service.commands import (
    _ANNOTATOR_ROLES,
    _load_my_claim,
    _load_working_claim,
    _next_dto,
    _require_annotator,
    _require_work_viewer,
    _work_view_scope,
    close_items,
    count_closable,
    ensure_for_clip,
    ensure_for_video,
    lock_item,
    next_work,
    release_holds,
    released_payload,
    report_deident_miss,
    retire_video_ticket,
    save_draft,
    submit,
)
from api_gateway.services.work.service.dto import _to_dto
from api_gateway.services.work.service.history import (
    _HISTORY_START_EVENTS,
    _history_query,
    _history_rows,
    list_my_history,
    my_history_detail,
)
from api_gateway.services.work.service.queries import (
    claimable_summary,
    count_claimable,
    get_one,
    latest_qc,
    list_labels,
    list_mine,
)

__all__ = [
    # classify
    "HELD_ORDER",
    "_CLOSABLE",
    "_claimable_where",
    "_held_by",
    "_human_workable",
    "held_order",
    "holder_left",
    "needs_admin_check_where",
    "reject_if_not_claimable",
    # dto
    "_to_dto",
    # commands
    "_ANNOTATOR_ROLES",
    "_load_my_claim",
    "_load_working_claim",
    "_require_annotator",
    "_require_work_viewer",
    "_next_dto",
    "_work_view_scope",
    "close_items",
    "count_closable",
    "ensure_for_clip",
    "ensure_for_video",
    "lock_item",
    "next_work",
    "release_holds",
    "released_payload",
    "report_deident_miss",
    "retire_video_ticket",
    "save_draft",
    "submit",
    # queries
    "claimable_summary",
    "count_claimable",
    "get_one",
    "latest_qc",
    "list_labels",
    "list_mine",
    # history
    "_HISTORY_START_EVENTS",
    "_history_query",
    "_history_rows",
    "list_my_history",
    "my_history_detail",
]
