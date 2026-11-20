"""준비 타임라인과 단계 이력 테스트.

타임라인은 단계별 현재 상태와 실패·건너뜀 사유, 재시도·건너뛰기 가능 여부만 싣는다.
시각·소요·시도 수·정체 판정은 싣지 않는다. 단계 이력은 기록으로 남고 파이프라인이 읽는다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.models import MediaStageRun, Video
from api_gateway.services.media import stage_runs
from httpx import AsyncClient
from sqlalchemy import update
from tests._helpers import new_project, upload_video

# 여정 순서의 기준은 서버의 `STAGE_ORDER`다. 여기에 다시 적으면 단계를 늘릴 때 두 곳이 달라진다.
# 그러면 이 테스트는 순서 대신 두 목록의 일치를 검사하게 된다.
_STAGES = stage_runs.STAGE_ORDER


async def _timeline(client: AsyncClient, token: str, vid: str) -> dict:
    """타임라인을 조회해 단계 이름으로 찾을 수 있게 돌려준다."""
    r = await client.get(f"/videos/{vid}/prep-timeline", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return {e["stage"]: e for e in r.json()["entries"]}


async def test_timeline_lists_all_stages_in_journey_order(client: AsyncClient, admin_token: str):
    """타임라인이 여정의 모든 단계를 `STAGE_ORDER` 순서대로 돌려주는지 확인한다.

    아직 도달하지 않은 단계도 항목으로 싣는다.
    """
    pid = await new_project(client, admin_token, "tl-order")
    vid = await upload_video(client, admin_token, pid, title="t1")

    r = await client.get(f"/videos/{vid}/prep-timeline", headers={"Authorization": f"Bearer {admin_token}"})
    assert [e["stage"] for e in r.json()["entries"]] == list(_STAGES)


async def test_timeline_carries_state_not_history(client: AsyncClient, admin_token: str):
    """타임라인 줄에 상태와 사유만 싣고 시각·소요·시도 수·정체 판정은 싣지 않는지 확인한다."""
    pid = await new_project(client, admin_token, "tl-shape")
    vid = await upload_video(client, admin_token, pid, title="t2")
    by = await _timeline(client, admin_token, vid)

    assert by["upload"]["status"] == "ok"
    gone = {"recorded", "attempt", "started_at", "finished_at", "duration_seconds",
            "elapsed_seconds", "stalled", "typical_seconds", "detail", "prior"}
    for entry in by.values():
        assert not gone & set(entry), entry


async def test_no_face_deident_result_is_flagged_for_review(client: AsyncClient, admin_token: str):
    """얼굴을 찾지 못한 비식별화 결과는 완료여도 확인 필요 경고를 싣는다. 정상 결과에는 싣지 않는다.

    가릴 얼굴이 없으면 산출이 원본과 같다. 완료만 표시하면 관리자는 얼굴이 가려지지 않은 채 나가는 것을 알 수 없다.
    """
    pid = await new_project(client, admin_token, "tl-deid-review")
    vid = await upload_video(client, admin_token, pid, title="dr")
    no_face = {"model": "yunet", "masked": False, "detection_rate": 0.0,
               "review_needed": True, "review": ["no-detections"]}
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            probe_status="passed", deident_status="done", extra={"deident": no_face}))
        await s.commit()

    deident = (await _timeline(client, admin_token, vid))["deident"]
    assert deident["status"] == "ok"
    assert deident["review_needed"] is True
    assert deident["review_note"] == "얼굴을 찾지 못해 원본이 그대로 나갑니다."

    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            extra={"deident": {**no_face, "masked": True, "detection_rate": 1.0,
                               "review_needed": False, "review": []}}))
        await s.commit()
    deident = (await _timeline(client, admin_token, vid))["deident"]
    assert deident["review_needed"] is False and deident["review_note"] is None


async def test_probe_failure_is_recorded_with_reason(client: AsyncClient, admin_token: str):
    """probe 실패를 실패 시도로 기록하고 사유를 싣는다. 타임라인이 실패 이유를 표시한다."""
    pid = await new_project(client, admin_token, "tl-probe")
    vid = await upload_video(client, admin_token, pid, title="t3")
    async with SessionLocal() as s:
        await stage_runs.start(s, UUID(vid), "probe")
        await stage_runs.finish(s, UUID(vid), "probe", ok=False, error="코덱 av1 미지원")
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    assert by["probe"]["status"] == "failed"
    assert "av1" in by["probe"]["error"]


async def test_retry_shows_the_latest_attempt(client: AsyncClient, admin_token: str):
    """같은 단계를 다시 돌리면 최신 시도의 결과를 그 단계의 상태로 쓴다. 앞 시도의 실패 사유는 남기지 않는다."""
    pid = await new_project(client, admin_token, "tl-attempt")
    vid = await upload_video(client, admin_token, pid, title="t4")
    async with SessionLocal() as s:
        for i in range(3):
            await stage_runs.start(s, UUID(vid), "transcode")
            await stage_runs.finish(s, UUID(vid), "transcode", ok=(i == 2), error=None if i == 2 else "일시 오류")
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    assert by["transcode"]["status"] == "ok"
    assert by["transcode"]["error"] is None
    async with SessionLocal() as s:
        counts = await stage_runs.attempt_counts(s, UUID(vid))
    assert counts["transcode"] == 3, "단계 이력에는 시도마다 행이 남아야 한다"


async def test_skipped_transcode_says_why(client: AsyncClient, admin_token: str):
    """변환을 건너뛴 이유를 싣는다. 이유가 없으면 운영자가 이 영상만 단계가 다른 이유를 문의하게 된다."""
    pid = await new_project(client, admin_token, "tl-skip")
    vid = await upload_video(client, admin_token, pid, title="t5")
    async with SessionLocal() as s:
        await s.execute(
            update(Video).where(Video.video_id == UUID(vid)).values(
                probe_status="passed", transcode_status="skipped", transcode_profile="never",
            )
        )
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    assert by["transcode"]["status"] == "skipped"
    assert by["transcode"]["skipped_reason"]
    # 사용자에게 열거값을 보여 주지 않는다. 화면에서 그 선택지 이름은 "원본 그대로"다.
    assert "never" not in by["transcode"]["skipped_reason"]


async def test_playback_only_video_does_not_claim_no_transcode(
    client: AsyncClient, admin_token: str
):
    """`h264 · 720p` 원본의 변환 건너뜀 사유를 분석본이 필요 없다는 문장으로 표시한다.

    이 단계는 `plan_outputs`에 "analysis"가 있는지로 판단한다.
    `needs_transcode`는 사본이 하나라도 필요하면 참이다.
    720p 원본은 재생본(480p)이 필요해서 `needs_transcode`가 참이지만 분석본은 필요 없다.
    사유를 `needs_transcode`에서 파생시키면 profile이 `auto`인 영상에 화면이
    "변환 없이 진행됨(프로파일 never)"이라고 잘못 표시한다.
    두 판정이 다르면 화면이 설정을 원인으로 잘못 표시한다.
    """
    pid = await new_project(client, admin_token, "tl-playback-only")
    vid = await upload_video(client, admin_token, pid, title="t5b")
    async with SessionLocal() as s:
        await s.execute(
            update(Video).where(Video.video_id == UUID(vid)).values(
                probe_status="passed",
                transcode_status="skipped",
                transcode_profile="auto",
                extra={"probe": {"video_codec": "h264", "height": 720,
                                 "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}},
            )
        )
        await s.commit()

    reason = (await _timeline(client, admin_token, vid))["transcode"]["skipped_reason"]
    assert "분석본" in reason, reason
    assert "프로파일" not in reason and "never" not in reason, \
        "profile=auto인데 설정 때문이라고 말하면 운영자가 설정을 뒤진다"


async def test_lease_expiry_explains_itself(client: AsyncClient, admin_token: str):
    """리스가 만료된 running 단계에 처리 서버 무응답과 자동 회수 안내를 붙인다."""
    pid = await new_project(client, admin_token, "tl-lease")
    vid = await upload_video(client, admin_token, pid, title="dead")
    async with SessionLocal() as s:
        s.add(MediaStageRun(
            video_id=UUID(vid), stage="transcode", attempt=1, status="running",
            started_at=datetime.now(UTC) - timedelta(minutes=20),
        ))
        await s.execute(
            update(Video).where(Video.video_id == UUID(vid)).values(
                transcode_status="running",
                transcode_lease_until=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    # 내부 용어(워커·회수) 대신 사용자가 아는 용어로 쓴다.
    assert "응답이 없습니다" in (by["transcode"]["note"] or "")


async def test_encoding_progress_is_per_stage_media_time(
    client: AsyncClient, admin_token: str
):
    """진행률의 분모가 그 단계가 인코딩할 미디어 시간인지 확인한다.

    재생본은 publish 단계가 만들므로 두 단계 모두 잡당 타깃이 하나다.
    `길이 × 산출 수`를 분모로 쓰면 산출이 둘 필요한 영상(1080p·av1)의 변환 막대가 50%에서 멈춘다.
    워커는 마스터를 다 만들었는데 화면은 절반으로 표시한다.
    """
    pid = await new_project(client, admin_token, "tl-progress")
    vid = await upload_video(client, admin_token, pid, title="tp")
    async with SessionLocal() as s:
        await stage_runs.start(s, UUID(vid), "transcode")
        await s.commit()

    async def _set(probe: dict, done: float) -> float | None:
        """probe 메타와 변환 진행 초를 넣고 타임라인의 진행률을 돌려준다."""
        async with SessionLocal() as s:
            v = await s.get(Video, UUID(vid))
            v.extra = {**(v.extra or {}), "probe": probe}
            v.duration_seconds = 100
            v.transcode_done_seconds = done
            v.transcode_status = "running"
            await s.commit()
        return (await _timeline(client, admin_token, vid))["transcode"]["progress"]

    h264 = {"video_codec": "h264", "format_name": "mov,mp4,m4a"}
    assert await _set({**h264, "height": 720}, 50) == 0.5
    # 산출이 둘 필요한 영상이라도 이 단계가 만드는 것은 분석본 마스터 하나다.
    # 마스터 하나를 다 만들면 이 단계는 끝난다.
    assert await _set({**h264, "height": 1080}, 100) == 1.0
    # ffmpeg가 길이를 살짝 넘겨 보고해도 1을 넘지 않는다.
    assert await _set({**h264, "height": 1080}, 120) == 1.0
    # 길이를 모르면 진행률을 만들지 않는다.
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            duration_seconds=None))
        await s.commit()
    assert (await _timeline(client, admin_token, vid))["transcode"]["progress"] is None


async def test_publish_reports_progress_too(client: AsyncClient, admin_token: str):
    """확정(publish) 단계에도 진행률을 싣는다.

    `h264 · 720p` 원본은 transcode를 건너뛰고 publish만 돈다. 가장 흔한 입력이다.
    그래서 실제로 인코딩하는 publish 단계가 진행률을 표시해야 한다. 워커는 `publish_done_seconds`를 쓴다.
    """
    pid = await new_project(client, admin_token, "tl-pub-progress")
    vid = await upload_video(client, admin_token, pid, title="pp")
    async with SessionLocal() as s:
        await stage_runs.start(s, UUID(vid), "publish")
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            duration_seconds=200, publish_status="running", publish_done_seconds=50))
        await s.commit()

    assert (await _timeline(client, admin_token, vid))["publish"]["progress"] == 0.25


async def test_new_stages_do_not_fall_through_to_mi_derivation(
    client: AsyncClient, admin_token: str
):
    """`publish`·`deident`를 각자의 상태 컬럼으로 판정한다.

    기록이 running인데 파생이 ok면 코드는 파생을 따른다(회수·경합 규칙).
    두 단계에 분기가 없으면 MI 파생으로 넘어가 클립이 있는 영상은 항상 "ok"가 된다.
    그러면 DB에서 돌고 있는 단계를 화면이 "완료"로 표시한다.
    """
    pid = await new_project(client, admin_token, "tl-newstage")
    vid = await upload_video(client, admin_token, pid, title="ns")
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(vid))
        v.probe_status = "passed"
        v.publish_status = "running"
        v.deident_status = "pending"
        await s.commit()
        await stage_runs.start(s, UUID(vid), "publish")
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    assert by["publish"]["status"] == "running", "돌고 있는데 완료라고 쓰면 안 된다"
    assert by["deident"]["status"] == "running"

    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            deident_status="skipped", publish_status="failed"))
        await s.commit()
    by = await _timeline(client, admin_token, vid)
    # 쓰지 않은 단계(`skipped`)와 처리하지 못한 단계(`failed`)를 구분한다.
    assert by["deident"]["status"] == "skipped"
    assert "사용하지 않습니다" in (by["deident"]["skipped_reason"] or "")
    assert by["publish"]["status"] == "failed"


async def test_new_attempt_closes_the_abandoned_one(client: AsyncClient, admin_token: str):
    """새 시도가 시작되면 앞 시도의 열린 행을 닫는다.

    리스 만료로 회수돼 다시 투입하면 앞 시도의 행이 running으로 남는다.
    `finish`는 최신 행 하나만 닫으므로 그대로 두면 그 행들이 계속 "진행 중"으로 남는다.
    리스 회수(`open_started_at`)도 버려진 시도의 시작 시각을 읽게 된다.
    """
    from api_gateway.models import MediaStageRun
    from sqlalchemy import select as _select

    pid = await new_project(client, admin_token, "tl-abandon")
    vid = await upload_video(client, admin_token, pid, title="ab")
    async with SessionLocal() as s:
        for _ in range(3):
            await stage_runs.start(s, UUID(vid), "transcode")
        await s.commit()

    async with SessionLocal() as s:
        rows = list((await s.scalars(
            _select(MediaStageRun)
            .where(MediaStageRun.video_id == UUID(vid), MediaStageRun.stage == "transcode")
            .order_by(MediaStageRun.id)
        )).all())
    assert [r.status for r in rows] == ["failed", "failed", "running"], \
        "앞선 시도가 열린 채 남으면 화면이 죽은 시도를 진행 중이라 말한다"
    assert all(r.finished_at is not None for r in rows[:2]), "닫힌 행은 끝난 시각이 있어야 한다"
    # 어형 대신 뜻을 확인한다. 이 테스트는 시도가 버려졌다는 사실이 적혀 있는지 확인한다.
    # 어형(`버려짐`)으로 비교하면 문구를 다듬기만 해도 실패한다.
    assert "버려" in (rows[0].error or ""), "닫힌 행은 왜 닫혔는지 적어야 한다"


async def test_upload_stage_waits_until_transfer_actually_starts(
    client: AsyncClient, admin_token: str
):
    """등록만 하고 전송을 시작하지 않은 영상의 `업로드` 단계를 대기로 표시한다.

    행은 파일보다 먼저 생긴다(등록, multipart 시작, 파트 전송, complete 순서).
    그 사이에 창을 닫으면 `upload_status='pending'`인 채 남는다.
    이때 `running`으로 판정하면 화면이 "원본 파일 전송 중"이라고 표시한다.
    아무도 다시 올리지 않으면 데이터 전송 없이 그 표시가 계속 남는다.
    """
    pid = await new_project(client, admin_token, "tl-upload")
    r = await client.post(
        f"/projects/{pid}/videos",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"title": "중단된 업로드", "copyright_policy": "pre_cleared"},
    )
    assert r.status_code in (200, 201), r.text
    vid = r.json()["video_id"]

    t = await _timeline(client, admin_token, vid)
    assert t["upload"]["status"] == "pending", t["upload"]

    # 멀티파트를 열면 전송이 시작된 것으로 보고 진행 중으로 표시한다.
    async with SessionLocal() as s:
        await s.execute(
            update(Video)
            .where(Video.video_id == UUID(vid))
            .values(extra={"mpu_upload_id": "test-upload-id"})
        )
        await s.commit()

    t = await _timeline(client, admin_token, vid)
    assert t["upload"]["status"] == "running", t["upload"]


async def test_publish_waits_when_transcode_failed(client: AsyncClient, admin_token: str):
    """분석본 변환이 실패하면 재생본 단계를 대기로 표시한다.

    `publish_status`의 기본값(`skipped`)을 그대로 읽으면 "원본이 이미 규격이라 확정할 산출이
    없습니다"라고 표시한다. 그러면 변환이 필요한 영상을 규격에 맞는 영상으로 잘못 설명한다.
    """
    pid = await new_project(client, admin_token, "tl-tc-fail")
    vid = await upload_video(client, admin_token, pid, title="tcf")
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            probe_status="passed", transcode_status="failed", publish_status="skipped"))
        await s.commit()

    by = await _timeline(client, admin_token, vid)
    assert by["transcode"]["status"] == "failed"
    assert by["publish"]["status"] == "pending"
    assert by["publish"]["skipped_reason"] is None
    assert by["publish"]["note"] == "앞 단계가 끝나야 진행합니다."
    # 사유 정보가 없으면 사유 필드를 비운다. 상태를 사유 필드에 되풀이하지 않는다.
    assert by["transcode"]["error"] is None


async def _seed_mi(video_id: str, *, external_ref: str | None, error: str) -> None:
    """실패한 MI 잡 한 건을 심는다."""
    from api_gateway.models import AnalysisJob
    from api_gateway.services.analysis.status_map import neutral_status

    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        s.add(AnalysisJob(
            adapter_key="maiu", video_id=v.video_id, project_id=v.project_id,
            stage="failed", status=neutral_status("failed"),
            external_ref=external_ref, error_msg=error,
        ))
        await s.execute(update(Video).where(Video.video_id == v.video_id).values(
            probe_status="passed"))
        await s.commit()


async def test_analysis_failure_after_registration_reads_as_operator_text(
    client: AsyncClient, admin_token: str,
):
    """자산이 있으면 등록은 성공, 분석은 실패로 표시하고 사유를 운영자용 문구로 바꾼다."""
    pid = await new_project(client, admin_token, "tl-mi-fail")
    vid = await upload_video(client, admin_token, pid, title="mif")
    await _seed_mi(
        vid, external_ref="asset-0001",
        error=('MAIU POST /api/v1/workspaces/mi/projects/101/video-assets/asset-0001/analyze → 400 '
               '{"code":"10001","message":"유효하지 않은 요청입니다."}'),
    )

    by = await _timeline(client, admin_token, vid)
    assert by["register"]["status"] == "ok"
    assert by["analyze"]["status"] == "failed"
    assert "MAIU" not in by["analyze"]["error"] and "/api/" not in by["analyze"]["error"]
    assert "10001" in by["analyze"]["error"]
    assert by["summarize"]["note"] == "앞 단계가 끝나야 진행합니다."


async def test_analysis_failure_before_registration_fails_register(
    client: AsyncClient, admin_token: str,
):
    """자산이 없으면 등록을 실패로, 분석을 대기로 표시한다.

    등록을 `대기`로 두면 "분석 등록 대기" 아래에 "분석 실패"가 표시되어 순서가 맞지 않는다.
    """
    pid = await new_project(client, admin_token, "tl-mi-reg")
    vid = await upload_video(client, admin_token, pid, title="mir")
    await _seed_mi(vid, external_ref=None, error="transport error: connect timeout")

    by = await _timeline(client, admin_token, vid)
    assert by["register"]["status"] == "failed"
    # 문장 전체 대신 뜻을 확인한다. 연결 실패를 운영자용 문구로 적고 원문을 싣지 않는다.
    # 문장 전체를 비교하면 용어를 다듬을 때마다 실패한다.
    assert "연결하지 못했습니다" in by["register"]["error"]
    assert "transport" not in by["register"]["error"]
    assert by["analyze"]["status"] == "pending"


async def test_recorded_analysis_failure_is_translated_when_read(
    client: AsyncClient, admin_token: str,
):
    """저장 시점에 변환되지 않고 쌓인 분석 실패 원문도 읽을 때 화면 문구로 바꾼다.

    원문의 벤더 사유와 코드는 남긴다.
    """
    pid = await new_project(client, admin_token, "tl-mi-raw")
    vid = await upload_video(client, admin_token, pid, title="mraw")
    raw = ('MAIU POST /api/v1/workspaces/mi/projects/101/video-assets/asset-0000/analyze → 400 '
           '{"code":"10001","message":"유효하지 않은 요청입니다."}')
    async with SessionLocal() as s:
        base = datetime.now(UTC) - timedelta(hours=1)
        for n in (1, 2):
            s.add(MediaStageRun(
                video_id=UUID(vid), stage="analyze", attempt=n, status="failed",
                started_at=base + timedelta(minutes=n), finished_at=base + timedelta(minutes=n, seconds=3),
                error=raw,
            ))
        await s.commit()

    said = (await _timeline(client, admin_token, vid))["analyze"]["error"]
    assert "MAIU" not in said and "/api/" not in said and "{" not in said
    assert said.startswith("분석")
    assert "10001" in said


async def test_video_metadata_dispatch_error_is_readable(client: AsyncClient, admin_token: str):
    """영상 조회의 `metadata.dispatch_error`가 이전 형식의 원문이어도 화면 문구로 바꿔 돌려준다."""
    pid = await new_project(client, admin_token, "tl-dispatch")
    vid = await upload_video(client, admin_token, pid, title="disp")
    async with SessionLocal() as s:
        await s.execute(update(Video).where(Video.video_id == UUID(vid)).values(
            extra={"dispatch_error": "MI 요청 실패 — 'NoneType' object has no attribute 'startswith'"}))
        await s.commit()

    r = await client.get(f"/videos/{vid}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    said = r.json()["metadata"]["dispatch_error"]
    assert "NoneType" not in said and not said.startswith("MI")
    assert said.startswith("분석 요청을 보내지 못했습니다")


async def test_shots_project_has_no_analysis_service_stages(client: AsyncClient, admin_token: str):
    """샷 검출 프로젝트의 타임라인에서 분석 등록·분석·장면 요약 줄을 빼고 MI 프로젝트에는 둔다.

    실행되지 않을 단계를 대기로 표시하면 앞 단계 안내와 함께 계속 기다리는 것처럼 보인다.
    화면은 대기 줄이 있는 동안 타임라인을 계속 다시 조회한다.
    """
    mi_only = {"register", "analyze", "summarize"}
    shots = await new_project(client, admin_token, "tl-shots", analysis_preset="ncp-shots")
    vid = await upload_video(client, admin_token, shots)
    stages = set(await _timeline(client, admin_token, vid))
    assert not stages & mi_only, stages
    assert "scene_split" in stages

    mi = await new_project(client, admin_token, "tl-mi")
    vid = await upload_video(client, admin_token, mi)
    assert mi_only <= set(await _timeline(client, admin_token, vid))
