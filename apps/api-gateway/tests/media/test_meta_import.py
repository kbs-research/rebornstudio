"""등록 묶음 영상 메타 일괄 입력(양식·미리 보기·반영) 테스트."""
import io
import unicodedata
from datetime import UTC, datetime
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent, Video
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlalchemy import select, update
from tests._helpers import add_member, new_project, upload_video

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _h(tok: str) -> dict:
    """인증 헤더를 만든다."""
    return {"Authorization": f"Bearer {tok}"}


async def _set(vid: str, **values) -> None:
    """영상 행의 값을 DB에서 직접 바꾼다. `extra`는 통째로 넣는다."""
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(**values))
        await s.commit()


async def _video(vid: str) -> Video:
    """영상 행을 읽는다."""
    async with SessionLocal() as s:
        return await s.get(Video, UUID(vid))


async def _batch(client: AsyncClient, tok: str, titles: list[str]) -> tuple[str, str, list[str]]:
    """새 프로젝트에 한 묶음으로 영상을 올리고 (프로젝트 ID, 묶음 ID, 영상 ID 목록)을 돌려준다."""
    pid = await new_project(client, tok, "bmeta")
    batch = str(uuid4())
    vids = [await upload_video(client, tok, pid, title=t, batch=batch) for t in titles]
    return pid, batch, vids


async def _preview(client: AsyncClient, tok: str, batch: str, data: bytes, name: str = "m.csv"):
    """미리 보기를 부른다."""
    return await client.post(
        f"/ingest-batches/{batch}/meta-import/preview", headers=_h(tok),
        files={"file": (name, data, "text/csv" if name.endswith(".csv") else XLSX)},
    )


async def test_template_lists_every_batch_video_with_current_values(
    client: AsyncClient, admin_token: str,
):
    """양식이 묶음의 영상만 모두 싣고 지금 메타와 파일 경로를 채우는지 확인한다."""
    pid, batch, vids = await _batch(client, admin_token, ["B-영상", "A-영상"])
    other = await upload_video(client, admin_token, pid, title="다른 묶음", batch=str(uuid4()))
    await _set(vids[0], broadcaster="샘플 방송", episode="007", extra={"key": "보도/B.mp4"})
    r = await client.get(f"/ingest-batches/{batch}/meta-template", headers=_h(admin_token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(XLSX)
    assert r.headers["content-disposition"].endswith(f'-batch-{batch[:8]}-meta.xlsx"')
    ws = load_workbook(io.BytesIO(r.content))["메타"]
    body = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    assert [row[2] for row in body] == ["A-영상", "B-영상"], "제목순으로 싣는다"
    assert other not in [row[0] for row in body]
    b = next(row for row in body if row[0] == vids[0])
    assert b[1] == "보도/B.mp4" and b[3] == "샘플 방송" and b[6] == "007"


async def test_preview_matches_by_id_file_and_title(client: AsyncClient, admin_token: str):
    """영상 ID, 파일(경로·파일 이름·NFD 이름), 제목 순서로 영상을 찾고 바뀌는 값을 돌려주는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["첫째", "둘째", "셋째", "넷째"])
    await _set(vids[1], extra={"key": "보도/SAMPLE_0001.mp4"})
    await _set(vids[2], extra={"key": "x/뉴스.mp4", "original_filename": "뉴스.mp4"})
    nfd = unicodedata.normalize("NFD", "뉴스.mp4")
    csv = (
        "영상 ID,파일,제목,방송사,방송일,회차\n"
        f"{vids[0]},,새 제목,샘플 방송,2026-05-28,001\n"
        ",SAMPLE_0001.mp4,,예시 방송,,\n"
        f",{nfd},,견본 방송,,\n"
        ",,넷째,,20260101,\n"
    ).encode("utf-8-sig")
    r = await _preview(client, admin_token, batch, csv)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {x["row"]: x for x in body["rows"]}
    assert [rows[i]["matched_by"] for i in (2, 3, 4, 5)] == ["video_id", "file", "file", "title"]
    assert [rows[i]["video_id"] for i in (2, 3, 4, 5)] == vids
    first = {c["field"]: c for c in rows[2]["changes"]}
    assert first["title"] == {"field": "title", "before": "첫째", "after": "새 제목"}
    assert first["aired_at"]["after"] == "2026-05-28T00:00:00+09:00"
    assert first["episode"]["after"] == "001"
    assert all(x["status"] == "changed" for x in body["rows"])
    assert body["counts"]["changed"] == 4 and body["batch_video_count"] == 4
    assert body["matched_video_count"] == 4
    assert (await _video(vids[0])).title == "첫째", "미리 보기는 저장하지 않는다"


async def test_preview_matches_the_path_shown_on_screen(client: AsyncClient, admin_token: str):
    """묶음 화면에 보이는 경로 뒤 두 단계로도 찾고, 두 영상의 뒷부분이 같으면 고르지 않는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["가", "나", "다"])
    await _set(vids[0], extra={"key": "test data/video/SAMPLE_0002.mp4"})
    await _set(vids[1], extra={"key": "a/clip/E_1.mp4"})
    await _set(vids[2], extra={"key": "b/clip/E_1.mp4"})
    csv = (
        "파일,방송사\n"
        "video/SAMPLE_0002.mp4,샘플 방송\n"
        "clip/E_1.mp4,샘플 방송\n"
        "a/clip/E_1.mp4,예시 방송\n"
    ).encode("utf-8-sig")
    r = await _preview(client, admin_token, batch, csv)
    assert r.status_code == 200, r.text
    rows = {x["row"]: x for x in r.json()["rows"]}
    assert rows[2]["video_id"] == vids[0] and rows[2]["matched_by"] == "file"
    assert rows[3]["status"] == "ambiguous", "뒷부분이 같은 두 영상 중 하나를 고르지 않는다"
    assert rows[4]["video_id"] == vids[1]


async def test_preview_never_guesses(client: AsyncClient, admin_token: str):
    """찾지 못한 행, 여러 영상과 같은 제목, 같은 영상을 가리키는 여러 행을 반영 대상에서 빼는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["같은 제목", "같은 제목", "혼자"])
    csv = (
        "영상 ID,제목,방송사\n"
        ",같은 제목,샘플 방송\n"
        f"{uuid4()},,샘플 방송\n"
        "not-a-uuid,,샘플 방송\n"
        ",없는 제목,샘플 방송\n"
        f"{vids[2]},,샘플 방송\n"
        ",혼자,예시 방송\n"
        ",,샘플 방송\n"
    ).encode()
    body = (await _preview(client, admin_token, batch, csv)).json()
    st = {x["row"]: x["status"] for x in body["rows"]}
    assert st == {2: "ambiguous", 3: "unmatched", 4: "unmatched", 5: "unmatched",
                  6: "invalid", 7: "invalid", 8: "unmatched"}
    dup = next(x for x in body["rows"] if x["row"] == 6)
    assert any("6·7행" in e["message"] for e in dup["errors"])
    assert body["counts"] == {"changed": 0, "unchanged": 0, "invalid": 2, "unmatched": 4, "ambiguous": 1}


async def test_preview_validation_and_empty_cells(client: AsyncClient, admin_token: str):
    """빈 셀은 바꾸지 않고, 규칙에 맞지 않는 값은 필드별 오류로, 같은 값은 변경 없음으로 판정하는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["a", "b", "c"])
    await _set(vids[2], broadcaster="샘플 방송",
               aired_at=datetime.fromisoformat("2026-05-28T21:00:00+09:00"))
    long = "가" * 101
    csv = (
        "영상 ID,방송사,방영일,알 수 없는 열\n"
        f"{vids[0]},{long},1900-01-01,x\n"
        f"{vids[1]},,,x\n"
        f"{vids[2]},샘플 방송,2026.05.28,x\n"
    ).encode()
    body = (await _preview(client, admin_token, batch, csv)).json()
    assert body["ignored_columns"] == ["알 수 없는 열"]
    rows = {x["row"]: x for x in body["rows"]}
    errs = {e["field"]: e["message"] for e in rows[2]["errors"]}
    assert rows[2]["status"] == "invalid"
    assert errs["broadcaster"] == "최대 100자까지 넣습니다"
    assert "1920년" in errs["aired_at"]
    assert rows[3]["status"] == "unchanged" and rows[3]["changes"] == []
    assert rows[4]["status"] == "unchanged", "같은 KST 날짜면 시각이 달라도 바꾸지 않는다"


async def test_preview_rejects_bad_files(client: AsyncClient, admin_token: str):
    """크기 초과와 읽지 못하는 파일을 정해진 코드로 거절하는지 확인한다."""
    _, batch, _ = await _batch(client, admin_token, ["a"])
    r = await _preview(client, admin_token, batch, b"x" * (1024 * 1024 + 10))
    assert r.status_code == 413 and r.json()["detail"] == "meta_import_file_too_large"
    r = await _preview(client, admin_token, batch, b"\xd0\xcf\x11\xe0" + b"\0" * 64, "old.xls")
    assert r.status_code == 422 and r.json()["detail"] == "meta_import_unsupported_file"


async def test_meta_import_requires_project_admin(client: AsyncClient, admin_token: str):
    """라벨러·검수자는 양식·미리 보기·반영을 모두 403으로 막고 관리자는 부를 수 있는지 확인한다."""
    pid, batch, vids = await _batch(client, admin_token, ["a"])
    _, annot = await add_member(client, admin_token, pid, "annotator")
    _, rev = await add_member(client, admin_token, pid, "reviewer")
    _, op = await add_member(client, admin_token, pid, "project_admin")
    apply_body = {"items": [{"video_id": vids[0], "fields": {"genre": "보도"}}]}
    for tok, want in ((annot, 403), (rev, 403), (op, 200)):
        assert (await client.get(f"/ingest-batches/{batch}/meta-template", headers=_h(tok))).status_code == want
        assert (await _preview(client, tok, batch, b"title\na\n")).status_code == want
        r = await client.post(f"/ingest-batches/{batch}/meta-import/apply", headers=_h(tok), json=apply_body)
        assert r.status_code == want, r.text
    missing = str(uuid4())
    r = await client.get(f"/ingest-batches/{missing}/meta-template", headers=_h(admin_token))
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


async def test_apply_updates_and_audits(client: AsyncClient, admin_token: str):
    """반영이 값을 저장하고 영상마다 일괄 입력 표시가 붙은 감사 기록과 묶음 기록을 남기는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["a", "b"])
    await _set(vids[1], genre="보도")
    r = await client.post(
        f"/ingest-batches/{batch}/meta-import/apply", headers=_h(admin_token),
        json={"items": [
            {"video_id": vids[0], "fields": {"broadcaster": " 샘플 방송 ", "aired_at": "2026-05-28T00:00:00+09:00"}},
            {"video_id": vids[1], "fields": {"genre": "보도"}},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "unchanged": 1, "changed_fields": 2}
    v = await _video(vids[0])
    assert v.broadcaster == "샘플 방송" and v.aired_at.isoformat().startswith("2026-05-27T15:00:00")
    async with SessionLocal() as s:
        evs = (await s.scalars(select(DomainEvent).where(
            DomainEvent.target_id.in_([UUID(x) for x in vids]),
            DomainEvent.event_type == "video.updated",
        ))).all()
        batch_evs = (await s.scalars(select(DomainEvent).where(
            DomainEvent.target_id == UUID(batch),
            DomainEvent.event_type == "ingest_batch.meta_imported",
        ))).all()
    assert len(evs) == 1 and evs[0].payload["via"] == "bulk_import"
    assert set(evs[0].payload["changed"]) == {"broadcaster", "aired_at"}
    assert len(batch_evs) == 1 and batch_evs[0].payload == {"videos": 1, "changed_fields": 2}


async def test_apply_is_all_or_nothing(client: AsyncClient, admin_token: str):
    """다른 묶음·다른 프로젝트·삭제된 영상이 하나라도 있으면 409로 막고 아무것도 바꾸지 않는지 확인한다."""
    pid, batch, vids = await _batch(client, admin_token, ["a", "b"])
    elsewhere = await upload_video(client, admin_token, pid, title="다른 묶음", batch=str(uuid4()))
    other_pid = await new_project(client, admin_token, "bmeta-x")
    foreign = await upload_video(client, admin_token, other_pid, title="남의 영상")
    await _set(vids[1], deleted_at=datetime.now(UTC))
    for intruder in (elsewhere, foreign, vids[1]):
        r = await client.post(
            f"/ingest-batches/{batch}/meta-import/apply", headers=_h(admin_token),
            json={"items": [
                {"video_id": vids[0], "fields": {"genre": "보도"}},
                {"video_id": intruder, "fields": {"genre": "보도"}},
            ]},
        )
        assert r.status_code == 409 and r.json()["detail"] == "meta_import_video_not_in_batch", r.text
    assert (await _video(vids[0])).genre is None


async def test_apply_rejects_clearing_and_duplicates(client: AsyncClient, admin_token: str):
    """값을 지우는 필드, 빈 변경, 같은 영상 두 번, 범위 밖 방영일을 422로 막는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["a"])
    bodies = [
        {"items": [{"video_id": vids[0], "fields": {"genre": None}}]},
        {"items": [{"video_id": vids[0], "fields": {}}]},
        {"items": [{"video_id": vids[0], "fields": {"genre": "a"}},
                   {"video_id": vids[0], "fields": {"genre": "b"}}]},
        {"items": [{"video_id": vids[0], "fields": {"aired_at": "1900-01-01T00:00:00+09:00"}}]},
        {"items": []},
    ]
    for body in bodies:
        r = await client.post(f"/ingest-batches/{batch}/meta-import/apply", headers=_h(admin_token), json=body)
        assert r.status_code == 422, body
    assert (await _video(vids[0])).genre is None


async def test_preview_then_apply_round_trip(client: AsyncClient, admin_token: str):
    """내려받은 양식을 고쳐 올리고 미리 보기의 변경을 그대로 반영하면 값이 저장되는지 확인한다."""
    _, batch, vids = await _batch(client, admin_token, ["a", "b"])
    r = await client.get(f"/ingest-batches/{batch}/meta-template", headers=_h(admin_token))
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["메타"]
    for row in ws.iter_rows(min_row=2):
        row[3].value = "샘플 방송"
        row[7].value = "2026.09.01"
    buf = io.BytesIO()
    wb.save(buf)
    body = (await _preview(client, admin_token, batch, buf.getvalue(), "m.xlsx")).json()
    assert body["sheet"] == "메타" and body["counts"]["changed"] == 2
    items = [
        {"video_id": x["video_id"], "fields": {c["field"]: c["after"] for c in x["changes"]}}
        for x in body["rows"] if x["status"] == "changed"
    ]
    r = await client.post(f"/ingest-batches/{batch}/meta-import/apply", headers=_h(admin_token),
                          json={"items": items})
    assert r.json()["updated"] == 2
    again = (await _preview(client, admin_token, batch, buf.getvalue(), "m.xlsx")).json()
    assert again["counts"]["unchanged"] == 2, "반영한 뒤 같은 파일은 변경 없음이다"
    assert [(await _video(v)).broadcaster for v in vids] == ["샘플 방송", "샘플 방송"]
