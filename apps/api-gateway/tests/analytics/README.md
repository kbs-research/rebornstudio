# tests/analytics

집계·지표 도메인(`services/analytics`) 테스트입니다. 대시보드 KPI, 파이프라인 요약, 통계 캐시, 한국 시각 집계를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_dashboard.py` | 대시보드 엔드포인트 세 개(summary, labeler-performance, business-kpi)를 검사합니다. |
| `test_dashboard_extra.py` | workflow-health와 진척률 규칙을 검사합니다. |
| `test_hours_denominator.py` | 진척의 시간 분모가 건수 분모처럼 제외된 클립(`rights_status='rejected'`)을 빼는지 확인합니다. |
| `test_kst_aggregates.py` | 날짜 집계를 한국 시각 기준으로 자르는지 검사합니다. |
| `test_labeler_approval_owner.py` | 수정 후 승인도 라벨러의 승인으로 세는지 검사합니다. |
| `test_labeler_approval_once.py` | 사람별 실적에서 관리자의 캡션 고쳐 다시 승인이 승인을 한 번 더 세지 않는지 검사합니다. |
| `test_labeler_summary.py` | 기간 안의 프로젝트 합계(`labeler-performance/summary`)가 같은 기간 사람별 행의 합과 같은지, 제출이 없거나 판정만 있는 기간의 비율과 분모를 검사합니다. |
| `test_pipeline_summary.py` | 작업 단계 대기 건수 요약(GET /projects/{id}/pipeline/summary)을 검증합니다. 비식별화 신고 배지(`deident_reports`)가 신고 목록의 `total`과 같은지도 봅니다. |
| `test_stats_cache_annotator.py` | stats의 30초 캐시를 검증합니다. |
| `test_stats_population.py` | 통계가 영상 단위 디스패치 표식을 빼고 사람 작업만 세는지 검증합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_kst_aggregates.py`는 `api_gateway/` 소스를 읽어 시간대 리터럴이 한 곳에만 있는지 봅니다(`parents[2]`).

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
