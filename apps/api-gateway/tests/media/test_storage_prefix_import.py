"""폴더 단위 등록(서버 작업) 테스트. 큐, beat 러너, 재개 규약을 확인한다.

세 가지 계약을 확인한다.
  1. API는 묶음 행에 사양만 남기고 즉시 응답한다. 브라우저가 버킷을 순회하지 않는다.
  2. 러너는 페이지마다 커밋하고 token을 저장한다. 도중에 비정상 종료돼도 다음 주기가 끊긴 지점부터
     재개한다.
  3. 일시 오류(요청량 제한·일시 장애)는 상한을 깎지 않고 running으로 남는다. 영구 오류만 3회까지
     센다.
"""
import uuid

from api_gateway.database import SessionLocal
from api_gateway.models import IngestBatch, Video
from api_gateway.services.media import storage, storage_import
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests.conftest import uniq


async def _new_project(client: AsyncClient, admin_token: str) -> str:
    """테스트용 프로젝트를 만들고 project_id를 돌려준다. 조직은 첫 프로젝트의 것을 쓴다."""
    slug = f"pi-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "pi"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    return pid


def _two_page_browse(fail_second_page_once: bool = False):
    """두 페이지짜리 가짜 버킷 목록을 만든다.

    1페이지는 mp4 3개와 json 1개이고 token이 있다. 2페이지는 mp4 2개이고 끝이다.
    `fail_second_page_once=True`면 2페이지 첫 시도에서 연결 오류(`ConnectionError`)를 낸다.
    재개 규약을 확인할 때 쓴다. 끊긴 token부터 재개해 1페이지를 다시 등록하지 않는지 확인한다.
    중복은 dedup 대신 페이지 단위 커밋으로 막는다(`register_keys`).
    builtin `ConnectionError`는 boto 예외가 아니라서 러너가 `error_count`로 센다.
    """
    state = {"failed": False}

    def fake_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """token이 없으면 1페이지를, 있으면 2페이지를 돌려준다."""
        if token is None:
            return {
                "prefix": prefix, "bucket": "fake", "folders": [],
                "files": [{"key": f"{prefix}clip-{i}.mp4", "size": 10} for i in range(3)]
                + [{"key": f"{prefix}meta.json", "size": 1}],
                "truncated": True, "next_token": "page-2",
            }
        if fail_second_page_once and not state["failed"]:
            state["failed"] = True
            raise ConnectionError("ncp transient")
        return {
            "prefix": prefix, "bucket": "fake", "folders": [],
            "files": [{"key": f"{prefix}clip-{i}.mp4", "size": 10} for i in (3, 4)],
            "truncated": False, "next_token": None,
        }

    return fake_browse


async def _queue(client: AsyncClient, admin_token: str, pid: str, **overrides):
    """폴더째 등록 API를 부른다. `overrides`로 요청 본문의 값을 바꾼다."""
    payload = {
        "prefix": "sample/2026-07/",
        "copyright_policy": "pre_cleared",
        "upload_batch_id": str(uuid.uuid4()),
    } | overrides
    return await client.post(
        f"/projects/{pid}/storage/register-prefix",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )


async def test_queue_then_runner_registers_all_pages(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """큐에 넣으면 즉시 돌아오고 러너가 모든 페이지를 등록한다. 묶음은 done, 라벨은 폴더명이다."""
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    pid = await _new_project(client, admin_token)

    r = await _queue(client, admin_token, pid)
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert r.json()["import_status"] == "pending"

    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats == {"batches": 1, "registered": 5}  # mp4 5건이다. json은 확장자 검사에서 빠진다.

    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, uuid.UUID(batch_id))
        assert batch.import_status == "done"
        assert batch.import_error is None
        assert batch.label == "2026-07"  # 사람이 알아볼 기본 이름은 폴더명이다.
        n = len((await s.scalars(
            select(Video).where(Video.upload_batch_id == batch.batch_id)
        )).all())
        assert n == 5

    # 묶음 목록에도 import 필드가 실린다. 화면은 이 값으로 '등록 중'과 '실패'를 구분한다.
    rows = (await client.get(
        f"/projects/{pid}/ingest-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    mine = next(b for b in rows if b["batch_id"] == batch_id)
    assert mine["import_status"] == "done"
    assert mine["import_prefix"] == "sample/2026-07/"


async def test_transient_error_keeps_running_then_resumes(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """연결 오류가 1회 나도 running으로 남고 다음 주기에 token부터 재개해 끝낸다.

    오류 전의 1페이지 등록분은 이미 커밋돼 있다.
    """
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse(fail_second_page_once=True))
    pid = await _new_project(client, admin_token)
    batch_id = uuid.UUID((await _queue(client, admin_token, pid)).json()["batch_id"])

    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats["registered"] == 3  # 1페이지는 이미 커밋돼 진행 수에 잡힌다.
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, batch_id)
        assert batch.import_status == "running"  # 실패로 확정하지 않는다
        assert batch.import_spec["error_count"] == 1
        assert batch.import_spec["next_token"] == "page-2"

    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats["registered"] == 2  # 2페이지부터 재개해 1페이지를 중복 등록하지 않는다.
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, batch_id)
        assert batch.import_status == "done"
        n = len((await s.scalars(
            select(Video).where(Video.upload_batch_id == batch_id)
        )).all())
        assert n == 5


def _always_failing_second_page(exc: Exception):
    """2페이지에서 매번 같은 예외를 낸다. 일시·영구 오류 분류를 확인할 때 쓴다."""
    first = _two_page_browse()

    def fake_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """1페이지는 정상으로 돌려주고 2페이지에서는 항상 `exc`를 낸다."""
        if token is None:
            return first(prefix, max_keys, recursive, token, bucket)
        raise exc

    return fake_browse


def _client_error(code: str, status: int):
    """ListObjectsV2의 `ClientError`를 오류 코드와 HTTP 상태로 만든다."""
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status,
                                             "RequestId": "tx-10-req"}}, "ListObjectsV2")


async def test_throttling_never_settles_the_job(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """요청량 제한 오류는 상한을 깎지 않는다.

    일시 오류는 running으로 남고 다음 주기가 끊긴 페이지부터 이어받는다.
    일시·영구 오류를 구분하지 않으면 NCP 요청량 제한이 세 번 연속으로 나면 폴더 등록이 영구 실패로 끝난다.
    """
    monkeypatch.setattr(storage, "browse_bucket",
                        _always_failing_second_page(_client_error("SlowDown", 503)))
    pid = await _new_project(client, admin_token)
    batch_id = uuid.UUID((await _queue(client, admin_token, pid)).json()["batch_id"])
    for _ in range(storage_import._MAX_ERRORS + 1):
        async with SessionLocal() as s:
            await storage_import.run_pending_imports(s)
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, batch_id)
        assert batch.import_status == "running", "일시 오류가 영구 실패로 굳었다"
        assert "error_count" not in batch.import_spec
        assert batch.import_spec["transient_count"] == storage_import._MAX_ERRORS + 1


async def test_permanent_failure_speaks_plainly(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """영구 실패는 상한에서 멈추고 화면 사유를 운영자용 문구로 쓴다.

    원문은 감사 이벤트에 남긴다. `repr(exc)`를 쓰면 Job 행 툴팁에 `ClientError('… RequestId …')`가 그대로 나간다.
    """
    from api_gateway.models import DomainEvent

    monkeypatch.setattr(storage, "browse_bucket",
                        _always_failing_second_page(_client_error("NoSuchBucket", 404)))
    pid = await _new_project(client, admin_token)
    batch_id = uuid.UUID((await _queue(client, admin_token, pid)).json()["batch_id"])
    for _ in range(storage_import._MAX_ERRORS):
        async with SessionLocal() as s:
            await storage_import.run_pending_imports(s)
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, batch_id)
        assert batch.import_status == "failed"
        said = batch.import_error or ""
        assert said and "ClientError" not in said and "RequestId" not in said, said
        ev = (await s.scalars(select(DomainEvent).where(
            DomainEvent.target_id == batch_id,
            DomainEvent.event_type == "storage.prefix_import_failed",
        ))).first()
    assert ev is not None and "NoSuchBucket" in ev.payload["raw"], "원문은 감사에 남아야 한다"


async def test_queue_rejects_empty_prefix(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """빈 폴더 등록 요청을 바로 400으로 거절한다.

    사용자가 화면을 떠나기 전에 알 수 있는 실패는 즉시 돌려준다.
    `pre_cleared`에는 근거를 요구하지 않는다. 등록 시점에는 권리 결정이 없어서 증빙할 결정도 없다.
    근거를 요구하면 화면이 보내지 않는 기본값에 걸려 버킷 등록이 모두 400으로 실패한다.
    """
    def empty_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """파일이 없는 빈 폴더를 흉내 낸다."""
        return {"prefix": prefix, "bucket": "fake", "folders": [], "files": [],
                "truncated": False, "next_token": None}

    monkeypatch.setattr(storage, "browse_bucket", empty_browse)
    pid = await _new_project(client, admin_token)

    r = await _queue(client, admin_token, pid)
    assert r.status_code == 400
    assert r.json()["detail"] == "empty_prefix"

    # 근거 없이 pre_cleared로 넣어도 통과한다. 이 정책은 등록 시점에 권리를 결정하지 않는다.
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    r = await _queue(client, admin_token, pid, copyright_policy="pre_cleared")
    assert r.status_code == 200, r.text


async def test_reimport_same_prefix_skips_existing(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """같은 폴더를 다시 등록하면 이미 등록한 파일은 건너뛰고 건너뛴 수를 묶음에 남긴다.

    다시 등록하면 같은 원본이 영상·작업·분석 비용까지 두 배가 된다.
    새로 넣은 영상이 0건이어도 건너뛴 파일이 있으면 묶음을 지우지 않는다.
    묶음까지 지우면 사용자에게는 등록했는데 아무 일도 일어나지 않은 것으로 보인다.
    """
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    pid = await _new_project(client, admin_token)

    first = (await _queue(client, admin_token, pid)).json()["batch_id"]
    async with SessionLocal() as s:
        await storage_import.run_pending_imports(s)

    second = (await _queue(client, admin_token, pid)).json()["batch_id"]
    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats["registered"] == 0

    async with SessionLocal() as s:
        assert await s.get(IngestBatch, uuid.UUID(first)) is not None
        batch = await s.get(IngestBatch, uuid.UUID(second))
        assert batch is not None, "건너뛴 파일이 있는데 묶음이 사라졌다"
        assert batch.import_status == "done"
        assert batch.import_spec["skipped_existing"] == 5
        n = len((await s.scalars(
            select(Video).where(Video.project_id == uuid.UUID(pid))
        )).all())
        assert n == 5, "같은 파일이 다시 등록됐다"

    # 건너뛴 수는 묶음 상세와 목록 응답에도 실린다. 화면이 `N건 건너뜀`으로 보여 준다.
    h = {"Authorization": f"Bearer {admin_token}"}
    detail = (await client.get(f"/ingest-batches/{second}", headers=h)).json()
    assert detail["import_skipped_existing"] == 5
    rows = (await client.get(f"/projects/{pid}/ingest-batches", headers=h)).json()
    assert {r["batch_id"]: r["import_skipped_existing"] for r in rows}[second] == 5


async def test_reimport_registers_only_new_files(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """폴더에 파일이 늘어난 뒤 다시 등록하면 새 파일만 등록한다."""
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    pid = await _new_project(client, admin_token)
    await _queue(client, admin_token, pid)
    async with SessionLocal() as s:
        await storage_import.run_pending_imports(s)

    def grown(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """기존 다섯 파일에 한 파일이 더해진 폴더를 한 페이지로 돌려준다."""
        return {"prefix": prefix, "bucket": "fake", "folders": [],
                "files": [{"key": f"{prefix}clip-{i}.mp4", "size": 10} for i in range(6)],
                "truncated": False, "next_token": None}

    monkeypatch.setattr(storage, "browse_bucket", grown)
    second = (await _queue(client, admin_token, pid)).json()["batch_id"]
    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats["registered"] == 1
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, uuid.UUID(second))
        assert batch.import_spec["skipped_existing"] == 5
        keys = [v.source_uri.rsplit("/", 1)[-1] for v in (await s.scalars(
            select(Video).where(Video.upload_batch_id == batch.batch_id)))]
        assert keys == ["clip-5.mp4"]


async def test_register_keys_keeps_manual_reregistration(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """사람이 키를 골라 등록하는 경로는 같은 파일도 다시 등록하고 중복으로 표시만 한다.

    같은 원본을 정책을 바꿔 다시 돌리는 것은 정당한 사용이라 고른 파일을 건너뛰지 않는다.
    """
    from api_gateway.services.media import video as video_service
    from tests._helpers import user_id_by_email

    pid = await _new_project(client, admin_token)
    async with SessionLocal() as s:
        uid = uuid.UUID(await user_id_by_email("admin@example.com"))
        first = await video_service.register_keys(
            s, uuid.UUID(pid), ["manual/a.mp4"], "pre_cleared",
            created_by=uid, upload_batch_id=None)
        again = await video_service.register_keys(
            s, uuid.UUID(pid), ["manual/a.mp4"], "pre_cleared",
            created_by=uid, upload_batch_id=None)
        await s.commit()
    assert first["registered"] == 1
    assert again["registered"] == 1 and again["duplicates"] == 1


async def test_empty_prefix_leaves_no_batch(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """영상이 하나도 없는 폴더는 빈 묶음을 남기지 않는다.

    이력에 0건짜리 행이 쌓이면 목록에서 실제 등록을 찾기 어렵다.
    중복 차단이 없으므로 영상이 없는 폴더로 확인한다.
    """
    def empty_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """영상 없이 json 하나만 있는 폴더를 흉내 낸다."""
        return {"prefix": prefix, "bucket": "fake", "folders": [],
                "files": [{"key": f"{prefix}meta.json", "size": 1}],
                "truncated": False, "next_token": None}

    monkeypatch.setattr(storage, "browse_bucket", empty_browse)
    pid = await _new_project(client, admin_token)
    batch = (await _queue(client, admin_token, pid)).json()["batch_id"]
    async with SessionLocal() as s:
        await storage_import.run_pending_imports(s)
    async with SessionLocal() as s:
        assert await s.get(IngestBatch, uuid.UUID(batch)) is None, "0건 묶음이 남았다"


async def test_app_owned_prefixes_are_not_registered(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """앱이 만든 경로(변환본·업로드 원본 등)의 원본 등록을 400으로 거절한다.

    같은 영상이 사본으로 한 번 더 들어가지 않게 한다. 경로는 사람이 입력하므로 서버가 거절한다.
    """
    called = {"browse": 0}

    def fake_browse(prefix="", max_keys=500, recursive=False, token=None, bucket=None, store=None):
        """저장소 목록 조회 횟수를 센다."""
        called["browse"] += 1
        return {"prefix": prefix, "bucket": "fake", "folders": [], "files": [],
                "truncated": False, "next_token": None}

    monkeypatch.setattr(storage, "browse_bucket", fake_browse)
    pid = await _new_project(client, admin_token)
    headers = {"Authorization": f"Bearer {admin_token}"}

    for prefix in ("transcoded/", "transcoded/720p/", "intake/"):
        r = await _queue(client, admin_token, pid, prefix=prefix)
        assert r.status_code == 400, (prefix, r.text)
        assert r.json()["detail"] == "system_prefix"

    r = await client.post(
        f"/projects/{pid}/storage/register", headers=headers,
        json={"keys": ["transcoded/720p/x.mp4"], "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 400 and r.json()["detail"] == "system_prefix", r.text
    assert called["browse"] == 0, "거절한 요청은 저장소를 조회하지 않는다"


async def test_default_policy_registers_pre_cleared(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """화면처럼 정책 값을 보내지 않아도 영상이 pre_cleared로 등록되는지 확인한다.

    DB는 영상 권리 정책으로 pre_cleared만 받는다. 요청 기본값이 다른 값이면 모든 행이
    CHECK 제약에 걸려 묶음이 등록 중에 멈춘다.
    """
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    pid = await _new_project(client, admin_token)
    r = await client.post(
        f"/projects/{pid}/storage/register-prefix",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"prefix": "sample/2026-08/", "upload_batch_id": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        stats = await storage_import.run_pending_imports(s)
    assert stats["registered"] == 5
    async with SessionLocal() as s:
        policies = set((await s.scalars(select(Video.copyright_policy).where(
            Video.upload_batch_id == uuid.UUID(r.json()["batch_id"])))).all())
    assert policies == {"pre_cleared"}


async def test_db_write_failure_settles_at_limit(
    client: AsyncClient, admin_token: str, monkeypatch
):
    """등록 도중 DB 쓰기가 실패해도 오류 횟수를 남기고 상한에서 실패로 끝나는지 확인한다.

    실패한 세션을 되돌리지 않고 기록하면 기록의 커밋까지 실패한다. 그러면 오류 횟수가 남지 않아
    묶음이 매 주기 같은 실패를 되풀이한다. 실패한 페이지의 행은 남지 않아야 한다.
    """
    monkeypatch.setattr(storage, "browse_bucket", _two_page_browse())
    original = storage_import.video_service.register_keys

    async def with_rejected_policy(db, project_id, keys, _policy, **kw):
        """DB가 거절하는 정책 값으로 등록해 세션을 실패 상태로 만든다."""
        return await original(db, project_id, keys, "review_required", **kw)

    monkeypatch.setattr(storage_import.video_service, "register_keys", with_rejected_policy)
    pid = await _new_project(client, admin_token)
    batch_id = uuid.UUID((await _queue(client, admin_token, pid)).json()["batch_id"])
    for _ in range(storage_import._MAX_ERRORS):
        async with SessionLocal() as s:
            await storage_import.run_pending_imports(s)
    async with SessionLocal() as s:
        batch = await s.get(IngestBatch, batch_id)
        assert batch.import_status == "failed"
        assert batch.import_error
        left = (await s.scalars(select(Video).where(Video.upload_batch_id == batch_id))).all()
    assert not left
