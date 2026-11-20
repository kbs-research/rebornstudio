"""대시보드 지표 집계. `stats`와 작업·판정 테이블, `domain_events`를 조합한다.

읽기 전용이다. 볼 수 있는 사람은 함수마다 다르다.
- 관리자(project_admin/super_admin): labeler_performance, labeler_summary,
  workflow_health, pipeline_summary, capacity_health.
- 관리자와 검수자: business_kpi.

지표는 묶음별 하위 모듈에 있다. 이 파일은 그 이름을 모두 다시 내보낸다.
호출부는 하위 모듈 대신 패키지 경로로 import한다.

    from api_gateway.services.analytics import dashboard as dashboard_service
    from api_gateway.services.analytics.dashboard import _approval_rate

하위 모듈의 역할과 의존 방향은 README에 있다.
"""
from api_gateway.services.analytics.dashboard.capacity import (
    _STALE,
    _blocker_reasons,
    _deliverable_summary,
    _scan_deliverable,
    _stale_buckets,
    capacity_health,
)
from api_gateway.services.analytics.dashboard.classify import (
    _approval_rate,
    _current_loads,
    _edit_rate,
    _is_reviewer_revision,
    _rate_fields,
    _rejection_rate,
)
from api_gateway.services.analytics.dashboard.queries import (
    _labeler_counts,
    business_kpi,
    labeler_performance,
    labeler_summary,
    pipeline_summary,
    workflow_health,
)

__all__ = [
    "_STALE",
    "_approval_rate",
    "_blocker_reasons",
    "_current_loads",
    "_deliverable_summary",
    "_edit_rate",
    "_is_reviewer_revision",
    "_labeler_counts",
    "_rate_fields",
    "_rejection_rate",
    "_scan_deliverable",
    "_stale_buckets",
    "business_kpi",
    "capacity_health",
    "labeler_performance",
    "labeler_summary",
    "pipeline_summary",
    "workflow_health",
]
