"""일시·영구 오류 판정의 기준 정의와 워커 복사본이 어긋나지 않는지 보는 테스트.

기준 정의는 `services/transient.py`다.
워커 패키지 `reborn_transcode`는 워커가 서버 코드와 DB에 붙지 않는 경계 때문에 `api_gateway`를 import할 수 없다.
그래서 `job_runner._HTTP_TRANSIENT`에 손으로 옮긴 복사본을 둔다.
기준 정의에 상태 코드를 더하고 워커를 고치지 않으면 워커는 그 오류를 영구 실패로 확정해 시도 횟수를 소모한다.
판정 규칙을 한 곳에 둘 수 없으므로 두 정의가 어긋나면 이 테스트가 실패한다.

403은 의도적으로 다르게 판정한다.
워커의 업로드 경로에서는 presigned URL 만료가 403으로 온다.
잠시 뒤 새 URL을 받으면 되므로 일시 오류다.
서버는 자격 증명으로 직접 호출하므로 403은 권한 문제다.
서버가 403을 일시 오류로 보면 계속 재시도하면서 원인을 감춘다.
이 차이를 이 파일의 값으로 고정한다.
"""
from __future__ import annotations

from api_gateway.services.transient import TRANSIENT_STATUS

#: 워커가 기준 정의에 더해 일시 오류로 보는 상태 코드.
#: 값을 늘리면 근거를 모듈 docstring에 함께 적는다.
WORKER_EXTRA: frozenset[int] = frozenset({403})


def test_worker_copy_matches_canonical() -> None:
    """워커 복사본이 기준 정의에 403을 더한 집합과 같은지 확인한다. 한쪽만 고치면 이 테스트가 실패한다."""
    from reborn_transcode.job_runner import _HTTP_TRANSIENT

    assert set(_HTTP_TRANSIENT) == set(TRANSIENT_STATUS) | WORKER_EXTRA, (
        "일시 오류 판정이 갈렸다 — `services/transient.py`(기준)와 "
        "`reborn_transcode/job_runner._HTTP_TRANSIENT`(워커 복사본)를 같이 고쳐야 한다. "
        f"기준={sorted(TRANSIENT_STATUS)} 워커={sorted(_HTTP_TRANSIENT)}"
    )


def test_canonical_excludes_403() -> None:
    """기준 정의에 403이 없는지 확인한다. 403이 들어가면 서버가 권한 오류를 계속 재시도한다."""
    assert 403 not in TRANSIENT_STATUS


def test_maiu_client_uses_canonical() -> None:
    """MAIU 클라이언트가 복사본 없이 기준 정의를 그대로 참조하는지 확인한다."""
    from api_gateway.services.maiu.naver_client import _TRANSIENT_STATUS

    assert _TRANSIENT_STATUS is TRANSIENT_STATUS
