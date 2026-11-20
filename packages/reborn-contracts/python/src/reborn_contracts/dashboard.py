"""대시보드 스키마. 개요와 통계(사람별 실적) 화면이 쓴다.

프로젝트 집계(`stats_service`)와 작업·판정 테이블, `domain_events`를 조합한
읽기 전용 데이터를 담는다.
"""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class LabelerPerfRow(BaseModel):
    """작업자 1행. 작업량과 반려만 담는다.

    도구는 사람에게 품질 등급이나 종합 점수를 매기지 않는다.
    여기 있는 값은 사람이 보고 판단할 수치다.

    `rejection_rate`도 서버가 계산한다. 화면이 다시 계산하면 분모가 달라진다.

    두 비율의 분모는 다르다. `approval_rate`는 해결된 건(승인+반려)으로 나눈다.
    `rejection_rate`는 작업량(제출)으로 나눈다. 그래서 둘을 더해도 100%가 되지 않는다.
    분모가 같으면 두 수가 같은 정보를 반복한다. 또 반려율이 검수 속도에 따라 흔들린다.
    예를 들어 50건 중 2건만 검수됐고 그중 1건이 반려면 반려율이 50%가 된다.
    이 지표는 작업량 대비 반려율이다.
    """
    user_id: UUID
    name: str | None = None
    email: str | None = None
    # 역할. 이 표는 활성 멤버 전원을 싣지만 라벨링 작업만 센다.
    # 그래서 검수자 행은 모두 `—`가 된다.
    # 역할이 없으면 검수만 한 사람의 `0`이 일을 하지 않았다는 뜻으로 읽힌다.
    # 활성 멤버가 아닌데 기간 안에 이 프로젝트에 제출했거나 판정받은 사람(멤버가 아닌 최고관리자,
    # 나간 멤버)도 행으로 싣는다. 그 행은 `None`이다. 그래서 표의 제출·판정 합이 프로젝트 합계
    # (`LabelerPerfSummary`)와 같다.
    role: str | None = None
    submitted: int
    approved: int
    rejected: int
    # 두 비율의 분모가 다르다(위 docstring 참조).
    # 통과율은 해결된 건(승인+반려)으로, 반려율은 작업량(제출)으로 나눈다.
    # 각 분모가 0이면 `None`이다(`0.0`과 구분한다).
    # `0.0`으로 내면 화면마다 `approved+rejected == 0`을 다시 계산해 `—`로 바꿔야 한다.
    approval_rate: float | None = None
    rejection_rate: float | None = None
    # 각 비율을 낸 분모. 화면은 비율 아래 괄호에
    # `(승인/approval_base)`·`(반려/rejection_base)`를 쓴다.
    # `approval_base`는 승인+반려, `rejection_base`는 `max(제출, 승인+반려)`다.
    # 기간 전에 낸 작업이 기간 안에 판정되면 판정 수가 제출 수보다 커서 반려율은 판정 수로 나눈다.
    # 화면이 제출 수로 괄호를 쓰면 `100% (18/0)`처럼 비율과 다른 수가 나오므로 서버가 분모를 준다.
    # 0이면 해당 비율은 `None`이다. 기본값을 두지 않아 응답에 늘 실린다.
    approval_base: int
    rejection_base: int
    last_activity: datetime | None = None

    # ── 부하 ────────────────────────────────────────────────────────────
    # 부하(진행 중)를 실적과 같은 행에 싣는다. 이 행이 단일 출처다.
    # 화면이 `user_id`로 합치면 커서 페이지가 서로 어긋날 때 깨지므로 서버가 한 행으로 모은다.
    # 지금 보유 중인 작업 수. 라벨링과 검수의 보유를 합친다.
    in_flight: int = 0

    # ── 수정률 ──────────────────────────────────────────
    # 고친 글자 수 합 ÷ AI 초안 글자 수 합 × 100. 초안보다 길게 덧붙이면 100을 넘을 수 있다.
    # 첫 제출 중 AI 초안이 있던 것만 잰다(`work.edit_rate`). 재제출은 직전 제출에서 시작한다.
    # 분모(`edit_measured`)와 글자 수의 분자·분모(`edit_distance`·`draft_chars`)를 함께 준다.
    # 잰 제출이 없으면 `edit_rate`는 `None`이다(0%와 구분한다).
    # 초안 보관(0080)을 시작하기 전의 제출은 분모에 넣지 않는다.
    edit_rate: float | None = None
    edit_measured: int = 0
    edit_distance: int = 0
    draft_chars: int = 0


class LabelerPerfSummary(BaseModel):
    """기간 안의 프로젝트 합계. 통계 화면 위의 요약 영역이 쓴다.

    수(제출·승인·반려·글자 수)는 같은 기간 사람별 행(`LabelerPerfRow`)의 합과 같다.
    서버가 같은 행 집합을 더해서 내므로 요약 영역과 사람별 표의 합계가 같다.
    비율과 분모는 합계에 사람별 행과 같은 규칙(`_rate_fields`·`_edit_rate`)을 적용한 값이다.
    그래서 `rejection_base`는 행별 분모의 합이 아니라 `max(제출 합, 승인 합 + 반려 합)`이다.
    """
    submitted: int
    # 기간 안에 한 건 이상 제출한 사람 수.
    submitters: int
    approved: int
    rejected: int
    # 각 분모가 0이면 `None`이다(`0.0`과 구분한다).
    # 단위는 사람별 행과 같다(승인율 0~100, 반려율 0~1).
    approval_rate: float | None = None
    rejection_rate: float | None = None
    # `approval_base`는 기간 안의 판정 수(승인+반려)다. 요약의 판정 수도 이 값을 쓴다.
    approval_base: int
    rejection_base: int
    edit_rate: float | None = None
    edit_measured: int = 0
    edit_distance: int = 0
    draft_chars: int = 0


class WorkflowHealth(BaseModel):
    """워크플로 상태. 상태별 작업 수(큐 깊이)를 담는다."""
    queue_depth: dict[str, int]              # work_items status별 카운트


class PipelineSummary(BaseModel):
    """작업 단계별 대기 건수. 파이프라인 스텝퍼와 사이드바 배지가 이 값을 단일 출처로 쓴다.

    영상(Video)·클립(Clip)·work_item 기준의 수를 한 번에 모은다.
    페이지에 들어가지 않고도 지금 어느 단계에 일이 쌓였는지 보여 준다.
    `WorkflowHealth`는 work_item 기준이라 업로드·MI 단계를 세지 못한다.
    """
    #: 비식별화 미흡 신고로 작업이 멈춘 클립 수(`rights_status=legal_review`).
    #: 관리자가 재개·제외를 정한다.
    deident_reports: int = 0
    awaiting_analysis: int      # MI 분석 요청 가능 (MAIU job 없음)
    analyzing: int              # MI 분석 중 (MAIU job이 done/failed 이전)
    #: 변환 중인 영상 수. 변환·비식별화·재생본 확정이 끝나지 않은 영상이다.
    #: 반입 화면의 `변환 중`과 같은 분류라 대기 수(`awaiting_analysis`)와
    #: 겹치지 않는다. 등록 직후 대기 수가 0일 때 등록한 영상이 어디에 있는지 이 수가 알린다.
    preparing: int = 0
    label_available: int        # 라벨링 대기
    review_submitted: int       # 검수 대기
    #: 반려가 여러 번 쌓여 관리자 확인 대상이 된 미완 작업 수(`needs_admin_check_where`).
    repeated_rejects: int = 0
    #: 완료(승인) 작업 수. 완료 목록·내보내기와 같은 정의(`approved_rows_stmt`)로 센다.
    completed_total: int = 0


class HoursProgress(BaseModel):
    """시간 진척. 값과 그 값의 기준 분모를 함께 싣는다.

    화면이 목표 유무를 보고 기준을 고르면 개요와 통계가 같은 이름으로 다른 수를 보여 준다.
    기준을 고르는 판정은 서버 한 곳(`analytics.stats.hours_progress`)에서만 한다.
    """
    #: 승인된 클립 길이 합 ÷ `scale_hours` × 100. 소수 1자리까지 쓴다.
    pct: float
    #: 잰 기준. `target`(계약 목표) 또는 `intake`(반입 분량). 화면이 이름을 붙인다.
    basis: Literal["target", "intake"]
    #: 분모(시간). 목표가 있으면 `max(반입 분량, 목표)`다.
    #: 그래야 막대의 세 구간이 같은 기준 위에 놓인다.
    scale_hours: float


class DeliverableSummary(BaseModel):
    """납품할 수 있는 작업 수. 사유는 싣지 않는다.

    개요 단계별 대기의 완료 항목이 쓴다.
    납품 불가 사유는 운영 상태라 `CapacityHealth.blocker_reasons`(관리자 전용)에만 있다.
    """
    #: 납품 검사를 통과한 작업 수.
    count: int


class BusinessKpi(BaseModel):
    """사업 KPI."""
    #: 건수 기준 진척. 승인 ÷ 사람 작업(`analytics.stats.progress_pct`가 기준).
    progress_pct: float
    done_count: int
    total_count: int

    # ── 시간 축 ────────────────────────────────────────────────────────────────
    # 계약이 시간 단위라 건수 진척은 고객 보고에 쓸 수 없다. 클립 길이가 제각각이라 둘은 다르다.
    # 분모가 둘이다. `total_hours`(들어와 있는 분량)는 가진 분량 중 처리한 비율의 분모다.
    # `target_hours`(계약 목표)는 계약 대비 진척의 분모다.
    # 하나만 주면 화면이 다른 진척에도 그 값을 쓰게 된다. 그래서 둘 다 주고 화면이 분모를 밝힌다.
    done_hours: float = 0.0
    total_hours: float = 0.0
    target_hours: float | None = None
    # 클립이 없어 시간으로 셀 수 없는 work 수.
    # 0이면 표시하지 않고, 있으면 시간 진척 옆에 `측정 불가 N건`을 붙인다.
    # 이 표시가 없으면 진척이 설명 없이 낮게 나온다(`ProjectStats` 참조).
    hours_unmeasurable_count: int = 0
    #: 시간 진척. 반입 분량이 0이면(클립이 아직 없으면) `None`이다. 그때 화면은 건수로 표시한다.
    #: 개요와 통계가 같은 값을 그린다. 화면이 위 두 분모로 다시 나누지 않는다.
    hours_progress: HoursProgress | None = None
    contract_deadline: date | None = None
    # 단계별 건수는 `WorkflowHealth.queue_depth`가 준다.
    #: 납품 가능 요약. 관리자에게만 싣는다. 검사 상한을 넘으면 `None`이다.
    deliverable: DeliverableSummary | None = None


# ── 처리 능력(capacity) ────────────────────────────────────────────────────────


class StaleBucket(BaseModel):
    """임계 시간을 넘긴 작업 수. 경과 시간의 기준 시각을 함께 준다.

    `3일 넘음 4건`만 쓰면 무엇으로부터 3일인지 알 수 없다.
    제출 시각 기준인지 생성 시각 기준인지에 따라 운영자가 확인할 곳이 다르다.
    """

    key: str
    label: str
    threshold_days: int
    count: int = 0
    # 경과 시간의 기준 시각(`submitted_at`·`created_at` 등).
    clock: str


class CapacityHealth(BaseModel):
    """처리 능력 지표. 임계 초과와 납품 불가 사유를 담는다.

    큐 소진 예상(남은 큐·처리 속도·남은 일수)은 싣지 않는다.

    `blocker_reasons`는 전체를 검증하므로 `deliverable_total`과 같은 상한을 쓴다.
    상한을 넘으면 `None`이다. 세지 않았다는 뜻이다(0건과 구분한다).
    """

    stale: list[StaleBucket] = []

    blocker_reasons: dict[str, int] | None = None
    blocker_scan_limit: int = 0
