"""도메인 이벤트(감사 로그) 조회 DTO."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DomainEventDTO(BaseModel):
    """감사 로그의 도메인 이벤트 한 건."""
    event_id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None = None
    #: 행위자의 이름·이메일. 서버가 붙인다.
    #: 행위자에는 멤버가 아닌 `super_admin`과 나간 멤버도 있다. 그래서 화면이 프로젝트 멤버
    #: 목록에서 찾으면 행위자를 알 수 없는 행이 생긴다.
    #: 두 값을 한 문자열로 합쳐 주지 않는다. 표시 방식(이름 우선, 없으면 이메일)은
    #: 화면의 규칙(`lib/person.personLabel`) 한 곳에 둔다.
    #: 행위자가 없으면 둘 다 `None`이다(시스템 이벤트).
    actor_name: str | None = None
    actor_email: str | None = None
    event_type: str
    target_kind: str
    target_id: UUID | None = None
    project_id: UUID | None = None
    payload: dict
