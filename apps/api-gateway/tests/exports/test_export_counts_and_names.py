"""내보내기 이력의 수·파일 이름과 패키지의 수정 횟수를 보는 테스트.

같은 용어는 같은 수를 가리켜야 한다.
이력 행의 `납품 불가`는 완료 목록·내보내기 창의 `납품 불가`와 같은 정의(관문 제외)이고, 품질검수 보류는 따로 센다.
같은 분에 만든 두 패키지는 이름으로 구별돼야 한다.
"""
import json
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

from api_gateway.database import SessionLocal
from api_gateway.models import Export, Review, User, WorkItem
from api_gateway.services.exports.export_service import process_pending
from api_gateway.services.media import storage
from httpx import AsyncClient
from sqlalchemy import select, update
from tests import _storage
from tests._helpers import new_project, seed_clip_work, upload_video

_CAPTION = (
    "서울 도심에서 시민들이 우산을 쓰고 출근길을 서두르고 있다. 기상청은 오후까지 비가 이어질"
    " 것으로 내다봤다. 퇴근길 교통 혼잡도 예상된다."
)


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _approve(work_item_id: str, verdicts: list[str]) -> None:
    """작업을 승인 상태로 바꾸고 판정 기록을 차례로 남긴다. 마지막 판정이 승인이어야 한다."""
    async with SessionLocal() as s:
        wi = await s.get(WorkItem, UUID(work_item_id))
        wi.status = "approved"
        wi.payload = {"caption": {"brief": _CAPTION}}
        reviewer = await s.scalar(select(User.user_id).limit(1))
        for v in verdicts:
            s.add(Review(work_item_id=wi.work_item_id, reviewer_id=reviewer, verdict=v))
        await s.commit()


async def _flush() -> None:
    """beat가 부르는 내보내기 처리를 바로 돈다."""
    async with SessionLocal() as s:
        await process_pending(s, limit=10)


async def _package(export_id: str) -> ZipFile:
    """버킷에 올라간 패키지를 그대로 연다."""
    async with SessionLocal() as s:
        key = (await s.get(Export, UUID(export_id))).manifest_s3_key
    return ZipFile(BytesIO(storage.get_object_body(bucket=_storage.BUCKET, key=key).read()))


async def test_same_minute_exports_have_distinct_filenames(client: AsyncClient, admin_token: str):
    """연달아 만든 두 내보내기의 파일 이름이 다른지 확인한다."""
    pid = await new_project(client, admin_token, "expname")
    ids = [
        (await client.post(f"/projects/{pid}/exports", headers=_h(admin_token))).json()[
            "export_id"
        ]
        for _ in range(2)
    ]
    await _flush()
    rows = (await client.get(f"/projects/{pid}/exports", headers=_h(admin_token))).json()
    names = {r["export_id"]: r["filename"] for r in rows if r["export_id"] in ids}
    assert len(set(names.values())) == 2, names
    for eid, name in names.items():
        assert eid[:8] in name


async def test_history_splits_gate_exclusions_from_qc_held(client: AsyncClient, admin_token: str):
    """이력 행이 관문 제외(`excluded_count`)와 품질검수 보류(`qc_held_count`)를 나눠 싣는지 확인한다.

    둘의 합이 패키지의 `excluded`다. 완료 요약의 `total - deliverable_total`이 `excluded_count`와 같다.
    """
    pid = await new_project(client, admin_token, "exphold")
    vid = await upload_video(client, admin_token, pid)
    _c1, ok = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    _c2, held = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    await _approve(ok, ["approve"])
    await _approve(held, ["approve"])
    async with SessionLocal() as s:
        await s.execute(update(WorkItem).where(WorkItem.work_item_id == UUID(held))
                        .values(qc_status="held"))
        await s.commit()

    summary = (await client.get(f"/projects/{pid}/completed/summary", headers=_h(admin_token))).json()
    eid = (await client.post(f"/projects/{pid}/exports", headers=_h(admin_token))).json()["export_id"]
    await _flush()
    row = next(
        r for r in (await client.get(f"/projects/{pid}/exports", headers=_h(admin_token))).json()
        if r["export_id"] == eid
    )
    assert row["status"] == "completed", row
    assert row["item_count"] == 1
    assert row["excluded_count"] == summary["total"] - summary["deliverable_total"] == 0
    assert row["qc_held_count"] == summary["qc_held_total"] == 1
    with await _package(eid) as z:
        report = json.loads(z.read("report.json"))
    assert report["excluded"] == row["excluded_count"] + row["qc_held_count"]


async def test_package_revision_count_counts_review_fixes(client: AsyncClient, admin_token: str):
    """`review.revision_count`가 반려와 수정 후 승인의 수이고 한 번에 승인된 클립은 0인지 확인한다."""
    pid = await new_project(client, admin_token, "exprev")
    vid = await upload_video(client, admin_token, pid)
    c_once, once = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    c_twice, twice = await seed_clip_work(pid, vid, start=10.0, end=20.0)
    c_edit, edited = await seed_clip_work(pid, vid, start=20.0, end=30.0)
    await _approve(once, ["approve"])
    await _approve(twice, ["reject", "reject", "approve"])
    await _approve(edited, ["modify_and_approve"])

    eid = (await client.post(f"/projects/{pid}/exports", headers=_h(admin_token))).json()["export_id"]
    await _flush()
    with await _package(eid) as z:
        got = {
            cid: json.loads(z.read(f"metadata/{cid}.json"))["review"]["revision_count"]
            for cid in (c_once, c_twice, c_edit)
        }
    assert got == {c_once: 0, c_twice: 2, c_edit: 1}
