"""Walking Skeleton E2E 테스트. 등록부터 납품까지 한 흐름이 끝까지 동작하는지 확인한다.

전체 시나리오(admin 1명, annotator, reviewer):
  1. project 생성
  2. video 등록, 업로드, complete (helper 사용)
  3. MAIU dispatch와 FakeMaiuClient 5단계 reconcile로 clips 생성
  4. clips가 허가 상태로 생기면서 clip 단위 work_item 생성
  5. annotator claim, submit
  6. reviewer approve
  7. export 생성, `process_pending` 처리, 다운로드
  8. project stats, my_stats 검증

FakeMaiuClient를 써서 외부를 호출하지 않는다. 각 단계의 status를 명시적으로 assert한다.
"""
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import AnalysisJob, Clip, Export, WorkItem
from api_gateway.services.exports.export_service import process_pending as export_pending
from api_gateway.services.maiu import client as maiu_client
from api_gateway.services.maiu import reconciler as maiu_reconciler
from api_gateway.services.maiu.client import FakeMaiuClient
from httpx import AsyncClient
from sqlalchemy import select
from tests._helpers import add_member, caption_payload, claim_review, new_project, upload_video


async def test_walking_skeleton_full_pipeline(client: AsyncClient, admin_token: str):
    """전체 흐름을 테스트 하나로 확인한다.

    프로젝트 생성, 영상 등록, MI 분석, 클립 생성, 라벨링, 검수, 납품 순서로 진행한다.
    """
    maiu_client.set_client(FakeMaiuClient())
    try:
        # ───────────────────────────── 1. project + video ─────────────────
        pid = await new_project(client, admin_token, "ws-skel")
        video_id = await upload_video(
            client, admin_token, pid, title="ws-video", policy="pre_cleared", probed=True,
        )
        assert video_id

        # 영상 메타 네 필드(방송사·프로그램명·장르·방영일)와 회차를 실제 경로로 채운다.
        # 운영자가 영상에 한 번 넣으면 클립이 이 값을 물려받는다.
        # 납품 스키마에서 네 필드는 선택이라 비어 있어도 납품된다.
        # 이 테스트는 네 필드를 모두 채운 한 건이 끝까지 가는지 확인한다.
        patch = await client.patch(
            f"/videos/{video_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "broadcaster": "샘플 방송",
                "program_name": "9시 뉴스 샘플",
                "genre": "보도",
                "episode": "20260819",
                "aired_at": "2026-08-19T21:00:00+09:00",
            },
        )
        assert patch.status_code == 200

        # ───────────────────────────── 2. MAIU dispatch ────────────────────
        dispatch = await client.post(
            f"/projects/{pid}/maiu/dispatch",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"limit": 5},
        )
        assert dispatch.status_code == 200
        jobs = dispatch.json()["created"]
        assert len(jobs) >= 1

        # ───────────────────────────── 3. reconciler 단계 ────────────────
        async with SessionLocal() as s:
            await maiu_reconciler.bulk_check_registrations(s)
            await maiu_reconciler.bulk_poll_registrations(s)
            await maiu_reconciler.bulk_check_analyses(s)
            await maiu_reconciler.bulk_poll_analyses(s)
            await maiu_reconciler.bulk_check_summaries(s)
            await maiu_reconciler.bulk_poll_scene_summaries(s)

        async with SessionLocal() as s:
            job = await s.scalar(
                select(AnalysisJob).where(AnalysisJob.video_id == UUID(video_id))
            )
            assert job is not None
            assert job.status == "done", f"reconciler 도달 실패: {job.status}"

            clips = (await s.scalars(
                select(Clip).where(Clip.video_id == UUID(video_id))
            )).all()
            assert len(clips) >= 2  # FakeMaiuClient summary는 2 segments
            # 클립은 정상 상태로 생긴다.
            assert all(c.rights_status == "auto_cleared" for c in clips)

        # 클립마다 라벨링 티켓이 분석 직후에 생긴다.
        async with SessionLocal() as s:
            clip_wis = (await s.scalars(
                select(WorkItem).where(
                    WorkItem.video_id == UUID(video_id),
                    WorkItem.clip_id.is_not(None),
                )
            )).all()
            assert len(clip_wis) == len(clips)

        # ───────────────────────────── 4. annotator claim + submit ────────
        _, annot_tok = await add_member(client, admin_token, pid, "annotator")
        claim = await client.post(
            f"/projects/{pid}/work/next",
            headers={"Authorization": f"Bearer {annot_tok}"},
        )
        assert claim.status_code == 200
        wid = claim.json()["work_item_id"]

        submit = await client.post(
            f"/work/{wid}/submit",
            headers={"Authorization": f"Bearer {annot_tok}"},
            # 장르는 라벨러가 보내지 않는다. 장르는 위에서 영상에 넣었다.
            json={"payload": caption_payload("E2E")},
        )
        assert submit.status_code == 200
        assert submit.json()["status"] == "submitted"

        # ───────────────────────────── 5. reviewer approve ────────────────
        _, rev_tok = await add_member(client, admin_token, pid, "reviewer")
        await claim_review(client, rev_tok, pid, wid)
        review = await client.post(
            f"/work/{wid}/review",
            headers={"Authorization": f"Bearer {rev_tok}"},
            json={"decision": "approve"},
        )
        assert review.status_code == 200
        assert review.json()["status"] == "approved"

        # ───────────────────────────── 6. export ──────────────────────────
        exp = await client.post(
            f"/projects/{pid}/exports",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert exp.status_code == 201
        assert exp.json()["status"] == "pending"

        async with SessionLocal() as s:
            n = await export_pending(s, limit=10)
        assert n >= 1

        download = await client.get(
            f"/exports/{exp.json()['export_id']}/download",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert download.status_code == 200

        # 제외 사유를 먼저 확인한다. `item_count`만 보면 0건일 때 원인을 알 수 없다.
        async with SessionLocal() as s:
            e = await s.get(Export, UUID(exp.json()["export_id"]))
            assert e.error is None, f"납품 패키지에서 제외됐다: {e.error}"

        # 승인된 작업 1건이 매니페스트 1줄이 된다.
        assert download.json()["item_count"] == 1

        # ───────────────────────────── 7. stats 검증 ───────────────────────
        stats = await client.get(
            f"/projects/{pid}/stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert stats.status_code == 200
        body = stats.json()
        assert body["total_work_items"] >= len(clips)
        assert body["work_by_status"].get("approved", 0) >= 1
    finally:
        maiu_client._client_singleton = None
