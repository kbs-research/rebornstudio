"""선택 항목 제외(`omit_optional`) 테스트. 메타 파일의 키, 색인 체크섬, 리포트·카드·README를 확인한다.

옵션은 메타 파일을 쓸 때만 키를 뺀다. 판정(납품 대상·제외)은 옵션과 무관해야 한다.
받는 쪽은 키가 없는 이유를 패키지 안에서 알 수 있어야 한다.
"""
import json
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import yaml
from api_gateway.database import SessionLocal
from api_gateway.models import DomainEvent
from api_gateway.services.exports.export_package import (
    FIELD_LABEL,
    OPTIONAL_LABEL_FIELDS,
    write_package,
)
from httpx import AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import select
from tests._helpers import new_project, seed_clip_work, upload_video
from tests.exports.test_exports import (
    _NOW,
    _PAYLOAD,
    _approve,
    _built_package,
    _clip,
    _flush_pending,
    _video,
    _work,
)

_FIVE = {"description", "keywords", "speakers", "transcript", "scene_summary"}


def _rows():
    """채운 클립 하나와 캡션이 짧아 막히는 클립 하나를 만든다."""
    v = _video()
    ok = (_work(_PAYLOAD), v, _clip(start=0.0, end=10.0))
    bad = (_work({"caption": {"brief": "짧다"}}), v, _clip(start=10.0, end=20.0))
    return [ok, bad], {ok[0].work_item_id: _NOW, bad[0].work_item_id: _NOW}


def _metas(z: ZipFile) -> dict[str, dict]:
    """패키지의 메타 파일을 이름별로 읽는다."""
    return {n: json.loads(z.read(n)) for n in z.namelist() if n.startswith("metadata/")}


def test_omitted_set_is_the_five_optional_label_keys():
    """빼는 키 집합이 선택 항목 다섯 개이고 모두 화면 이름이 있으며 시각과 비식별화는 남는지 확인한다."""
    assert set(OPTIONAL_LABEL_FIELDS) == _FIVE
    assert all(k in FIELD_LABEL for k in OPTIONAL_LABEL_FIELDS)
    assert not OPTIONAL_LABEL_FIELDS & {"start_time", "end_time", "duration", "deidentification"}


def test_option_off_keeps_the_optional_keys(tmp_path: Path):
    """옵션을 끄면 다섯 키가 모두 실리고 리포트가 옵션이 꺼졌다고 적는지 확인한다."""
    rows, approved = _rows()
    out = tmp_path / "p.zip"
    report = write_package(rows, approved, out, project_name="t")
    with ZipFile(out) as z:
        metas = _metas(z)
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
        readme = z.read("README.md").decode()
    assert len(metas) == 1
    assert set(next(iter(metas.values()))) >= _FIVE
    assert report["omit_optional"] is False and report["omitted_fields"] == []
    assert "fill_rates" not in report
    assert card["omit_optional"] is False and "omitted_fields" not in card
    assert "선택 항목 제외" not in readme


def test_option_on_drops_only_the_optional_keys(tmp_path: Path):
    """옵션을 켜면 다섯 키만 빠지고 나머지 키와 값은 옵션을 끈 패키지와 같은지 확인한다."""
    rows, approved = _rows()
    off, on = tmp_path / "off.zip", tmp_path / "on.zip"
    r_off = write_package(rows, approved, off, project_name="t")
    r_on = write_package(rows, approved, on, project_name="t", omit_optional=True)
    with ZipFile(off) as z:
        m_off = _metas(z)
    with ZipFile(on) as z:
        m_on = _metas(z)

    assert m_on.keys() == m_off.keys()
    for name, doc in m_on.items():
        assert not _FIVE & set(doc), f"{name}에 선택 항목 키가 남았다"
        assert doc == {k: v for k, v in m_off[name].items() if k not in _FIVE}
    # 판정은 옵션과 무관하다. 짧은 캡션은 두 패키지 모두에서 같은 사유로 빠진다.
    for k in ("included", "excluded", "excluded_by_reason", "exclusions"):
        assert r_on[k] == r_off[k], k


def test_option_on_checksums_match_the_written_bytes(tmp_path: Path):
    """색인의 체크섬을 키를 뺀 뒤의 바이트로 계산하는지 확인한다. 받는 쪽이 파일과 대조하면 맞아야 한다."""
    import xxhash

    rows, approved = _rows()
    out = tmp_path / "p.zip"
    write_package(rows, approved, out, project_name="t", omit_optional=True)
    with ZipFile(out) as z:
        lines = [json.loads(x) for x in z.read("manifest.jsonl").decode().splitlines() if x]
        assert lines
        for line in lines:
            body = z.read(line["metadata_path"])
            assert line["checksum"] == f"xxh64:{xxhash.xxh64(body).hexdigest()}"


def test_option_on_records_pass_the_packaged_schema(tmp_path: Path):
    """키가 없는 레코드도 패키지에 든 스키마를 통과하는지 확인한다. 다섯 항목은 스키마에서 선택 항목이다."""
    rows, approved = _rows()
    out = tmp_path / "p.zip"
    write_package(rows, approved, out, project_name="t", omit_optional=True)
    with ZipFile(out) as z:
        schema = json.loads(z.read("schema/clip-metadata-v1.json"))
        metas = _metas(z)
    assert not _FIVE & set(schema.get("required", []))
    validator = Draft202012Validator(schema)
    for name, doc in metas.items():
        errs = [f"{e.json_path}: {e.message}" for e in validator.iter_errors(doc)]
        assert not errs, f"{name}: {errs}"


def test_option_on_is_written_into_report_card_and_readme(tmp_path: Path):
    """옵션과 뺀 키를 리포트·카드·README에 적는지 확인한다."""
    rows, approved = _rows()
    out = tmp_path / "p.zip"
    report = write_package(rows, approved, out, project_name="t", omit_optional=True)
    with ZipFile(out) as z:
        on_disk = json.loads(z.read("report.json"))
        card = yaml.safe_load(z.read("dataset_card.yaml").decode())
        readme = z.read("README.md").decode()

    assert on_disk == report
    assert report["omit_optional"] is True
    assert report["omitted_fields"] == sorted(_FIVE)
    assert "fill_rates" not in report
    assert card["omit_optional"] is True and card["omitted_fields"] == sorted(_FIVE)
    assert "선택 항목 제외" in readme
    for k in _FIVE:
        assert f"`{k}`" in readme


async def test_queued_option_row_is_built_and_new_requests_ignore_it(
    client: AsyncClient, admin_token: str,
):
    """새 요청은 선택 항목 제외를 받지 않고, 이미 큐에 있는 행은 그 설정대로 만들어지는지 확인한다."""
    from api_gateway.models import Export

    pid = await new_project(client, admin_token, "omitopt")
    vid = await upload_video(client, admin_token, pid)
    cid, wid = await seed_clip_work(pid, vid, start=0.0, end=10.0)
    await _approve(wid, _PAYLOAD)
    auth = {"Authorization": f"Bearer {admin_token}"}

    plain = (await client.post(f"/projects/{pid}/exports?omit_optional=true", headers=auth)).json()
    assert plain["omit_optional"] is False, "새 요청은 선택 항목 제외를 받지 않는다"
    async with SessionLocal() as s:
        ev = (await s.execute(select(DomainEvent).where(
            DomainEvent.event_type == "export.created",
            DomainEvent.target_id == UUID(plain["export_id"]),
        ))).scalar_one()
    assert ev.payload["omit_optional"] is False

    me = (await client.get("/auth/me", headers=auth)).json()["user_id"]
    async with SessionLocal() as s:
        row = Export(project_id=UUID(pid), created_by=UUID(me), status="pending", omit_optional=True)
        s.add(row)
        await s.commit()
        body = {"export_id": str(row.export_id)}

    await _flush_pending()
    rows = {e["export_id"]: e for e in (await client.get(f"/projects/{pid}/exports", headers=auth)).json()}
    assert rows[body["export_id"]]["omit_optional"] is True

    with await _built_package(body["export_id"]) as z:
        doc = json.loads(z.read(f"metadata/{cid}.json"))
        report = json.loads(z.read("report.json"))
    assert not _FIVE & set(doc)
    assert doc["caption"] and "deidentification" in doc
    assert report["omit_optional"] is True
    with await _built_package(plain["export_id"]) as z:
        assert set(json.loads(z.read(f"metadata/{cid}.json"))) >= _FIVE
