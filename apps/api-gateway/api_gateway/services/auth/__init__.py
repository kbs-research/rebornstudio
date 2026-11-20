"""인증·계정 바운디드 컨텍스트. 로그인, 세션, 프로필과 인증 경로 보호를 맡는다.

서브모듈:
- `service`: 이메일 인증(login/refresh/logout, 비밀번호 변경·재설정).
- `jwt_revocation`: 세션 무효화(jti/refresh family).
- `login_throttle`·`rate_limit`: 인증 엔드포인트 남용 억제.
- `user`: 프로필과 사용자 조회.
- `admin`: 최고관리자 작업(계정 생성, 역할·상태 변경, 재설정 링크 발급).
- `setup`: 최초 설정(첫 super_admin 생성).
- `consent`: 개인정보 수집·이용 동의의 판정과 기록.

서브모듈을 직접 import 한다. 호출부를 읽기 쉽게 별칭을 붙인다.

    from api_gateway.services.auth import service as auth_service
    from api_gateway.services.auth import admin as admin_service
    from api_gateway.services.auth import jwt_revocation, rate_limit   # 이름이 같아 별칭 없음

주의: 이 파일에서 서브모듈을 eager import 하지 않는다. 도메인 사이의 모듈 순환이 패키지 순환으로
커져 ImportError가 난다(`media/__init__.py`).
"""
