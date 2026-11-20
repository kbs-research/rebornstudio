"""조직·프로젝트 바운디드 컨텍스트. 프로젝트, 멤버, 조직을 다룬다.

`project`는 프로젝트 CRUD와 멤버 초대·역할 변경·제거·나가기를 맡는다.
`organization`은 기본 조직 한 행을 찾는다.

서브모듈은 직접 import 한다. 호출부를 읽기 쉽도록 별칭을 붙인다.

    from api_gateway.services.org import project as project_service

역할 기반 권한 가드는 모든 도메인이 쓰므로 평면 모듈 `services/authz.py`에 둔다.
가드를 이 패키지에 두면 `work`·`media`가 권한 검사 하나 때문에 `org`를 import 해야 한다.
인가의 근거는 `ProjectMember.role`이다.

주의: 여기서 서브모듈을 eager import 하지 않는다. `media/__init__.py` 주석을 참고한다.
"""
