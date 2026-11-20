"""최초 설정 API 라우터. 인증 없이 열리는 유일한 쓰기 경로다.

첫 관리자를 만드는 시점에는 인증할 계정이 없으므로 인증을 요구하지 않는다.
대신 초기화 전에만 동작하고 셋업 토큰을 요구한다.
셋업 토큰은 서버 로그에 찍히므로 토큰을 가진 사람은 서버에 접근할 수 있는 사람이다.

화면은 로그인 전에 `/setup/status`로 로그인과 최초 설정 중 어디로 갈지 정한다.
그래서 `/setup/status`는 초기화 후에도 열어 둔다.
응답에는 판정 결과만 싣고 사용자 수나 설정값은 싣지 않는다.

연동 진단은 인증 없는 경로로 열지 않는다. 버킷 이름과 워크스페이스 이름은 시스템 내부 정보라서
초기화 뒤 super_admin이 플랫폼 개요(`/admin/diagnostics/last`)에서 확인한다.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as st
from reborn_contracts import SetupRequest, SetupStatus
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.dependencies import get_db
from api_gateway.services.auth import setup as setup_service

router = APIRouter()


async def _check_token(db: AsyncSession, token: str) -> None:
    """셋업 토큰을 검사한다. 틀리면 403, 토큰 저장소를 읽지 못하면 503을 낸다.

    Redis 오류나 빈 토큰을 통과로 처리하면 토큰 없이 첫 관리자가 생긴다.
    저장소를 읽지 못한 것은 일시 오류이므로 다시 시도하라고 안내한다
    (`redis_unavailable`, 화면의 오류 사전에 있다).

    저장된 토큰이 없으면 DB로 판단한다.
    이미 초기화됐으면(토큰을 폐기한 정상 상태) 409를 낸다.
    아직 초기화 전이면 기동 때 발급이 실패한 경우다. 새 토큰을 발급해 로그에 찍고
    이번 요청은 403으로 거절한다.
    """
    try:
        state = setup_service.token_state(token)
        if state == "ok":
            return
        if state == "missing":
            if not await setup_service.is_uninitialized(db):
                raise HTTPException(st.HTTP_409_CONFLICT, "이미 최초 설정을 마쳤습니다")
            setup_service.reissue_token()
    except setup_service.SetupTokenUnavailableError:
        raise HTTPException(st.HTTP_503_SERVICE_UNAVAILABLE, "redis_unavailable") from None
    raise HTTPException(st.HTTP_403_FORBIDDEN, "셋업 토큰이 올바르지 않습니다")


@router.get("/status", response_model=SetupStatus, summary="최초 설정 필요 여부")
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    """초기화가 필요한지 돌려준다. 인증 없이 부를 수 있고, 화면의 첫 분기에 쓴다."""
    uninitialized = await setup_service.is_uninitialized(db)
    return SetupStatus(
        uninitialized=uninitialized,
        # 발급 함수를 부르면 이 조회 한 번에 로그의 토큰 값이 바뀐다.
        # 그래서 토큰을 발급할 수 있는 환경인지만 확인한다.
        token_required=uninitialized and setup_service.token_required(),
    )


@router.post(
    "/complete",
    status_code=st.HTTP_201_CREATED,
    summary="첫 super_admin 생성 (초기화 전에만)",
)
async def setup_complete(
    body: SetupRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """첫 super_admin을 만든다. 초기화 전에만 동작하고 셋업 토큰이 필요하다.

    토큰을 먼저 검사한다. 토큰이 틀리면 응답에 시스템 상태를 싣지 않는다.
    초기화 여부는 `complete` 안의 SQL 한 문장이 DB에서 판정한다.
    그래서 동시 요청이 와도 관리자가 둘 생기지 않는다.

    Raises:
        HTTPException: 토큰이 틀린 경우(403). 이미 초기화된 경우(409).
            토큰 저장소를 읽지 못한 경우(503 `redis_unavailable`).
            개인정보 수집·이용 동의가 없는 경우(422 `privacy_consent_required`).
    """
    await _check_token(db, body.token)
    user_id = await setup_service.complete(
        db, email=body.email, display_name=body.display_name, password=body.password,
        privacy_consent=body.privacy_consent,
    )
    return {"user_id": user_id}

