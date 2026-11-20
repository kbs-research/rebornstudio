"""내보내기가 멈추거나 끝없이 재시도하지 않는지 보는 테스트.

`process_pending` beat는 `pending`만 가져간다. 일시 오류를 영구 실패로 만들지 않는다.
예외 하나로 `failed`를 확정하면 그 export는 다시 처리되지 않고, 스로틀 한 번에 사용자가 export를 다시 만들어야 한다.

이 파일이 확인하는 것:
  1. 일시 오류(503·스로틀·연결)면 `pending`으로 되돌려 다음 beat가 다시 가져가는지
  2. 그래도 끝없이 재시도하지 않는지. 상한을 넘으면 `failed`로 확정한다.
  3. 만들던 워커가 비정상 종료해 남은 `running`을 회수하는지. 동작 중인 워커의 건은 그대로 둔다.
  4. 실패 사유가 예외 원문이 아니라 코드인지. 옛 행의 원문도 화면에 나가지 않는다.
"""
from uuid import UUID

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from httpx import AsyncClient
from tests._helpers import new_project


def _client_error(status: int, code: str = "SlowDown") -> ClientError:
    """상태 코드와 오류 코드를 정한 boto3 `ClientError`를 만든다."""
    return ClientError(
        {"Error": {"Code": code, "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "PutObject",
    )


async def _make_export(client: AsyncClient, token: str, name: str) -> tuple[str, str]:
    """프로젝트와 내보내기를 하나씩 만들고 (project_id, export_id)를 돌려준다."""
    pid = await new_project(client, token, name)
    r = await client.post(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {token}"}, json={}
    )
    assert r.status_code in (200, 201, 202), r.text
    return pid, r.json()["export_id"]


@pytest.mark.parametrize(
    "exc",
    [
        _client_error(503),
        _client_error(429, "Throttling"),
        _client_error(400, "SlowDown"),            # 상태는 400인데 코드가 스로틀
        EndpointConnectionError(endpoint_url="https://x"),  # 응답 자체가 없다
    ],
    ids=["s503", "s429", "code-slowdown", "conn-drop"],
)
async def test_transient_returns_to_pending(
    client: AsyncClient, admin_token: str, monkeypatch, exc, request
):
    """일시 오류면 `pending`으로 되돌아 다음 beat가 다시 가져가는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service
    from api_gateway.services.media import storage

    _, eid = await _make_export(client, admin_token, f"exp-t-{request.node.callspec.id}")

    def boom(*a, **k):
        """주어진 예외를 낸다."""
        raise exc

    monkeypatch.setattr(storage, "upload_file", boom)
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "pending", f"{row.status} — 일시인데 영구 실패로 확정됐다"
        assert row.attempts == 1
        # 원문 대신 코드를 남긴다. 화면이 `[일시] An error occurred …`를 그대로 표시하지 않게 한다.
        assert row.error == export_service.UPLOAD_RETRYING


async def test_permanent_fails_immediately(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """권한 오류(403)를 일시 오류로 보지 않고 바로 실패로 확정하는지 확인한다. 바로 확정해야 원인을 확인할 수 있다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service
    from api_gateway.services.media import storage

    _, eid = await _make_export(client, admin_token, "exp-perm")
    monkeypatch.setattr(storage, "upload_file", lambda *a, **k: (_ for _ in ()).throw(
        _client_error(403, "AccessDenied")))
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "failed"
        assert row.error == export_service.UPLOAD_FAILED


async def test_transient_gives_up_at_cap(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """상한을 넘으면 재시도를 멈추고 `failed`로 확정하는지 확인한다.

    시도 횟수를 세지 않고 `pending`으로만 되돌리면 상한에 도달하지 않는다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service
    from api_gateway.services.media import storage

    _, eid = await _make_export(client, admin_token, "exp-cap")
    monkeypatch.setattr(storage, "upload_file", lambda *a, **k: (_ for _ in ()).throw(
        _client_error(503)))
    for _ in range(export_service._MAX_ATTEMPTS + 1):
        async with SessionLocal() as s:
            await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "failed", "상한을 넘겼는데 아직 재시도 중이다"
        assert row.attempts >= export_service._MAX_ATTEMPTS


def test_ffmpeg_stderr_classification():
    """ffmpeg가 남긴 stderr로 일시 오류와 영구 오류를 구분하는지 확인한다.

    이 워커는 원본을 내려받지 않고 presigned URL을 ffmpeg에 직접 넘긴다.
    NCP 대역폭이 낮아 전체 다운로드는 현실적이지 않다.
    그래서 네트워크 순단·스로틀·URL 만료가 실패 경로에 섞인다.
    ffmpeg는 이런 실패를 0이 아닌 종료 코드 하나로만 알리므로 종료 코드로는 코덱이 깨진 파일과 구분되지 않는다.
    둘을 함께 세면 일시 오류를 영구 실패로 만든다.

    403은 일시 오류로 본다. 이 경로의 403은 presigned URL 만료다.
    `job_runner`도 같은 이유로 403을 일시 오류에 넣는다.
    자격증명으로 직접 호출하는 서버 측 403과는 성격이 다르다.
    """
    from api_gateway.services.transient import is_transient_ffmpeg

    transient = [
        "Server returned 503 Service Unavailable",
        "Server returned 429 Too Many Requests",
        "Server returned 403 Forbidden",          # presigned URL 만료
        "Server returned 5XX Server Error reply",
        "http://ncp/x.mp4: Connection timed out",
        "Connection reset by peer",
        "Operation timed out",
    ]
    permanent = [
        "Invalid data found when processing input",
        "moov atom not found",
        "HTTP error 404 Not Found",
        "Unsupported codec",
        "",                                        # 판정 정보가 없으면 영구로 본다
    ]
    for msg in transient:
        assert is_transient_ffmpeg(msg), f"일시인데 영구로 봤다: {msg!r}"
    for msg in permanent:
        assert not is_transient_ffmpeg(msg), f"영구인데 일시로 봤다: {msg!r}"


# 워커 유실 회수
#
# 가져가기(`running`, 시도 +1)는 커밋되고, 만드는 워커는 그 행을 `FOR UPDATE`로 잠근다.
# 그래서 아무도 잠그지 않은 `running`은 만들던 워커가 없다는 뜻이다. 시각 필드 없이 가려낸다.


async def _set(eid: str, **values) -> None:
    """내보내기 행을 원하는 상태로 바꾼다. 워커가 도중에 비정상 종료한 상태를 만든다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export

    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        for k, v in values.items():
            setattr(row, k, v)
        await s.commit()


async def test_orphaned_running_is_reclaimed_to_pending(
    client: AsyncClient, admin_token: str
):
    """만들던 워커가 비정상 종료해 남은 `running`을 다음 beat가 되돌려 `생성 중`으로 남지 않는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service

    _, eid = await _make_export(client, admin_token, "exp-orphan")
    await _set(eid, status="running", attempts=1)   # 가져간 뒤 비정상 종료했다

    async with SessionLocal() as s:
        assert await export_service.reclaim_stale(s) >= 1
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "pending"
        assert row.error == export_service.WORKER_LOST
        assert row.attempts == 1        # 되돌릴 때는 세지 않는다. 가져갈 때 이미 셌다.


async def test_orphaned_running_at_cap_fails(client: AsyncClient, admin_token: str):
    """시도를 다 쓴 건을 되돌리지 않고 실패로 확정하는지 확인한다.

    매번 워커를 비정상 종료시키는 건이 끝없이 재시도되지 않게 한다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service

    _, eid = await _make_export(client, admin_token, "exp-orphan-cap")
    await _set(eid, status="running", attempts=export_service._MAX_ATTEMPTS)

    async with SessionLocal() as s:
        await export_service.reclaim_stale(s)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "failed"
        assert row.error == export_service.WORKER_LOST
        assert row.completed_at is not None


async def test_running_held_by_a_live_worker_is_not_reclaimed(
    client: AsyncClient, admin_token: str
):
    """만드는 중인 건(행을 잠그고 있다)을 건드리지 않는지 확인한다. 동작 중인 워커의 작업을 빼앗지 않는다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service
    from sqlalchemy import select

    _, eid = await _make_export(client, admin_token, "exp-live")
    await _set(eid, status="running", attempts=1)

    async with SessionLocal() as worker, SessionLocal() as beat:
        # 워커가 행을 잠근 채 만드는 중이다.
        await worker.scalar(
            select(Export).where(Export.export_id == UUID(eid)).with_for_update()
        )
        await export_service.reclaim_stale(beat)
        await worker.rollback()
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "running", "살아 있는 워커의 건을 되돌렸다"
        assert row.error is None


async def test_claim_counts_the_attempt_before_building(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """시도 수가 가져갈 때 오르는지 확인한다.

    만드는 도중 워커가 비정상 종료해도 시도 수가 트랜잭션과 함께 사라지지 않는다.
    실패할 때만 세면 워커 프로세스가 종료될 때 그 증가분도 롤백된다.
    그러면 매번 워커를 종료시키는 건이 시도 수 0인 채로 계속 다시 처리된다.
    """
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_service

    _, eid = await _make_export(client, admin_token, "exp-claim")

    async def die(*a, **k):
        """조립 도중 워커가 비정상 종료한 것처럼 예외를 낸다."""
        raise RuntimeError("워커가 여기서 죽었다")   # 조립 밖에서 올라오는 예외다(DB 오류 등).

    monkeypatch.setattr(export_service, "_run_single", die)
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        # 가져간 상태(`running`)로 남았다.
        # 시도 수는 이미 커밋됐고, 다음 beat의 회수 단계가 되돌린다.
        assert row.status == "running"
        assert row.attempts == 1


async def test_build_failure_is_a_code_not_a_traceback(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """조립이 실패하면 파이썬 예외 원문이 아니라 코드가 이력 행에 저장되는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_package, export_service

    _, eid = await _make_export(client, admin_token, "exp-build")

    def broken(*a, **k):
        """조립 실패를 흉내 낸다."""
        raise KeyError("payload")

    monkeypatch.setattr(export_package, "write_package", broken)
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "failed"
        assert row.error == export_service.BUILD_FAILED


async def test_integrity_failure_fails_the_export(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """색인과 파일이 어긋나면 올리지 않고 실패로 처리하는지 확인한다."""
    from api_gateway.database import SessionLocal
    from api_gateway.models import Export
    from api_gateway.services.exports import export_package, export_service
    from api_gateway.services.media import storage

    _, eid = await _make_export(client, admin_token, "exp-integrity")
    uploaded: list[str] = []

    def mismatched(*a, **k):
        """색인과 파일이 어긋난 무결성 오류를 낸다."""
        raise export_package.PackageIntegrityError("색인 2줄 · metadata 파일 1개")

    monkeypatch.setattr(export_package, "write_package", mismatched)
    monkeypatch.setattr(storage, "upload_file", lambda _p, key, **k: uploaded.append(key))
    async with SessionLocal() as s:
        await export_service.process_pending(s, limit=10)
    async with SessionLocal() as s:
        row = await s.get(Export, UUID(eid))
        assert row.status == "failed"
        assert row.error == export_service.INTEGRITY_FAILED
    assert not [k for k in uploaded if eid in k], "어긋난 패키지를 올렸다"


async def test_legacy_error_text_never_reaches_the_screen(
    client: AsyncClient, admin_token: str
):
    """코드 도입 전 행의 원문(`[일시] An error …`)도 화면에는 코드로 나가는지 확인한다.

    `error` 필드에 한글(`[일시]`)이 섞여 있어서 화면 사전이 서버가 보낸 한국어 문장으로 보고 그대로 표시할 수 있다.
    그 뒤는 boto3 영문 원문이다.
    """
    from api_gateway.services.exports import export_service

    pid, eid = await _make_export(client, admin_token, "exp-legacy")
    await _set(eid, status="failed", attempts=5,
               error="[일시] An error occurred (503) when calling the PutObject operation")
    rows = (await client.get(
        f"/projects/{pid}/exports", headers={"Authorization": f"Bearer {admin_token}"}
    )).json()
    row = next(r for r in rows if r["export_id"] == eid)
    assert row["error"] == export_service.UPLOAD_FAILED
    assert row["attempts"] == 5 and row["max_attempts"] == export_service._MAX_ATTEMPTS
