"""집계·지표 바운디드 컨텍스트. 운영 상태를 읽어 화면에 쓸 수로 집계한다.

서브모듈:
- `stats`: 원천 건수(그룹별 집계, 캐시)와 진척률 판정.
- `dashboard`: `stats` 위에서 화면용 지표를 조합한다
  (summary / labeler_performance / workflow_health / business_kpi 등).

이 컨텍스트는 읽기 전용이다.
납품물 생성(export)은 S3에 쓰는 산출 작업이라서 `services/exports/`에 둔다.

서브모듈을 직접 import하고, 호출부를 읽기 쉽게 별칭을 붙인다.

    from api_gateway.services.analytics import dashboard as dashboard_service
    from api_gateway.services.analytics import stats as stats_service

주의: 이 파일에서 서브모듈을 eager import하지 않는다. 이유는 `media/__init__.py` 주석에 있다.
"""
