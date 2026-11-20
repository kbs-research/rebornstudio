# services/analytics

집계·지표 도메인입니다. 원천 건수를 모아 화면과 감시 시스템이 읽는 지표로 조합합니다. 이 도메인은 도메인 상태를 바꾸지 않습니다. 쓰는 대상은 캐시뿐입니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `stats.py` | 원천 집계와 프로젝트 통계(`for_project`·`compute`). 진척률 판정(`progress_pct` 건수, `hours_progress` 시간), 사람 작업 모집단(`_HUMAN_WORK`), Redis 캐시 |
| `dashboard/` | 화면용 지표 조합. 사업 KPI·사람별 실적·작업 흐름·단계 대기 건수·처리 능력([README](dashboard/README.md)) |

## 설계 이유

- 진척률은 `stats.progress_pct` 한 함수가 정합니다. 개요·사업 KPI·프로젝트 통계가 각자 나누면 같은 프로젝트가 화면마다 다른 비율로 표시됩니다. 화면은 서버가 준 값을 그대로 씁니다.
- 날짜는 운영 시간대(Asia/Seoul) 기준으로 자릅니다. 경계는 `services/operating_time.py`가 정합니다.
- 비식별화 미흡 신고로 멈춘 클립은 클립의 `rights_status`(`legal_review`)로 표시되므로 작업 상태별 집계에 포함되지 않습니다. 그래서 파이프라인 집계는 신고 건수(`deident_reports`)를 따로 셉니다. 셈 조건은 신고 목록(`work.deident_reports`)과 같습니다.
- 납품 패키지 생성은 저장소에 쓰는 일이므로 이 패키지가 아니라 `services/exports`에 있습니다. 납품 불가 사유 분포는 `exports`의 판정 함수를 읽기만 합니다.

## 의존성

- 부르는 것: `models`, `api_gateway.redis_infra`, `services.authz`·`operating_time`, 허용 목록의 `media.progress`·`media.video`·`work.service`·`analysis.job_state`·`exports.export_service`·`exports.export_package`
- 부르는 쪽: `api/v1/dashboard.py`·`projects.py`

## 남은 일

- 없음.
