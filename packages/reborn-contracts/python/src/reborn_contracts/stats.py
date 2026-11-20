"""프로젝트 통계. 진행 상황을 요약한다."""
from pydantic import BaseModel, Field


class ProjectStats(BaseModel):
    """프로젝트 진행 상황 요약."""
    work_by_status: dict[str, int] = Field(default_factory=dict)
    members_by_role: dict[str, int] = Field(default_factory=dict)
    total_videos: int = 0
    total_clips: int = 0
    total_work_items: int = 0
    total_members: int = 0

    # ── 시간 축 ────────────────────────────────────────────────────────────────
    # 계약 단위가 시간이다(`projects.target_hours`).
    # 클립 길이가 제각각이라 건수 진척과 시간 진척은 다르다.
    # 주의: 건수의 분모로 쓰지 않는다.
    # 클립이 없는 work(영상 단위 티켓)는 시간이 0으로 들어가므로 두 dict의 합은 비례하지 않는다.
    work_hours_by_status: dict[str, float] = Field(default_factory=dict)
    # 이 프로젝트에 들어와 있는 분량. `target_hours`(납품하기로 한 분량)와 다른 수다.
    # 주의: 영상 길이 대신 클립 길이의 합이다. 클립은 영상의 일부만 덮으므로
    # MI 비용의 영상 시간과 빼거나 나누지 않는다.
    # 이름이 비슷하고 단위가 같아 타입 검사와 린트로 잡을 수 없다.
    total_hours: float = 0.0
    # 시간으로 셀 수 없는 work 수. 클립이 없어(영상 단위 티켓) 잴 길이가 없는 작업이다.
    # 이 작업들은 시간 분모에서 빠진다. 이 값이 없으면 시간 진척이 설명 없이 낮게 나온다.
    # `approved_rows_stmt`는 클립 없는 work를 지원하므로(승인되면 납품에도 나간다)
    # 이런 작업은 언제든 생길 수 있다.
    # 0이면 화면은 아무것도 표시하지 않는다. 있으면 시간 진척 옆에 `측정 불가 N건`을 붙인다.
    hours_unmeasurable_count: int = 0
    # 건수 기준 진척률. 승인 ÷ 사람 작업 × 100이고 소수 1자리까지 쓴다.
    # 계산은 서버 한 곳(`analytics.stats.progress_pct`)에서 하고 화면은 값을 그대로 옮긴다.
    # 화면이 `work_by_status`로 다시 나누면 반올림이 달라진다.
    # 그러면 같은 프로젝트의 진척이 화면마다 다르게 보인다.
    progress_pct: float = 0.0
