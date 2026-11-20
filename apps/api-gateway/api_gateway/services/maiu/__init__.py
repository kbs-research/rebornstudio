"""MAIU(Naver Media Intelligence) 연동 바운디드 컨텍스트.

분석 요청, 폴링, 클립 생성, 비용 집계를 담당한다.

서브모듈:
- `service`: 분석 요청(dispatch), 잡 조회, 비용·처리 속도 집계, 실패 재시도.
- `client`: 어댑터 경계(Fake/Naver 전환).
- `naver_client`: 실제 Naver MAIU HTTP 구현.
- `reconciler`: 5단계 상태 머신.
- `slot_counter`: 정상 사이클과 보류(deferred) 재처리 사이클을 10:1로 배분한다.
- `provisioning`: MI 워크스페이스·연동 버킷·분석 프로젝트의 읽기 전용 진단.

호출하는 쪽은 서브모듈을 직접 import 한다. 읽기 쉽게 별칭을 붙인다.

    from api_gateway.services.maiu import service as maiu_service
    from api_gateway.services.maiu import client as maiu_client

주의: 도메인끼리 서로 의존하므로 패키지 수준에서 서브모듈을 eager import 하면 순환 import가 생긴다.
그래서 이 파일은 서브모듈을 import 하지 않는다(`media/__init__.py`).
"""
