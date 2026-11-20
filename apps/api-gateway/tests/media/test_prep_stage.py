"""준비 여정 축(prep_stage)의 판정·합계 불변식·필터 일치·배치 분포 테스트.

등록 묶음의 단계 칩 숫자와 칩을 눌렀을 때 나오는 결과는 항상 같아야 한다.
그래서 묶음 집계·목록 필터·행 배지가 모두 `_prep_stage_case` 하나를 쓴다.
bucket 축은 기준이 달라서 같은 영상이 두 축에서 다른 값을 가질 수 있다
(예: bucket=analyzing, prep=registering).
"""
from uuid import UUID, uuid4

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Video, WorkItem
from httpx import AsyncClient
from sqlalchemy import delete, update
from tests._helpers import new_project, seed_clip_work, upload_video

_STAGES = (
    "uploading", "probing", "transcoding", "awaiting_analysis",
    "registering", "analyzing", "summarizing", "ready_for_work", "blocked",
)


async def _get(client: AsyncClient, token: str, url: str):
    """GET을 보내고 200인지 확인한 뒤 본문을 돌려준다."""
    r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


async def _prep_counts(client: AsyncClient, token: str, batch: str) -> dict:
    """등록 묶음 상세의 준비 단계 집계를 돌려준다. 단계 칩이 읽는 값이다."""
    return (await _get(client, token, f"/ingest-batches/{batch}"))["prep"]


async def _set_video(video_id: str, **values) -> None:
    """영상 행의 컬럼 값을 바로 바꾼다."""
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(video_id)).values(**values))
        await s.commit()


async def _set_maiu(pid: str, video_id: str, stage: str) -> None:
    """지정한 MI 단계로 job을 만든다. 중립 상태는 코드와 같은 매핑으로 정한다.

    두 필드를 따로 정하면 픽스처가 실제로 생길 수 없는 상태가 된다.
    그러면 테스트가 통과해도 실제 동작을 확인하지 못한다.
    """
    from api_gateway.services.analysis.status_map import neutral_status

    async with SessionLocal() as s:
        s.add(AnalysisJob(
            adapter_key="maiu", video_id=UUID(video_id), project_id=UUID(pid),
            stage=stage, status=neutral_status(stage),
        ))
        await s.commit()


async def test_counts_sum_equals_total(client: AsyncClient, admin_token: str):
    """모든 단계 수의 합이 total과 같은지 확인한다. 분모가 틀리면 스트립 전체가 잘못 표시된다."""
    pid = await new_project(client, admin_token, "prep-sum")
    batch = str(uuid4())
    v_probe = await upload_video(client, admin_token, pid, title="s1", batch=batch)  # probe pending
    await upload_video(client, admin_token, pid, title="s2", batch=batch)
    await _set_video(v_probe, probe_status="pending")

    c = await _prep_counts(client, admin_token, batch)
    assert sum(c[k] for k in _STAGES) == c["total"] == 2


async def test_stage_judgments_across_journey(client: AsyncClient, admin_token: str):
    """여정의 각 지점이 해당 단계로 판정되는지 확인한다.

    업로드 중·검증·변환·요청 대기·MI 3구간·완료를 확인한다.
    """
    pid = await new_project(client, admin_token, "prep-j")
    batch = str(uuid4())

    # upload_video 헬퍼는 uploaded·probe pending 상태까지 만든다. 각 지점으로 옮겨 검증한다.
    v_upl = await upload_video(client, admin_token, pid, title="j-upl", batch=batch)
    await _set_video(v_upl, upload_status="pending")
    v_probe = await upload_video(client, admin_token, pid, title="j-probe", batch=batch)
    await _set_video(v_probe, probe_status="pending")
    v_tc = await upload_video(client, admin_token, pid, title="j-tc", batch=batch)
    await _set_video(v_tc, transcode_status="running")
    # upload 직후에는 probe가 돌지 않아 probing이다. 요청 대기를 만들려면 통과를 지정한다.
    v_wait = await upload_video(client, admin_token, pid, title="j-wait", batch=batch)
    await _set_video(v_wait, probe_status="passed", duration_seconds=60.0)
    v_reg = await upload_video(client, admin_token, pid, title="j-reg", batch=batch)
    await _set_maiu(pid, v_reg, "registering")
    v_ana = await upload_video(client, admin_token, pid, title="j-ana", batch=batch)
    await _set_maiu(pid, v_ana, "analyzing")
    v_sum = await upload_video(client, admin_token, pid, title="j-sum", batch=batch)
    await _set_maiu(pid, v_sum, "summarizing")
    v_done = await upload_video(client, admin_token, pid, title="j-done", batch=batch)
    await _set_maiu(pid, v_done, "done")
    # 클립이 있어야 완료다. 분석 종료와 작업할 클립 존재는 별개 조건이다.
    # 클립이 0인 done은 blocked/no_clips다(아래 전용 테스트).
    await seed_clip_work(pid, v_done)

    rows = await _get(client, admin_token, f"/projects/{pid}/videos?limit=50")
    got = {r["title"]: r["prep_stage"] for r in rows}
    assert got == {
        "j-upl": "uploading", "j-probe": "probing", "j-tc": "transcoding",
        "j-wait": "awaiting_analysis", "j-reg": "registering",
        "j-ana": "analyzing", "j-sum": "summarizing", "j-done": "ready_for_work",
    }

    c = await _prep_counts(client, admin_token, batch)
    assert sum(c[k] for k in _STAGES) == c["total"] == 8
    assert c["ready_for_work"] == 1 and c["transcoding"] == 1


async def test_filter_matches_row_and_counts(client: AsyncClient, admin_token: str):
    """`?prep=` 필터 결과, 그 단계의 행 전부, 집계 수가 같다.

    세 곳이 같은 규칙을 쓰는지 확인한다.
    """
    pid = await new_project(client, admin_token, "prep-f")
    batch = str(uuid4())
    v_tc = await upload_video(client, admin_token, pid, title="f-tc", batch=batch)
    await _set_video(v_tc, transcode_status="pending")
    await upload_video(client, admin_token, pid, title="f-wait", batch=batch)

    rows = await _get(client, admin_token, f"/projects/{pid}/videos?prep=transcoding")
    assert [r["title"] for r in rows] == ["f-tc"]
    assert all(r["prep_stage"] == "transcoding" for r in rows)
    c = await _prep_counts(client, admin_token, batch)
    assert c["transcoding"] == len(rows) == 1


async def test_blocked_carries_location(client: AsyncClient, admin_token: str):
    """막힌 행은 막힌 위치(prep_blocked_stage)를 함께 싣는다. "변환에서 막힘" 표시에 쓴다.

    막히지 않은 행은 None으로 둔다.
    """
    pid = await new_project(client, admin_token, "prep-b")
    v_tc = await upload_video(client, admin_token, pid, title="b-tc")
    await _set_video(v_tc, transcode_status="failed")
    v_probe = await upload_video(client, admin_token, pid, title="b-probe")
    await _set_video(v_probe, probe_status="rejected")
    v_maiu = await upload_video(client, admin_token, pid, title="b-maiu")
    await _set_maiu(pid, v_maiu, "failed")
    v_ok = await upload_video(client, admin_token, pid, title="b-ok")
    await _set_video(v_ok, probe_status="passed", duration_seconds=60.0)

    rows = await _get(client, admin_token, f"/projects/{pid}/videos?limit=50")
    got = {r["title"]: (r["prep_stage"], r["prep_blocked_stage"]) for r in rows}
    assert got["b-tc"] == ("blocked", "transcode")
    assert got["b-probe"] == ("blocked", "probe")
    assert got["b-maiu"] == ("blocked", "analysis")
    assert got["b-ok"] == ("awaiting_analysis", None)


async def test_two_axes_disagree_by_design(client: AsyncClient, admin_token: str):
    """bucket=analyzing이면서 prep=registering인 영상을 정상으로 둔다.

    bucket은 대기 이유를, prep은 진행 위치를 나타낸다. 두 축의 기준이 달라 이 불일치는 정상이다.
    bucket은 MI 요청 뒤를 모두 분석 중으로 묶고, prep은 등록 구간을 따로 나눈다.
    같은 값을 강제하면 한 축이 잘못된 값을 표시한다.
    """
    pid = await new_project(client, admin_token, "prep-axes")
    v_ax = await upload_video(client, admin_token, pid, title="ax")
    await _set_video(v_ax, probe_status="passed", duration_seconds=60.0)
    await _set_maiu(pid, v_ax, "registered")

    row = (await _get(client, admin_token, f"/projects/{pid}/videos"))[0]
    assert row["ingest_bucket"] == "analyzing"
    assert row["prep_stage"] == "registering"

    # 티켓 없는 통과 영상은 bucket=blocked이고 prep도 blocked(ticket 위치)다.
    v = await upload_video(client, admin_token, pid, title="ax2", policy="pre_cleared")
    await _set_video(v, probe_status="passed", duration_seconds=60.0)
    async with SessionLocal() as s:
        await s.execute(
            delete(WorkItem).where(WorkItem.video_id == UUID(v), WorkItem.clip_id.is_(None))
        )
        await s.commit()
    row2 = next(
        r for r in await _get(client, admin_token, f"/projects/{pid}/videos") if r["video_id"] == v
    )
    assert (row2["ingest_bucket"], row2["prep_stage"], row2["prep_blocked_stage"]) == (
        "blocked", "blocked", "ticket",
    )


async def test_analysis_done_without_clips_is_blocked(client: AsyncClient, admin_token: str):
    """분석이 끝났는데 클립이 0개면 막힘으로 판정하고 막힌 위치를 `no_clips`로 둔다.

    `analysis`는 다시 돌려 볼 여지가 있지만 `no_clips`는 다시 돌려도 같은 결과가 나온다.
    클립 수를 보지 않고 `ready_for_work`로 두면 장면을 하나도 내지 못한 영상이 `준비 완료`로 표시된다.
    그러면 그 레인을 연 사람이 작업할 것이 있다고 판단한다.
    """
    pid = await new_project(client, admin_token, "prep-noclip")
    batch = str(uuid4())

    v_empty = await upload_video(client, admin_token, pid, title="n-empty", batch=batch)
    await _set_maiu(pid, v_empty, "done")            # 분석은 끝났고 클립은 없다
    v_ok = await upload_video(client, admin_token, pid, title="n-ok", batch=batch)
    await _set_maiu(pid, v_ok, "done")
    await seed_clip_work(pid, v_ok)                  # 같은 done인데 클립이 있다

    rows = {r["title"]: r for r in await _get(client, admin_token, f"/projects/{pid}/videos")}
    assert (rows["n-empty"]["prep_stage"], rows["n-empty"]["prep_blocked_stage"]) == (
        "blocked", "no_clips",
    ), rows["n-empty"]
    assert rows["n-ok"]["prep_stage"] == "ready_for_work", rows["n-ok"]

    # 집계도 같은 규칙을 쓴다. 단계 칩 숫자와 행 배지가 같아야 한다.
    c = await _prep_counts(client, admin_token, batch)
    assert (c["blocked"], c["ready_for_work"]) == (1, 1), c
    assert sum(c[k] for k in _STAGES) == c["total"] == 2


async def test_clips_without_maiu_row_are_ready_for_work(client: AsyncClient, admin_token: str):
    """MAIU 행이 없어도 클립이 있으면 완료로 판정한다. bucket의 done 판정과 같은 규칙이다."""
    pid = await new_project(client, admin_token, "prep-clips")
    vid = await upload_video(client, admin_token, pid, title="c1")
    await seed_clip_work(pid, vid)

    row = (await _get(client, admin_token, f"/projects/{pid}/videos"))[0]
    assert row["prep_stage"] == "ready_for_work"


async def test_batch_rows_carry_prep_distribution(client: AsyncClient, admin_token: str):
    """묶음 목록의 각 행에 준비 분포가 실린다. 배치 세그먼트 바에 쓰며 합은 video_count와 같다."""
    pid = await new_project(client, admin_token, "prep-batch")
    batch = str(uuid4())
    for i in range(3):
        vb = await upload_video(client, admin_token, pid, title=f"pb{i}", batch=batch)
        await _set_video(vb, probe_status="passed", duration_seconds=60.0)
    v_tc = await upload_video(client, admin_token, pid, title="pb-tc", batch=batch)
    await _set_video(v_tc, transcode_status="queued")

    row = next(
        b for b in await _get(client, admin_token, f"/projects/{pid}/ingest-batches")
        if b["batch_id"] == batch
    )
    prep = row["prep"]
    assert prep["awaiting_analysis"] == 3
    assert prep["transcoding"] == 1
    assert sum(prep[k] for k in _STAGES) == prep["total"] == row["video_count"] == 4

    # 묶음 상세의 집계도 같은 수다. 목록 세그먼트와 상세 칩이 같은 분모를 쓴다.
    c = await _prep_counts(client, admin_token, batch)
    assert c["transcoding"] == 1 and c["total"] == 4


async def test_job_detail_answers_what_the_screen_asks(client: AsyncClient, admin_token: str):
    """Job 상세가 수행자·이슈 분해·클립 수·분모를 한 번에 돌려주는지 확인한다.

    목록 DTO는 50행을 싣기 때문에 가볍게 둔다.
    상세는 1건이라 화면에 필요한 값을 모두 채운다.
    """
    from uuid import uuid4
    pid = await new_project(client, admin_token, "job-detail")
    batch = str(uuid4())
    # 준비 완료 1, 이슈 2(검증 실패·변환 실패), 대기 1
    v_done = await upload_video(client, admin_token, pid, title="d1", batch=batch)
    await seed_clip_work(pid, v_done, rights_status="auto_cleared")
    v_probe = await upload_video(client, admin_token, pid, title="d2", batch=batch)
    await _set_video(v_probe, probe_status="rejected")
    v_tc = await upload_video(client, admin_token, pid, title="d3", batch=batch)
    await _set_video(v_tc, transcode_status="failed")
    v_wait = await upload_video(client, admin_token, pid, title="d4", batch=batch)
    await _set_video(v_wait, probe_status="passed", duration_seconds=60.0)

    d = await _get(client, admin_token, f"/ingest-batches/{batch}")

    # 식별 정보: 목록에는 없는 수행자 이름을 싣는다.
    assert d["created_by_name"], "누가 넣었나는 운영 질문이다"
    assert d["video_count"] == 4
    # 분포: 칩·세그먼트의 분모다. 합이 total과 같다.
    assert d["prep"]["total"] == 4
    assert sum(d["prep"][k] for k in _STAGES) == 4
    assert d["prep"]["blocked"] == 2
    # 이슈 분해: 이슈 수와 함께 발생 단계를 돌려준다.
    assert {i["stage"]: i["count"] for i in d["issues"]} == {"probe": 1, "transcode": 1}
    # 클립 수: 이 Job의 영상에서 나온 클립이다.
    assert d["clip_count"] == 1


async def test_job_detail_reports_deleted_separately(client: AsyncClient, admin_token: str):
    """영상이 모두 삭제된 Job은 삭제 수를 따로 돌려준다. '0건 완료'로 표시하지 않는다."""
    from uuid import uuid4
    pid = await new_project(client, admin_token, "job-del")
    batch = str(uuid4())
    vid = await upload_video(client, admin_token, pid, title="gone", batch=batch)
    await client.delete(f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"})

    d = await _get(client, admin_token, f"/ingest-batches/{batch}")
    assert d["video_count"] == 0
    assert d["deleted_videos"] == 1
    assert d["prep"]["total"] == 0


async def test_condition_selection_knows_the_denominator(client: AsyncClient, admin_token: str):
    """조건 기준 선택에서는 서버가 분모를 돌려준다.

    화면에 실린 행의 id만 모으면 커서 페이지를 모두 넘기기 전에는 전체를 고를 수 없다.
    """
    from uuid import uuid4
    pid = await new_project(client, admin_token, "sel")
    batch = str(uuid4())
    ids = []
    for i in range(3):
        v = await upload_video(client, admin_token, pid, title=f"s{i}", batch=batch)
        await _set_video(v, probe_status="passed", duration_seconds=60.0)
        ids.append(v)
    other = await upload_video(client, admin_token, pid, title="other")
    await _set_video(other, probe_status="passed", duration_seconds=60.0)

    async def _sel(body: dict) -> dict:
        """선택 API를 부르고 본문을 돌려준다."""
        r = await client.post(
            f"/projects/{pid}/videos/select",
            headers={"Authorization": f"Bearer {admin_token}"}, json=body,
        )
        assert r.status_code == 200, r.text
        return r.json()

    # Job과 단계 조건: 그 Job의 대기 3건만 고른다. 다른 Job의 영상은 들어오지 않는다.
    got = await _sel({"batch": batch, "prep": "awaiting_analysis"})
    assert got["total"] == 3
    assert set(got["video_ids"]) == set(ids)
    assert got["truncated"] is False

    # 개별 제외: "전체 선택 후 한 건만 빼기"가 된다.
    got2 = await _sel({"batch": batch, "prep": "awaiting_analysis", "exclude_ids": [ids[0]]})
    assert got2["total"] == 2 and ids[0] not in got2["video_ids"]

    # 조건 없이 프로젝트 전체를 고르면 분모가 커진다(다른 Job 포함).
    got3 = await _sel({"prep": "awaiting_analysis"})
    assert got3["total"] == 4


async def test_scene_split_is_shown_as_vendor_delegated_not_missing(
    client: AsyncClient, admin_token: str
):
    """장면 분할 단계를 여정에 표시하고 MI 스택에서는 `건너뜀`과 이유로 표시한다.

    MI 스택에서는 MI가 분석하면서 구간을 자른다. 여정에 단계를 드러내면 화면이 구간을 자르는 주체를 표시할 수 있다.
    이 시스템이 자르지 않았을 뿐 처리는 끝난 상태다. 변환·비식별화 단계의 건너뜀과 같은 표현 방식이다.
    """
    pid = await new_project(client, admin_token, "scene")
    vid = await upload_video(client, admin_token, pid)
    await _set_maiu(pid, vid, "done")

    body = (await client.get(
        f"/videos/{vid}/prep-timeline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    stages = [e["stage"] for e in body["entries"]]
    # 여정은 하나로 유지한다. 스택마다 파이프라인을 따로 만들지 않는다.
    assert "scene_split" in stages
    assert stages.index("scene_split") == stages.index("analyze") - 1, "분석 바로 앞이어야 한다"

    row = next(e for e in body["entries"] if e["stage"] == "scene_split")
    assert row["status"] == "skipped"
    # 건너뛴 이유를 돌려준다. 이유 없는 `건너뜀`은 운영자에게 이유를 찾게 만든다.
    #
    # 문구에 `MI`가 들어 있는지는 확인하지 않는다. `MI`는 내부 약어라 화면에 쓰지 않는다
    # (`ncp-shots` 프로젝트에는 MI가 없다). `MI`가 있는지를 단언하면 그 결함을 테스트로 고정하게 된다.
    assert row["skipped_reason"], "건너뜀에 이유가 없다"
    assert "우리" not in row["skipped_reason"], "화면 문구에 1인칭을 쓰지 않는다"


async def test_scene_split_is_pending_before_analysis_starts(
    client: AsyncClient, admin_token: str
):
    """분석이 시작되기 전에는 장면 분할 단계를 `대기`로 두고 `건너뜀`으로 표시하지 않는다.

    구간을 자르는 주체는 분석이 시작돼야 정해진다.
    미리 "MI가 자름"이라고 쓰면 그 스택이 아닌 프로젝트에서 틀린 정보가 된다.
    """
    pid = await new_project(client, admin_token, "scene2")
    vid = await upload_video(client, admin_token, pid)
    await _set_maiu(pid, vid, "registering")

    body = (await client.get(
        f"/videos/{vid}/prep-timeline",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    row = next(e for e in body["entries"] if e["stage"] == "scene_split")
    assert row["status"] == "pending"
    assert not row.get("skipped_reason")
