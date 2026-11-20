"""플랫폼 운영 바운디드 컨텍스트. 연동 진단, 사용자 활동 이력, 감사 로그 보존을 다룬다.

서브모듈:
- `diagnostics`: 플랫폼 연동 점검과 주기 기록, 연동 예외를 한국어 사유로 바꾸는 `exception_reason`.
- `audit_log`: 사용자 활동 이력.
- `audit_retention`: 감사 로그 보존 기간 적용.

서브모듈은 직접 import한다. 호출부를 읽기 쉽도록 별칭을 붙인다.

    from api_gateway.services.ops import diagnostics as diag_service

주의: 여기서 서브모듈을 eager import하지 않는다.
도메인끼리 서로 의존하므로 eager import하면 순환 import가 생긴다(`media/__init__.py`).
"""

__all__ = ["audit_log", "audit_retention", "diagnostics"]
