"""반입 상태 분류(ingest_bucket), 커서 페이지네이션, 상태별 집계 테스트.

규칙 하나를 확인한다.
목록 필터, 행 표시, 배지 집계가 모두 같은 SQL CASE를 쓴다.
화면이 같은 판정을 따로 구현하면 라벨링 대기를 과다 계상하거나 티켓 없는 영상을 '분석 대기'로 표시한다.
그래서 "필터로 받은 목록의 ingest_bucket이 모두 그 값" 같은 단언도 일부러 남긴다.
"""
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import Video, WorkItem
from httpx import AsyncClient
from sqlalchemy import delete, update
from tests._helpers import new_project, seed_clip_work, upload_video


async def _get(client: AsyncClient, token: str, url: str):
    """GET 요청을 보내고 200인지 확인한 뒤 본문을 돌려준다."""
    r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


async def _drop_ticket(video_id: str) -> None:
    """영상의 디스패치 티켓(클립 없는 work_item)을 지운다."""
    async with SessionLocal() as s:
        await s.execute(
            delete(WorkItem).where(
                WorkItem.video_id == UUID(video_id), WorkItem.clip_id.is_(None)
            )
        )
        await s.commit()


async def test_buckets_shape_and_basic_split(client: AsyncClient, admin_token: str):
    """검증을 통과한 영상은 분석 대기, 검증 전 영상은 준비 중이다. 합계는 total과 맞는다."""
    pid = await new_project(client, admin_token, "buk")
    await upload_video(client, admin_token, pid, title="b1", policy="pre_cleared", probed=True)
    await upload_video(client, admin_token, pid, title="b2")

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    assert "confirm" not in c
    assert c["ready"] == 1
    assert c["preparing"] == 1
    assert c["total"] == 2
    assert (
        c["preparing"] + c["ready"] + c["analyzing"] + c["done"] + c["blocked"] == c["total"]
    )


async def test_bucket_filter_matches_row_bucket(client: AsyncClient, admin_token: str):
    """필터로 받은 목록의 행이 모두 그 bucket인지 확인한다. 목록과 배지가 같은 규칙을 써야 같다."""
    pid = await new_project(client, admin_token, "buk-filter")
    await upload_video(client, admin_token, pid, title="f1", policy="pre_cleared", probed=True)
    await upload_video(client, admin_token, pid, title="f2")

    ready = await _get(client, admin_token, f"/projects/{pid}/videos?bucket=ready")
    assert [v["title"] for v in ready] == ["f1"]
    assert all(v["ingest_bucket"] == "ready" for v in ready)

    preparing = await _get(client, admin_token, f"/projects/{pid}/videos?bucket=preparing")
    assert [v["title"] for v in preparing] == ["f2"]
    assert all(v["ingest_bucket"] == "preparing" for v in preparing)


async def test_cursor_pagination_no_overlap(client: AsyncClient, admin_token: str):
    """limit과 (created_at, video_id) 커서로 끊어도 겹치거나 빠지지 않는다.

    같은 초에 생성된 영상이 흔해서 created_at만 쓰는 커서는 항목을 건너뛴다.
    그래서 id를 tie-breaker로 쓰는지 확인한다.
    """
    pid = await new_project(client, admin_token, "buk-page")
    for i in range(5):
        await upload_video(client, admin_token, pid, title=f"p{i}", policy="pre_cleared")

    seen: list[str] = []
    url = f"/projects/{pid}/videos?limit=2"
    for _ in range(5):
        page = await _get(client, admin_token, url)
        if not page:
            break
        seen += [v["video_id"] for v in page]
        last = page[-1]
        url = (
            f"/projects/{pid}/videos?limit=2"
            f"&before={last['created_at']}&before_id={last['video_id']}"
        )

    assert len(seen) == 5
    assert len(set(seen)) == 5  # 겹치는 항목이 없다


async def test_batch_scopes_list_and_counts(client: AsyncClient, admin_token: str):
    """반입 배치로 좁히면 목록과 집계 모두 그 묶음만 센다. 배너의 수와 목록이 같아야 한다."""
    pid = await new_project(client, admin_token, "buk-batch")
    # 묶음 id는 프로젝트마다 새로 만든다. 다른 프로젝트가 만든 묶음 id는 등록이 404로 거절한다.
    batch = str(uuid4())
    vid = (await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "in-batch", "copyright_policy": "pre_cleared", "upload_batch_id": batch},
    )).json()["video_id"]
    await upload_video(client, admin_token, pid, title="out-batch", policy="pre_cleared")

    page = await _get(client, admin_token, f"/projects/{pid}/videos?batch={batch}")
    assert [v["video_id"] for v in page] == [vid]

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets?batch={batch}")
    assert c["total"] == 1


async def test_missing_ticket_is_blocked(client: AsyncClient, admin_token: str):
    """티켓 없는 통과 영상은 막힘으로 세고 요청 대기에서 뺀다. 행과 집계가 같은 분류를 쓴다."""
    pid = await new_project(client, admin_token, "buk-blocked")
    vid = await upload_video(client, admin_token, pid, title="stuck", policy="pre_cleared", probed=True)
    await _drop_ticket(vid)

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    assert c["blocked"] == 1
    assert c["ready"] == 0

    rows = await _get(client, admin_token, f"/projects/{pid}/videos?bucket=blocked")
    assert [v["video_id"] for v in rows] == [vid]


async def test_clips_make_done(client: AsyncClient, admin_token: str):
    """클립이 생기면 완료로 분류하고 행과 집계 양쪽에 같은 분류로 싣는다."""
    pid = await new_project(client, admin_token, "buk-done")
    vid = await upload_video(client, admin_token, pid, title="analyzed", policy="pre_cleared")
    await seed_clip_work(pid, vid, rights_status="legal_review")
    await seed_clip_work(pid, vid, rights_status="auto_cleared", start=5.0, end=10.0)

    rows = await _get(client, admin_token, f"/projects/{pid}/videos?bucket=done")
    assert len(rows) == 1
    assert rows[0]["clip_count"] == 2

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    assert c["done"] == 1


async def test_rejected_probe_is_blocked(client: AsyncClient, admin_token: str):
    """규격 위반 영상은 막힘으로 센다."""
    pid = await new_project(client, admin_token, "buk-probe")
    vid = await upload_video(client, admin_token, pid, title="bad-spec", policy="pre_cleared")
    async with SessionLocal() as s:
        await s.execute(
            update(Video).where(Video.video_id == UUID(vid)).values(probe_status="rejected")
        )
        await s.commit()

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    assert c["blocked"] == 1


async def test_soft_deleted_excluded_everywhere(client: AsyncClient, admin_token: str):
    """소프트 삭제한 영상을 목록과 집계에서 뺀다."""
    pid = await new_project(client, admin_token, "buk-del")
    vid = await upload_video(client, admin_token, pid, title="gone", policy="pre_cleared")
    await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"})

    assert await _get(client, admin_token, f"/projects/{pid}/videos") == []
    assert (await _get(client, admin_token, f"/projects/{pid}/videos/buckets"))["total"] == 0


async def test_default_limit_caps_page(client: AsyncClient, admin_token: str):
    """limit으로 페이지를 실제로 끊는지 확인한다. 전체 조회로 되돌아가지 않는다."""
    pid = await new_project(client, admin_token, "buk-limit")
    for i in range(3):
        await upload_video(client, admin_token, pid, title=f"l{i}", policy="pre_cleared")

    assert len(await _get(client, admin_token, f"/projects/{pid}/videos?limit=1")) == 1
    assert len(await _get(client, admin_token, f"/projects/{pid}/videos?limit=200")) == 3


async def test_done_video_reports_where_its_clips_are(
    client: AsyncClient, admin_token: str
):
    """분석이 끝난 영상은 클립이 지금 어느 단계에 있는지 싣는다.

    단계별 수가 없으면 화면이 완료 행의 다음 단계를 판단할 수 없다.
    """
    pid = await new_project(client, admin_token, "buk-where")
    vid = await upload_video(client, admin_token, pid, title="done-v", policy="pre_cleared")
    # 라벨링 대기 2건과 검수 대기 1건을 만든다. 클립은 기본값 `auto_cleared`로 시드한다.
    await seed_clip_work(pid, vid, start=0, end=5)
    await seed_clip_work(pid, vid, start=5, end=10)
    _, third = await seed_clip_work(pid, vid, start=10, end=15)
    async with SessionLocal() as s:
        await s.execute(
            update(WorkItem).where(WorkItem.work_item_id == UUID(third)).values(status="submitted")
        )
        await s.commit()

    row = next(
        v for v in await _get(client, admin_token, f"/projects/{pid}/videos") if v["video_id"] == vid
    )
    assert row["ingest_bucket"] == "done"
    assert row["labeling_clips"] == 2
    assert row["reviewing_clips"] == 1
    assert row["approved_clips"] == 0


async def test_ingest_batch_becomes_a_record(client: AsyncClient, admin_token: str):
    """반입 묶음마다 행을 만든다. 목록으로 찾을 수 있고 상태 분포를 싣는다.

    `upload_batch_id`가 클라이언트가 만든 UUID일 뿐이면 반입 직후 URL을 잃었을 때 그 묶음을 다시 찾을 수 없다.
    """
    from uuid import uuid4
    pid = await new_project(client, admin_token, "batch-rec")
    batch = str(uuid4())
    for i in range(3):
        await upload_video(
            client, admin_token, pid, title=f"b{i}", policy="pre_cleared", batch=batch,
            probed=True,
        )

    rows = await _get(client, admin_token, f"/projects/{pid}/ingest-batches")
    row = next(b for b in rows if b["batch_id"] == batch)
    assert row["video_count"] == 3
    assert row["source"] == "upload"
    assert row["label"] is None, "이름은 비어 있어도 된다 — 화면이 시각·출처로 만들어 쓴다"
    # 분포는 목록 필터와 같은 규칙을 쓴다. pre_cleared 3건은 모두 분석 대기다.
    assert row["ready"] == 3
    assert "confirm" not in row

    # 이름·메모를 달면 그 값을 그대로 돌려준다.
    r = await client.patch(
        f"/ingest-batches/{batch}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"label": "7월 4주 샘플 방송 반입", "note": "계약 A"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "7월 4주 샘플 방송 반입"
    assert r.json()["video_count"] == 3


async def test_video_uploaded_without_batch_id_gets_a_job(
    client: AsyncClient, admin_token: str
):
    """배치 id를 보내지 않아도 Job을 만든다.

    Job 보드는 `upload_batch_id IS NOT NULL`로 거른다.
    그래서 배치 id가 없는 영상은 보드에서 빠지고 목록과 배지에서만 세어진다.
    그러면 한 화면이 "영상 1건"과 "Job 없음"을 동시에 표시한다.
    등록 한 번이 Job 하나이고 id를 보내지 않은 등록도 등록이므로, 이 Job 행은 묶음 목록의 정상 항목이다.
    """
    pid = await new_project(client, admin_token, "batch-none")
    await upload_video(client, admin_token, pid, title="solo", policy="pre_cleared")

    jobs = await _get(client, admin_token, f"/projects/{pid}/ingest-batches")
    assert len(jobs) == 1, "배치 id 없이 올린 영상이 Job 보드에서 사라진다"
    assert jobs[0]["video_count"] == 1
    assert jobs[0]["source"] == "upload"


async def _set_video(video_id: str, **values) -> None:
    """영상 상태를 직접 바꾼다. API로 만들 수 없는 막힘(변환 실패, 규격 거부)을 만들 때 쓴다."""
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(video_id)).values(**values))
        await s.commit()


async def test_bucket_counts_issues_match_select_total(
    client: AsyncClient, admin_token: str
):
    """이슈 분해 수가 같은 조건의 `select`가 돌려주는 `total`과 같은지 확인한다.

    화면의 일괄 버튼은 이 수를 이름에 적고(`검증 재검사 3건`) 대상은 `select`로 가져온다.
    두 수가 다르면 버튼이 이름에 적힌 만큼 처리하지 않는다(분모 불일치).
    """
    pid = await new_project(client, admin_token, "buk-issues")
    v_tc = await upload_video(client, admin_token, pid, title="i-tc")
    await _set_video(v_tc, transcode_status="failed")
    v_tc2 = await upload_video(client, admin_token, pid, title="i-tc2")
    await _set_video(v_tc2, transcode_status="failed")
    v_probe = await upload_video(client, admin_token, pid, title="i-probe")
    await _set_video(v_probe, probe_status="rejected")
    await upload_video(client, admin_token, pid, title="i-ok")

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    issues = {i["stage"]: i["count"] for i in c["issues"]}
    assert issues == {"transcode": 2, "probe": 1}
    # 분해의 합은 blocked와 같다. 한쪽만 맞으면 화면이 "이슈 3건"과 "조치할 것 2건"처럼 다르게 표시한다.
    assert sum(issues.values()) == c["blocked"] == 3

    for stage, n in issues.items():
        r = await client.post(
            f"/projects/{pid}/videos/select",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"blocked_stage": stage},
        )
        assert r.status_code == 200, r.text
        assert r.json()["total"] == n, f"{stage}: 버튼 이름의 수와 대상 수가 다르다"
        assert len(r.json()["video_ids"]) == n


async def test_bucket_counts_issues_empty_when_nothing_blocked(
    client: AsyncClient, admin_token: str
):
    """막힌 영상이 없으면 이슈 분해를 비운다. 0인 줄을 만들면 화면이 필요 없는 버튼을 표시한다."""
    pid = await new_project(client, admin_token, "buk-issues-none")
    await upload_video(client, admin_token, pid, title="clean")

    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    assert c["issues"] == []
    assert c["blocked"] == 0


async def test_upload_limits_tells_cap_and_extensions(
    client: AsyncClient, admin_token: str
):
    """등록 시트가 파일을 받기 전에 조회하는 업로드 상한과 확장자를 돌려주는지 확인한다.

    확장자 목록은 서버 `_VIDEO_EXTS`와 같아야 한다.
    다르면 화면이 받은 파일을 서버가 거절하거나 그 반대가 된다.
    """
    from api_gateway.services.media.video import _VIDEO_EXTS

    pid = await new_project(client, admin_token, "limits")
    lim = await _get(client, admin_token, f"/projects/{pid}/videos/upload-limits")
    assert lim["max_bytes"] > 0
    assert set(lim["video_extensions"]) == _VIDEO_EXTS


async def test_bucket_spans_sum_lengths_past_the_page(client: AsyncClient, admin_token: str):
    """분류별 길이 합을 목록 페이지와 무관한 전체 합으로 계산한다. 길이가 없는 영상은 따로 센다.

    길이가 없는 영상은 분석 요청 게이트가 막으므로 막힘에서 센다.
    화면이 실린 행으로 더하면 커서 뒤의 영상이 빠져 요청 대기 총 길이와 예상 비용이 작게 표시된다.
    """
    from datetime import UTC, datetime, timedelta

    from api_gateway.models import AnalysisJob

    pid = await new_project(client, admin_token, "buk-span")
    ready = [await upload_video(client, admin_token, pid, title=f"s{i}") for i in range(3)]
    busy = [await upload_video(client, admin_token, pid, title=f"a{i}") for i in range(2)]
    early = datetime.now(UTC) - timedelta(hours=3)
    async with SessionLocal() as s:
        for vid, secs in zip(ready, (60.0, 90.5, None), strict=True):
            await s.execute(
                update(Video).where(Video.video_id == UUID(vid))
                .values(duration_seconds=secs, probe_status="passed")
            )
        for vid, at in zip(busy, (early, early + timedelta(hours=1)), strict=True):
            await s.execute(
                update(Video).where(Video.video_id == UUID(vid)).values(duration_seconds=30.0)
            )
            s.add(AnalysisJob(
                project_id=UUID(pid), video_id=UUID(vid), adapter_key="maiu",
                status="running", stage="analyzing", attempt=1, created_at=at,
            ))
        await s.commit()

    # 한 행짜리 페이지로 불러도 합은 전체 기준이다.
    page = await _get(client, admin_token, f"/projects/{pid}/videos?bucket=ready&limit=1")
    assert len(page) == 1
    c = await _get(client, admin_token, f"/projects/{pid}/videos/buckets")
    spans = {x["bucket"]: x for x in c["spans"]}
    assert set(spans) == {"preparing", "ready", "analyzing", "done", "blocked"}
    assert c["ready"] == 2 and c["blocked"] == 1 and c["analyzing"] == 2
    assert spans["ready"]["total_seconds"] == 150.5
    assert spans["ready"]["unmeasured"] == 0
    assert spans["blocked"]["unmeasured"] == 1
    assert spans["ready"]["oldest_requested_at"] is None
    assert spans["analyzing"]["total_seconds"] == 60.0
    oldest = datetime.fromisoformat(spans["analyzing"]["oldest_requested_at"])
    assert abs((oldest - early).total_seconds()) < 1
    assert spans["done"] == {
        "bucket": "done", "total_seconds": 0.0, "unmeasured": 0, "oldest_requested_at": None,
    }
