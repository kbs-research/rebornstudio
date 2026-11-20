# services/analytics/dashboard

개요·통계(사람별 실적)·파이프라인 화면이 읽는 지표를 조합합니다. 원천 건수는 `analytics/stats.py`가 냅니다.
이 패키지는 그 건수에 판정·비율·기간을 붙여 `reborn_contracts.dashboard`의 응답 모양으로 만듭니다.
읽기 전용입니다.

## 주요 파일

지표 묶음별로 모듈을 나눕니다. 모두 조회이므로 `commands`·`dto`·`jobs` 모듈은 두지 않습니다.

| 파일 | 역할 | 공개 이름 |
|---|---|---|
| `__init__.py` | 파사드. 아래 모듈의 이름을 모두 다시 내보냅니다 | 전부 |
| `classify.py` | 여러 지표가 함께 쓰는 비율과 판정 조건. 승인율·반려율(작업량 대비)과 각 분모(`_rate_fields`가 비율과 분모를 함께 냅니다), 수정률, 검수자 리비전 판정, 지금 보유 중인 작업(`_current_loads`) | `_rate_fields` · `_approval_rate` · `_rejection_rate` · `_edit_rate` · `_is_reviewer_revision` · `_current_loads` |
| `queries.py` | 현황·사람 지표. 사업 KPI·작업 흐름·단계 배지·사람 표와 기간 합계. 승인은 작업마다 첫 승인 하나만 셉니다. 사람 표와 기간 합계는 같은 사람 목록(`_period_counts`)을 써서 합계가 행의 합과 같습니다 | `business_kpi` · `workflow_health` · `pipeline_summary` · `labeler_performance` · `labeler_summary` |
| `capacity.py` | 처리 능력(장기 대기)과 납품 검사 분포, 완료 수 | `capacity_health` · `_deliverable_summary` · `_completed_count` |

## 설계 이유

- 호출부는 파사드를 씁니다. 라우터(`api/v1/dashboard.py`)는
  `from api_gateway.services.analytics import dashboard as dashboard_service`로 부릅니다.
  `__init__.py`가 이 이름을 모두 다시 내보내므로, 호출부는 하위 모듈 위치를 몰라도 됩니다.
- 둘 이상의 모듈이 쓰는 비율·판정 조건은 `classify` 한 곳에 둡니다. 여러 지표가 같은 헬퍼를 써야
  같은 사람을 같은 수로 표시합니다. 한 모듈만 쓰는 헬퍼(`_labeler_counts` 등)는 그 모듈에 둡니다.
- 도메인을 가로지르는 import는 두 모듈에만 있습니다. `exports`(납품 검사)는 `capacity`만 부릅니다.
  `media.video`·`work.service`·`analysis.job_state`는 `queries`만 부릅니다.
  import-linter 허용 목록(`apps/api-gateway/pyproject.toml`)에는 가져오는 쪽 이름을 이 하위 모듈로 적습니다.

### 의존 방향

```
__init__ ─→ queries ─→ capacity ─→ classify
```

- `classify`는 맨 아래에 있고, 형제 모듈을 import하지 않습니다.
- `queries`는 `business_kpi`의 납품 가능 수 때문에 `capacity._deliverable_summary`를 부릅니다.
  단계 표시줄의 완료 수 때문에 `capacity._completed_count`도 부릅니다.
  `capacity`가 `queries`를 부르는 반대 방향은 두지 않습니다.

### 테스트가 바꾸는 이름

`tests/analytics/test_dashboard.py`는 `export_service.DELIVERABLE_SCAN_LIMIT`를 모듈 객체에서 바꿉니다.
`capacity.py`는 호출할 때마다 `export_service.DELIVERABLE_SCAN_LIMIT`로 이 값을 읽으므로, 테스트가 바꾼 값이 적용됩니다.
하위 모듈에서 `from ... import DELIVERABLE_SCAN_LIMIT`로 가져오도록 바꾸면 테스트가 바꾼 값이 적용되지 않습니다.

## 의존성

- 부르는 것: `models`, `analytics.stats`, `services.authz`,
  `analysis.job_state`, `media.video`, `work.service`, `exports.{export_service,export_package}`
- 부르는 쪽: `api/v1/dashboard.py`(라우터)

## 남은 일

- `queries.py`가 큽니다. 사람 지표(`labeler_performance`·`labeler_summary`·`_labeler_counts`)를 별도 모듈로 나눌 후보입니다.
