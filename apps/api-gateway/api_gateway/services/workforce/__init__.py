"""작업자 운영 바운디드 컨텍스트. 본인 판정 목록과 본인 로그인 이력을 다룬다.

서브모듈은 직접 import한다. 호출부를 읽기 쉽도록 별칭을 붙인다.

    from api_gateway.services.workforce import decisions as decisions_service

주의: 여기서 서브모듈을 eager import하지 않는다.
도메인끼리 서로 의존하므로 eager import하면 순환 import가 생긴다(`media/__init__.py`).
"""
