"""storage 일괄 등록 테스트. 2단 스크리닝 파이프라인의 등록 단계다.

등록 영상의 copyright_status는 cleared다(`INITIAL_COPYRIGHT_STATUS`).
외부 전송과 분석 비용이 생기는 게이트는 사람이 누르는 분석 요청(dispatch)이다.
전체를 쓸 수 없는 영상에 분석비를 쓰지 않으려면 그 영상의 분석 요청을 누르지 않으면 된다.
분석 후에는 클립 단위 2단 판정이 예외 클립을 걸러낸다.
"""

import uuid

import pytest_asyncio
from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, Video, WorkItem
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select, update
from tests import _storage
from tests.conftest import uniq

# reconciler 6단계. `pending`, `registering`, `registered`, `analyzing`, `analyzed`,
# `summarizing`(클립 INSERT 포함), `done` 순서로 진행한다. `test_maiu_reconciler`와 같은 순서다.
_RECONCILER_STEPS = (
    maiu_reconciler.bulk_check_registrations,
    maiu_reconciler.bulk_poll_registrations,
    maiu_reconciler.bulk_check_analyses,
    maiu_reconciler.bulk_poll_analyses,
    maiu_reconciler.bulk_check_summaries,
    maiu_reconciler.bulk_poll_scene_summaries,
)


async def _run_reconciler() -> None:
    """reconciler 전 단계를 순서대로 1회씩 실행한다. job 1건이 done까지 전이한다."""
    for step in _RECONCILER_STEPS:
        async with SessionLocal() as s:
            await step(s)


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client():
    """dispatch 확인용 FakeMaiuClient를 주입하고 끝나면 싱글턴을 비운다."""
    fc = FakeMaiuClient()
    maiu_client.set_client(fc)
    yield fc
    maiu_client._client_singleton = None


async def _new_project(client: AsyncClient, admin_token: str) -> str:
    """테스트용 프로젝트를 만들고 project_id를 돌려준다."""
    slug = f"st-{uniq('').split('@')[0]}"
    pid = (
        await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"slug": slug, "name": "st"},
        )
    ).json()["project_id"]
    await _storage.pin_project(pid)
    return pid


async def _register(
    client: AsyncClient, admin_token: str, pid: str, keys: list[str],
    policy: str = "pre_cleared",
    probed: bool = True,
):
    """storage 일괄 등록을 요청한다. 기본값은 검수 생략(pre_cleared)이라 MAIU 경로를 바로 확인할 수 있다.

    영상 키는 moto 버킷에 실제 객체로 넣는다.
    객체가 없으면 dispatch의 원본 존재 사전 검사(`object_exists`)가 moto에서 404를 받는다.
    그러면 모든 영상이 '원본 없음'으로 막힌다.
    `probed`가 참이면 등록한 영상을 규격 검증 통과·길이 있음으로 바꾼다.
    probe 배치는 테스트에서 돌지 않고, 분석 요청 게이트는 검증 통과 영상만 보낸다.
    """

    for key in keys:
        _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
    body = {"keys": keys, "copyright_policy": policy}
    r = await client.post(
        f"/projects/{pid}/storage/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=body,
    )
    if probed and r.status_code == 200:
        async with SessionLocal() as s:
            await s.execute(
                update(Video)
                .where(Video.project_id == uuid.UUID(str(pid)), Video.probe_status == "pending")
                .values(probe_status="passed", duration_seconds=60.0)
            )
            await s.commit()
    return r


async def test_register_clears_video_and_enables_maiu(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """pre_cleared 등록 영상은 검수 없이 cleared가 되고 available work_item으로 MAIU dispatch된다."""
    pid = await _new_project(client, admin_token)
    keys = ["news/2026/a.mp4", "news/2026/b.mov", "news/2026/notes.json"]

    r = await _register(client, admin_token, pid, keys)
    assert r.status_code == 200, r.text
    body = r.json()
    # json은 영상이 아니라서 빠진다(invalid=1). 영상 2건만 등록하고 영상마다 work_item을 만든다.
    assert body["registered"] == 2
    assert body["invalid"] == 1
    assert body["work_items_created"] == 2

    async with SessionLocal() as s:
        vids = (await s.scalars(select(Video).where(Video.project_id == pid))).all()
        assert len(vids) == 2
        # 등록 즉시 cleared다. 초기 권리 상태는 정책과 관계없이 같다(`INITIAL_COPYRIGHT_STATUS`).
        assert all(v.copyright_status == "cleared" for v in vids)
        assert all(v.copyright_policy == "pre_cleared" for v in vids)
        # 영상마다 available work_item이 있다. MAIU dispatch가 가져가는 티켓이다.
        items = (await s.scalars(select(WorkItem).where(WorkItem.project_id == pid))).all()
        assert len(items) == 2
        assert all(it.status == "available" and it.clip_id is None for it in items)

    # MAIU dispatch가 영상 단위 검수 없이 바로 요청을 보낸다.
    jobs = (
        await client.post(
            f"/projects/{pid}/maiu/dispatch",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"limit": 10},
        )
    ).json()["created"]
    assert len(jobs) == 2


async def test_dispatch_selected_video_ids_only(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """선택 dispatch는 video_ids로 고른 영상만 MI에 요청한다. 나머지는 available로 남는다."""
    pid = await _new_project(client, admin_token)
    assert (await _register(
        client, admin_token, pid, ["sel/a.mp4", "sel/b.mp4"]
    )).json()["registered"] == 2

    async with SessionLocal() as s:
        vids = (await s.scalars(
            select(Video).where(Video.project_id == pid).order_by(Video.title)
        )).all()
    chosen, other = str(vids[0].video_id), str(vids[1].video_id)

    jobs = (await client.post(
        f"/projects/{pid}/maiu/dispatch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"video_ids": [chosen]},
    )).json()["created"]
    assert len(jobs) == 1  # 2건을 등록했지만 고른 1건만 요청한다.

    async with SessionLocal() as s:
        avail = (await s.scalars(
            select(WorkItem).where(
                WorkItem.project_id == pid, WorkItem.status == "available"
            )
        )).all()
        assert len(avail) == 1 and str(avail[0].video_id) == other


async def test_video_library_pipeline_stage(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """VideoDTO.pipeline_stage는 등록 직후 awaiting_analysis이고, MI 요청 후 analyzing이다.

    요청이 에셋을 바로 등록하므로 잡은 등록 완료 대기(`registering`)에서 시작한다.
    """
    pid = await _new_project(client, admin_token)
    hdr = {"Authorization": f"Bearer {admin_token}"}
    assert (await _register(client, admin_token, pid, ["lib/x.mp4"])).json()["registered"] == 1

    vids = (await client.get(f"/projects/{pid}/videos", headers=hdr)).json()
    assert len(vids) == 1
    assert vids[0]["pipeline_stage"] == "awaiting_analysis"
    assert vids[0]["clip_count"] == 0

    await client.post(f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"limit": 10})
    vids = (await client.get(f"/projects/{pid}/videos", headers=hdr)).json()
    assert vids[0]["pipeline_stage"] == "analyzing"


async def test_registered_video_waits_for_the_dispatch_button(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """등록 정책 대신 분석 요청 액션을 게이트로 쓴다.

    비용과 외부 전송은 사람이 누르는 '분석 요청'에서 생긴다.
    등록 시 고르는 정책은 판단 근거가 파일명뿐인 시점의 선택이라 게이트로 쓰지 않는다.
    우려되는 영상은 등록해 두고 요청을 누르지 않으면 된다.

    두 가지를 확인한다.
    등록만으로는 MAIU에 아무것도 나가지 않는다.
    등록된 영상은 정책과 관계없이 요청 한 번으로 나간다. 중간에 사람 승인 단계가 없다.
    """
    pid = await _new_project(client, admin_token)
    hdr = {"Authorization": f"Bearer {admin_token}"}
    assert (await _register(
        client, admin_token, pid, ["gate/a.mp4"]
    )).json()["registered"] == 1

    async with SessionLocal() as s:
        video = (await s.scalars(select(Video).where(Video.project_id == pid))).one()
        assert video.copyright_status == "cleared"
        items = (await s.scalars(select(WorkItem).where(WorkItem.project_id == pid))).all()
        assert len(items) == 1, "요청 대상이 되려면 디스패치 티켓이 있어야 한다"

    # 등록만으로는 아무것도 나가지 않는다. dispatch를 부르지 않았으므로 job이 없다.
    async with SessionLocal() as s:
        assert (await s.scalars(
            select(AnalysisJob).where(AnalysisJob.project_id == pid)
        )).all() == []

    # 요청을 누르면 그때 나간다. 중간에 사람 승인 단계가 없다.
    jobs = (await client.post(
        f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"limit": 10}
    )).json()["created"]
    assert len(jobs) == 1


async def test_register_allows_duplicates_and_marks_them(
    client: AsyncClient, admin_token: str
):
    """같은 객체를 다시 등록해도 허용하고 중복 등록이라는 사실을 표시한다.

    같은 원본을 정책·길이를 달리해 다시 돌리는 경우가 있다. 무엇을 두 번 넣을지는 올리는 쪽이 관리한다.
    대신 중복 건수를 돌려준다. 알리지 않고 건너뛰면 화면에서 골랐는데 등록되지 않은 이유를 알 수 없다.
    """
    from api_gateway.models import Video

    pid = await _new_project(client, admin_token)
    keys = ["dup/x.mp4"]
    first = (await _register(client, admin_token, pid, keys)).json()
    assert first["registered"] == 1
    assert first["duplicates"] == 0

    second = (await _register(client, admin_token, pid, keys)).json()
    assert second["registered"] == 1, "재등록이 막혔다"
    assert second["duplicates"] == 1, "두 번째라는 사실을 안 셌다"

    async with SessionLocal() as s:
        vids = (await s.scalars(
            select(Video).where(Video.project_id == uuid.UUID(pid))
        )).all()
    assert len(vids) == 2, "같은 오브젝트로 영상이 둘 생겨야 한다"
    dups = [v for v in vids if (v.extra or {}).get("duplicate_of")]
    assert len(dups) == 1, "나중 것이 앞엣것을 가리켜야 한다"


async def test_full_pipeline_register_to_clip_review_to_labeling(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """등록부터 라벨링 티켓 생성까지 전 단계를 실제 경로로 확인한다.

    순서는 등록, MI 분석 요청, 클립 생성, 라벨링 work_item 생성이다.
    클립은 정상 상태(`auto_cleared`)로 생기므로 라벨링 작업이 바로 생긴다.
    """
    pid = await _new_project(client, admin_token)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    # 1) 등록하면 영상은 곧바로 요청할 수 있다. 게이트는 아래 dispatch 호출 자체다.
    assert (await _register(
        client, admin_token, pid, ["pipe/src.mp4"]
    )).json()["registered"] == 1
    async with SessionLocal() as s:
        video = (await s.scalars(select(Video).where(Video.project_id == pid))).one()
        assert video.copyright_status == "cleared"
    vid = video.video_id

    # 2) MI 분석을 요청하면 AnalysisJob이 생기고 영상 work_item은 'claimed'(MI 요청 표시)가 된다.
    jobs = (await client.post(f"/projects/{pid}/maiu/dispatch", headers=hdr, json={"limit": 10})).json()["created"]
    assert len(jobs) == 1
    async with SessionLocal() as s:
        v_item = (await s.scalars(
            select(WorkItem).where(WorkItem.video_id == vid, WorkItem.clip_id.is_(None))
        )).one()
        assert v_item.status == "claimed" and v_item.claimed_by is None

    # 3) reconciler 전 단계를 돌린다. 클립은 허가 상태로 생기고 라벨링 work_item이 바로 생긴다.
    await _run_reconciler()
    async with SessionLocal() as s:
        clips = (await s.scalars(select(Clip).where(Clip.video_id == vid))).all()
        assert clips, "reconciler가 클립을 만들지 못함"
        assert all(c.rights_status == "auto_cleared" for c in clips)
        clip_items = (await s.scalars(
            select(WorkItem).where(WorkItem.video_id == vid, WorkItem.clip_id.is_not(None))
        )).all()
        assert {w.clip_id for w in clip_items} == {c.clip_id for c in clips}
        assert all(w.status == "available" and w.claimed_by is None for w in clip_items)


async def test_video_search_spans_beyond_current_page(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """검색을 서버에서 전체 행 대상으로 실행한다.

    커서 페이지에 실린 50건만 보고 결과 없음을 표시하면
    찾지 못한 영상이 없는 영상으로 읽힌다.
    """
    hdr = {"Authorization": f"Bearer {admin_token}"}
    pid = await _new_project(client, admin_token)
    await _register(client, admin_token, pid, [f"srch/bulk-{i}.mp4" for i in range(12)])
    await _register(client, admin_token, pid, ["srch/needle-clip.mp4"])

    # 첫 페이지는 5건만 싣는다. 찾는 영상(needle)이 그 안에 있는지는 정렬에 달려 있다.
    # 검색은 페이지와 관계없이 전체에서 찾아야 한다.
    page = (await client.get(f"/projects/{pid}/videos?limit=5", headers=hdr)).json()
    assert len(page) == 5

    hit = (await client.get(f"/projects/{pid}/videos?q=needle&limit=50", headers=hdr)).json()
    assert len(hit) == 1, "전량에서 찾지 못했다"
    assert "needle" in hit[0]["source_uri"]

    # 경로뿐 아니라 제목으로도 찾힌다(등록 시 파일명이 제목이 된다).
    by_title = (await client.get(
        f"/projects/{pid}/videos?q=needle-clip&limit=50", headers=hdr
    )).json()
    assert len(by_title) == 1

    # 없는 검색어는 0건을 돌려준다.
    miss = (await client.get(f"/projects/{pid}/videos?q=zzz-nope&limit=50", headers=hdr)).json()
    assert miss == []


async def test_register_without_batch_id_still_belongs_to_a_job(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """`upload_batch_id`를 보내지 않아도 서버가 Job을 만든다.

    Job 보드는 `upload_batch_id IS NOT NULL`로 거르므로 값이 NULL인 영상은 보드에서 빠진다.
    그러면 한 화면이 "영상 1건"과 "Job 없음"을 동시에 표시한다.
    id를 화면과 서버 중 어느 쪽이 만드는지는 구현 선택이고, 모든 영상이 Job에 속하는 것은 계약이다.
    """
    pid = await _new_project(client, admin_token)
    r = await _register(client, admin_token, pid, ["news/2026/solo.mp4"])
    assert r.status_code == 200, r.text

    batch_id = r.json()["batch_id"]
    assert batch_id, "부른 쪽이 자기 Job을 찾을 수 없다"

    async with SessionLocal() as s:
        v = (await s.scalars(select(Video).where(Video.project_id == pid))).one()
        assert str(v.upload_batch_id) == batch_id

    # Job 보드에 실제로 표시되는지 확인한다. 그래야 화면의 두 표시가 서로 맞는지 확인할 수 있다.
    jobs = (await client.get(
        f"/projects/{pid}/ingest-batches",
        headers={"Authorization": f"Bearer {admin_token}"},
    )).json()
    assert [j["batch_id"] for j in jobs] == [batch_id]
    assert jobs[0]["video_count"] == 1
    assert jobs[0]["source"] == "ncp"


async def test_register_chunks_share_one_job_when_batch_id_given(
    client: AsyncClient, admin_token: str, fresh_client: FakeMaiuClient
):
    """같은 `upload_batch_id`로 나눠 보내면 Job 하나에 모은다. 화면은 등록을 청크로 나눠 보낸다.

    요청마다 새 Job을 만들면 200건씩 끊어 보내는 등록 한 번이 Job 여러 개로 흩어진다.
    그래서 요청에 id가 있으면 그 id를 그대로 쓴다.
    """
    from uuid import uuid4


    pid = await _new_project(client, admin_token)
    given = str(uuid4())
    hdr = {"Authorization": f"Bearer {admin_token}"}
    for key in ("chunk/a.mp4", "chunk/b.mp4"):
        _storage.put_bytes(key, b"\x00" * 64, content_type="video/mp4")
        r = await client.post(
            f"/projects/{pid}/storage/register", headers=hdr,
            json={"keys": [key], "copyright_policy": "pre_cleared", "upload_batch_id": given},
        )
        assert r.status_code == 200, r.text
        assert r.json()["batch_id"] == given

    jobs = (await client.get(f"/projects/{pid}/ingest-batches", headers=hdr)).json()
    assert [j["batch_id"] for j in jobs] == [given]
    assert jobs[0]["video_count"] == 2


async def test_register_rejects_a_key_missing_from_the_bucket(
    client: AsyncClient, admin_token: str
):
    """입력한 영상 키가 저장소에 없으면 422로 거절하고 같은 요청의 다른 키도 등록하지 않는지 확인한다.

    키는 사람이 입력하므로 오타가 생긴다. 없는 원본을 등록하면 규격 검증이 실패할 때까지 문제 영상으로 남는다.
    영상이 아닌 키는 원래 등록하지 않으므로(`invalid`) 저장소에 없어도 막지 않는다.
    """
    pid = await _new_project(client, admin_token)
    hdr = {"Authorization": f"Bearer {admin_token}"}
    _storage.put_bytes("typed/ok.mp4", b"\x00" * 64, content_type="video/mp4")

    r = await client.post(
        f"/projects/{pid}/storage/register", headers=hdr,
        json={"keys": ["typed/ok.mp4", "typed/typo.mp4"], "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "storage_key_not_found:typed/typo.mp4"
    async with SessionLocal() as s:
        n = len((await s.execute(
            select(Video.video_id).where(Video.project_id == uuid.UUID(str(pid)))
        )).all())
    assert n == 0, "거절한 요청의 다른 키가 등록됐다"

    r = await client.post(
        f"/projects/{pid}/storage/register", headers=hdr,
        json={"keys": ["typed/ok.mp4", "typed/notes.json"], "copyright_policy": "pre_cleared"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["registered"] == 1 and r.json()["invalid"] == 1
