"""비식별화 단계의 순서와 안전장치 테스트.

세 가지를 확인한다.

  1. 꺼져 있으면 skipped다.
  2. 켰는데 처리하지 못하면 failed로 멈춘다. publish로도 MI로도 넘어가지 않는다.
     건너뛰면 가리지 않은 얼굴이 클라우드 분석기로 나간다.
  3. 켜면 항상 마스터를 만든다. publish가 그 마스터를 분석본으로 확정하므로
     MI는 비식별 사본만 읽는다.
"""
import re
import uuid

import pytest
from api_gateway.database import SessionLocal
from api_gateway.models import Project, Video
from api_gateway.services.media import deident as deident_service
from api_gateway.services.media import publish as publish_service
from api_gateway.services.media.pipeline import open_next
from httpx import AsyncClient
from tests._helpers import new_project, upload_video


@pytest.fixture(autouse=True)
async def _isolate():
    """앞 테스트가 남긴 변환·publish·비식별화 활성 행을 비운다."""
    from tests._helpers import reset_transcode_active
    await reset_transcode_active()
    yield


async def _seed(client: AsyncClient, token: str, name: str, *, deident: bool) -> uuid.UUID:
    """비식별화 스위치를 정한 프로젝트에 영상을 올리고 video_id를 돌려준다."""
    pid = await new_project(client, token, name)
    vid = uuid.UUID(await upload_video(client, token, pid))
    async with SessionLocal() as s:
        p = await s.get(Project, uuid.UUID(pid))
        p.deident_enabled = deident
        v = await s.get(Video, vid)
        v.extra = {**(v.extra or {}), "probe": {
            "video_codec": "h264", "height": 720, "format_name": "mov,mp4,m4a"}}
        await s.commit()
    return vid


async def test_disabled_project_never_opens_deident(client: AsyncClient, admin_token: str):
    """꺼져 있으면 `skipped`로 두고 publish로 넘어간다. 비식별화를 켜지 않은 프로젝트는 모두 이 경로다."""
    vid = await _seed(client, admin_token, "deid-off", deident=False)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        opened = await open_next(s, v)
        await s.commit()
    assert opened == "publish", "비식별화를 건너뛰고 publish로 간다"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "skipped"


async def test_enabled_project_opens_deident_before_publish(
    client: AsyncClient, admin_token: str
):
    """켜면 publish보다 먼저 비식별화를 연다. 이 순서를 지켜야 MI가 비식별 사본만 읽는다."""
    vid = await _seed(client, admin_token, "deid-on", deident=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        opened = await open_next(s, v)
        await s.commit()
    assert opened == "deident"
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "pending"
        assert v.publish_status == "skipped", "아직 열리면 안 된다"


async def test_failure_stops_the_pipeline(client: AsyncClient, admin_token: str):
    """켜 놓고 처리하지 못하면 파이프라인을 멈추고 publish를 열지 않는다.

    가장 위험한 실패는 오류 없이 다음 단계로 넘어가는 경우다. 비식별화를 건너뛰면 가리지 않은 얼굴이 MI로 나간다.
    """
    vid = await _seed(client, admin_token, "deid-fail", deident=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
        jobs = await deident_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))

    # 상한까지 영구 실패시킨다. 모델 미구성 오류는 이 문구로 보고된다.
    for _ in range(3):
        async with SessionLocal() as s:
            await deident_service.complete(
                s, vid, job["token"], ok=False, error="비식별화 모델이 구성되지 않았다")
            v = await s.get(Video, vid)
            if v.deident_status == "pending":
                jobs = await deident_service.build_jobs(s)
                job = next(j for j in jobs if j["video_id"] == str(vid))

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "failed"
        assert v.publish_status == "skipped", "실패했는데 다음이 열리면 raw가 나간다"
        # 화면에도 멈춘 상태로 표시해야 한다.
        assert v.extra.get("deident_error")

    async with SessionLocal() as s:
        pjobs = await publish_service.build_jobs(s)
    assert all(j["video_id"] != str(vid) for j in pjobs), "publish가 집으면 안 된다"


async def test_promotion_failure_is_recorded_not_dropped(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """승격(copy)이 실패하면 결과를 버리지 않고 사유를 남기고 시도 횟수를 센다.

    `copy_object`의 예외가 태스크 밖으로 나가면 트랜잭션이 롤백된다.
    그러면 status가 `running`으로 돌아간 채 리스(5분)가 만료될 때까지 아무 일도 일어나지 않는다.
    회수는 attempt를 되돌리므로 GPU가 처음부터 다시 돈다.
    원인이 영구적이면(5GB 초과 단일 copy 한계 등) 이 순환이 끝나지 않고 마스킹 결과도 매번 버려진다.
    """
    vid = await _seed(client, admin_token, "deid-promote-fail", deident=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
        jobs = await deident_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))

    def _boom(**kw):
        """copy 크기 한계 오류를 낸다."""
        raise RuntimeError("EntityTooLarge")

    monkeypatch.setattr(
        "api_gateway.services.media.deident.storage.copy_object", _boom)

    async with SessionLocal() as s:
        ok = await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=1.0,
            report={"model": "face-anonymizer", "masked": True})
    assert ok is False, "승격에 실패했는데 성공으로 답했다"

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        # running에 머물러 리스 만료를 기다리지 않는다. 바로 다음 시도로 넘어간다.
        assert v.deident_status == "pending", f"running에 매달렸다: {v.deident_status}"
        # 사유를 남긴다. 로그에만 있으면 화면에서 원인을 알 수 없다.
        #
        # 특정 낱말 대신 성질을 확인한다. 한국어로 적혀 있고 예외 타입이 노출되지 않아야 한다.
        # 특정 낱말을 단언하면 문구를 다듬을 때마다 실패한다. `승격`처럼 운영자에게 의미 없는
        # 내부 용어는 문구에 쓰지 않는다(`deident.py`의 해당 주석).
        said = v.extra.get("deident_error") or ""
        assert re.search(r"[가-힣]", said), f"사유가 우리 말로 안 남았다: {said!r}"
        assert "EntityTooLarge" not in said, f"예외 타입이 화면 문자열에 샜다: {said!r}"
        # 시도 횟수를 센다. 원인이 영구적이면 상한에서 멈춰야 한다. 횟수를 되돌리면 계속 다시 돈다.
        assert v.deident_attempts >= 1, "일시로 접어 환불했다 — 순환이 끝나지 않는다"
        assert v.publish_status == "skipped", "실패했는데 다음이 열리면 raw가 나간다"


async def test_permanent_failure_cleans_up_staging(
    client: AsyncClient, admin_token: str, monkeypatch,
):
    """영구 실패하면 스테이징 산출물을 지운다.

    `work/`에는 반입 버킷의 24시간 수명 규칙이 적용되지 않는다.
    그래서 성공 경로에서만 지우면 여기서 멈춘 영상의 `…deid.mp4`가 계속 남는다.
    """
    vid = await _seed(client, admin_token, "deid-cleanup", deident=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
        jobs = await deident_service.build_jobs(s)
    job = next(j for j in jobs if j["video_id"] == str(vid))

    deleted: list[str] = []
    monkeypatch.setattr(
        "api_gateway.services.media.deident.storage.delete_object",
        lambda **kw: deleted.append(kw.get("key", "")))

    # 상한까지 영구 실패시킨다.
    for _ in range(3):
        async with SessionLocal() as s:
            await deident_service.complete(
                s, vid, job["token"], ok=False, error="모델 미구성")
            v = await s.get(Video, vid)
            if v.deident_status == "pending":
                jobs = await deident_service.build_jobs(s)
                job = next(j for j in jobs if j["video_id"] == str(vid))

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "failed"
    assert any(str(vid) in k and k.endswith(".deid.mp4") for k in deleted), (
        f"스테이징을 안 지웠다: {deleted}")


async def test_skip_reason_stays_true_after_the_project_turns_it_on(
    client: AsyncClient, admin_token: str,
):
    """프로젝트가 나중에 비식별화를 켜면 그 단계를 실행 필요(`needed`)로 바꾸고 켜기 전 처리라는 설명을 싣는다.

    `skipped` 하나는 정책상 쓰지 않음, 켜기 전에 처리됨, 아직 미정(컬럼 기본값) 세 가지를 뜻한다.
    나중에 켠 프로젝트에서 "이 프로젝트는 비식별화를 쓰지 않습니다"는 틀린 문장이다.
    프로젝트는 지금 비식별화를 쓰고, 이 영상은 그 전에 처리됐다.
    프라이버시 기능이라 영상이 마스킹되지 않은 이유를 정확히 표시해야 한다.
    켠 시각은 따로 기록하지 않는다. `project.updated` 감사 이벤트가 행위자·시각과 함께
    `{"changed": {"deident_enabled": {"from": …, "to": …}}}`로 이미 남긴다.
    """
    vid = await _seed(client, admin_token, "deid-off", deident=False)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
        pid = v.project_id

    async def _entry() -> dict:
        """준비 타임라인에서 비식별화 단계 줄을 읽는다."""
        r = await client.get(f"/videos/{vid}/prep-timeline",
                             headers={"Authorization": f"Bearer {admin_token}"})
        return next(x for x in r.json()["entries"] if x["stage"] == "deident")

    e = await _entry()
    assert e["status"] == "skipped"
    assert "사용하지 않습니다" in (e["skipped_reason"] or "")

    # 나중에 켠다. 이때 "이 프로젝트는 비식별화를 쓰지 않습니다"가 표시되지 않아야 한다.
    async with SessionLocal() as s:
        p = await s.get(Project, pid)
        p.deident_enabled = True
        await s.commit()

    # 켠 뒤에는 실행이 필요한 단계(`needed`)다. 설명은 `note`에 있다.
    e = await _entry()
    assert e["status"] == "needed", e
    assert "켜기 전" in (e["note"] or ""), f"켠 뒤에도 거짓 문장이 남았다: {e}"


async def _open_and_take(vid: uuid.UUID) -> dict:
    """영상의 다음 단계를 열고 비식별화 잡을 받아 돌려준다."""
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        await open_next(s, v)
        await s.commit()
        jobs = await deident_service.build_jobs(s)
    return next(j for j in jobs if j["video_id"] == str(vid))


async def test_skipped_and_failed_are_not_the_same_column(
    client: AsyncClient, admin_token: str
):
    """비식별화를 끈 영상(`skipped`)과 켰지만 처리하지 못한 영상(`failed`)을 다른 값으로 둔다.

    `skipped`(쓰지 않음)와 `failed`(처리하지 못함)를 합치면 비식별화를 하지 않은 영상이
    정상인지 장애인지 구분할 수 없다.
    """
    off = await _seed(client, admin_token, "deid-a", deident=False)
    on = await _seed(client, admin_token, "deid-b", deident=True)
    async with SessionLocal() as s:
        await open_next(s, await s.get(Video, off))
        await s.commit()
    job = await _open_and_take(on)
    # 상한까지 영구 실패시킨다.
    for _ in range(3):
        async with SessionLocal() as s:
            await deident_service.complete(s, on, job["token"], ok=False, error="모델 미구성")
            if (await s.get(Video, on)).deident_status == "pending":
                job = next(j for j in await deident_service.build_jobs(s)
                           if j["video_id"] == str(on))
    async with SessionLocal() as s:
        assert (await s.get(Video, off)).deident_status == "skipped"
        assert (await s.get(Video, on)).deident_status == "failed"


async def test_success_moves_the_masked_file_to_the_master_key(
    client: AsyncClient, admin_token: str
):
    """처리에 성공하면 비식별 산출을 마스터 키로 옮기고 스테이징을 지운 뒤 publish를 연다.

    publish는 마스터 키의 파일을 분석본으로 확정하므로, 이 이동이 MI가 비식별 사본만 읽는 근거다.
    """
    from api_gateway.services.media import storage
    from api_gateway.services.media.deident import deident_key
    from api_gateway.services.media.transcode import master_key
    from tests import _storage

    vid = await _seed(client, admin_token, "deid-master", deident=True)
    job = await _open_and_take(vid)
    masked = b"masked-bytes"
    _storage.put_bytes(deident_key(vid, job["token"]), masked, content_type="video/mp4")

    async with SessionLocal() as s:
        assert await deident_service.complete(
            s, vid, job["token"], ok=True, elapsed_s=1.0,
            report={"model": "face-anonymizer", "masked": True}) is True

    body = storage.get_object_body(bucket=_storage.BUCKET, key=master_key(vid)).read()
    assert body == masked
    assert storage.object_exists(bucket=_storage.BUCKET,
                                 key=deident_key(vid, job["token"])) is False
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        assert v.deident_status == "done"
        assert v.publish_status == "pending", "마스터가 생긴 뒤에 publish가 열린다"


async def test_worker_refuses_unknown_model(monkeypatch):
    """등록되지 않은 모델 이름이면 잡을 영구 실패로 끝낸다. 켰는데 처리하지 못한 영상을 멈추는 규칙의 마지막 안전장치다.

    모델 이름을 비우면 기본 모델(essential-yunet)을 쓰고, 잘못 적은 이름은 건너뛰지 않고 멈춘다.
    """
    from reborn_transcode import run_deident_job
    from reborn_transcode.job_runner import JobError

    monkeypatch.setenv("DEIDENT_MODEL", "no-such-model")
    with pytest.raises(JobError) as e:
        run_deident_job({"input_url": "http://x", "put_url": "http://y"})
    assert "알 수 없는" in str(e.value)
    assert e.value.transient is False, "영구 실패여야 상한에 걸려 멈춘다"


async def test_heartbeat_runs_through_the_whole_job(monkeypatch, tmp_path):
    """오래 걸리는 어댑터가 도는 동안에도 하트비트를 보낸다.

    시작할 때 한 번만 하트비트를 보내면 큰 다운로드와 처리가 리스(5분)를 넘긴다.
    그러면 잡이 회수되고 같은 영상에 잡이 두 번 돈다.
    """
    import time as _time

    import reborn_transcode.deident as dd

    monkeypatch.setenv("DEIDENT_MODEL", "slowtest")
    monkeypatch.setattr(dd, "_put", lambda *a, **k: None)

    class _Resp:
        """다운로드 스트림 응답 대역."""
        def raise_for_status(self): pass
        def iter_bytes(self): return iter([b"x" * 16])
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(dd.httpx, "stream", lambda *a, **k: _Resp())

    def _slow(src, dst, coords=None):
        """하트비트 주기보다 오래 걸리는 비식별화 어댑터를 흉내 낸다."""
        _time.sleep(0.25)          # 하트비트 주기(0.05s)보다 길게
        dst.write_bytes(b"ok")
        return {"model": "slowtest", "masked": True}

    monkeypatch.setitem(dd._ADAPTERS, "slowtest", _slow)

    beats: list = []
    r = dd.run_deident_job(
        {"input_url": "http://x", "put_url": "http://y", "heartbeat_every_s": 0.05},
        on_heartbeat=beats.append)

    assert len(beats) >= 2, f"어댑터가 도는 동안 하트비트가 안 나갔다: {beats}"
    assert all(b is None for b in beats), "모르는 진행을 0으로 쓰지 않는다"
    assert r["report"]["model"] == "slowtest"


async def test_mi_gate_waits_for_deident_and_publish(client: AsyncClient, admin_token: str):
    """MI 요청 게이트가 준비 3단계를 모두 확인한다.

    `transcode`만 보면 `h264 · 720p` 원본은 변환을 건너뛰므로 게이트가 바로 열린다.
    그러면 비식별화가 끝나기 전에 가리지 않은 영상이 MI로 나간다.
    """
    # 예약 자동 요청은 없다. 게이트는 요청 경로 자체가 지킨다.
    # 그래서 수동 요청이 쓰는 코어(`_dispatch_core`)로 같은 성질을 확인한다.
    # 준비가 끝나지 않은 영상은 나가지 않는다.
    from api_gateway.services.maiu.service import _dispatch_core

    vid = await _seed(client, admin_token, "gate-deid", deident=True)
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        pid = v.project_id
        v.probe_status = "passed"
        v.duration_seconds = v.duration_seconds or 60.0
        v.transcode_status = "skipped"      # 720p 원본이라 마스터가 필요 없다
        v.deident_status = "pending"        # 아직 마스킹 전
        v.publish_status = "skipped"
        await s.commit()

    async def _try() -> int:
        """이 영상의 분석 요청을 시도하고 만들어진 잡 수를 돌려준다."""
        async with SessionLocal() as s:
            r = await _dispatch_core(s, pid, [vid], 1, actor_user_id=None)
            return len(r.created)

    assert await _try() == 0, "비식별화 전에 MI로 나가면 안 된다"

    # 비식별화·확정이 끝나야 열린다.
    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.deident_status = "done"
        v.publish_status = "running"
        await s.commit()
    assert await _try() == 0, "확정 전이면 분석본이 최종 자리에 없다"

    async with SessionLocal() as s:
        v = await s.get(Video, vid)
        v.publish_status = "done"
        await s.commit()
    assert await _try() >= 1, "다 끝나면 열려야 한다"
