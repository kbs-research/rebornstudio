"""대상을 DB에서 골라 처리하는 주기 태스크. 서비스 함수를 부르는 얇은 셸.

유실돼도 다음 주기가 같은 대상을 다시 집는 멱등 스윕이다. 주기는 `celery_app`의
beat 스케줄이 정한다.
"""
from ..celery_app import app
from ._session import _run_with_fresh_session


@app.task(name="worker_io.tasks.run_maiu_reconciler")
def run_maiu_reconciler() -> dict[str, int]:
    """5초마다 호출된다. 여러 worker가 동시에 불러도 안전하다.

    pg_advisory_lock으로 한 번에 하나만 돈다.

    Returns: stage별 처리 건수 dict(관찰·모니터링용).
    """
    from api_gateway.services.maiu.reconciler import run_once
    return _run_with_fresh_session(run_once)


@app.task(name="worker_io.tasks.check_integrations")
def check_integrations() -> int:
    """15분마다 호출된다. 스토리지·MI·워커·DB를 실제로 호출해 보고 상태가 바뀐 것만 알린다.

    화면의 진단은 브라우저가 외부를 자동으로 호출하지 않도록 버튼을 눌러야 돈다.
    그래서 아무도 누르지 않으면 상태를 확인할 수 없다. 외부 서비스 쪽 권한이 바뀌어 자격증명이
    무효가 돼도 누군가 그 화면을 열 때까지 드러나지 않는다. 그동안 업로드·분석은 실패한다.
    그래서 자동 점검은 서버의 주기 태스크로 둔다.

    남기는 것은 세 가지다. 스냅숏(화면이 외부를 호출하지 않고 상태를 보여 주게),
    `domain_events`(언제부터 문제가 있었는지), 알림(사람에게)이다.
    근거는 `services/ops/diagnostics`의 "지켜보기" 절에 있다.

    Returns:
        상태가 바뀐 항목 수. 바뀐 것이 없으면 0이다. 0은 정상 결과다.
    """
    from api_gateway.services.ops.diagnostics import run_and_record

    async def _run(db):
        """진단을 돌려 기록하고 상태가 바뀐 항목 수를 돌려준다."""
        _diag, changed = await run_and_record(db)
        return len(changed)

    return _run_with_fresh_session(_run)


@app.task(name="worker_io.tasks.check_connections")
def check_connections() -> int:
    """15분마다 호출된다. 등록된 자격증명 연결을 실제로 호출해 보고 상태가 바뀐 것만 알린다.

    `check_integrations`는 플랫폼 자격증명(`.env`)을 점검한다. 이 태스크는 조직이 등록한
    연결 행을 점검한다. 사람이 `테스트` 버튼을 누를 때만 점검하면 외부 서비스 쪽 권한이 바뀌어
    자격증명이 무효가 돼도 누군가 그 화면을 열 때까지 드러나지 않는다.

    알림을 받는 사람도 다르다. 연결은 조직에 속하므로(`Connection.org_id`) 알림은
    그 조직의 담당자(조직 안 활성 프로젝트의 project_admin)에게 간다.
    근거는 `services/stacks/connections`의 "지켜보기" 절에 있다.

    프로젝트가 고른 분석 대상(MI 연결·워크스페이스·MI 프로젝트)도 같은 주기로 점검한다
    (`project_stack.recheck_all`). 연결이 정상이어도 그 MI 프로젝트가 지워졌거나 권한이 빠지면
    그 프로젝트의 등록·분석만 막힌다. 알림은 그 프로젝트의 관리자에게 간다.

    Returns:
        상태가 바뀐 연결 수와 프로젝트 수의 합. 바뀐 것이 없으면 0이다. 0은 정상 결과다.
    """
    from api_gateway.services.stacks import project_stack
    from api_gateway.services.stacks.connections import recheck_all

    async def _run(db):
        """연결을 점검한 뒤 프로젝트 분석 대상을 점검한다."""
        return await recheck_all(db) + await project_stack.recheck_all(db)

    return _run_with_fresh_session(_run)


@app.task(name="worker_io.tasks.dispose_finished_sources")
def dispose_finished_sources() -> int:
    """10분마다 호출된다. 파생물이 필요한 처리를 모두 마친 영상의 원본을 보관 정책에 따라 정리한다.

    publish가 끝날 때 정리하지 않는다. publish 뒤에도 MI 분석이 있고,
    규격 안의 원본은 그 분석의 입력이다.
    조건과 근거는 `services/media/retention` 모듈 docstring에 있다.
    """
    from api_gateway.services.media.retention import dispose_finished_sources as run
    return _run_with_fresh_session(run)


@app.task(name="worker_io.tasks.sweep_orphan_staging")
def sweep_orphan_staging() -> int:
    """매일 KST 04:30에 호출된다. 보고 없이 끝난 시도가 남긴 스테이징 파일을 지운다.

    앱 경로에는 버킷 만료 규칙을 두지 않으므로 그 파일은 이 태스크만 지운다.
    조건과 근거는 `services/media/retention.sweep_orphan_staging`에 있다.
    """
    from api_gateway.services.media.retention import sweep_orphan_staging as run
    return _run_with_fresh_session(run)


@app.task(name="worker_io.tasks.fill_source_checksums")
def fill_source_checksums() -> int:
    """10분마다 호출된다. 아직 재지 않은 원본의 sha256을 몇 건씩 채운다.

    체크섬은 파일 전체를 읽어야 해서(2.3GB에 수십 초) 등록할 때 재지 않는다.
    원본이 지워지면 다시 잴 수 없으므로 주기마다 몇 건씩 채운다.
    근거는 `services/media/checksum` 모듈 docstring에 있다.
    """
    from api_gateway.services.media.checksum import fill_missing_checksums as run
    return _run_with_fresh_session(run)


# 한 번의 실행이 이 시간보다 길면 워커 프로세스를 끝낸다(prefork 하드 제한).
# 업로드가 응답 없이 멈추면 그 행을 점유한 채 계속 멈춰 있어 회수 쿼리도 그 행을 처리할 수 없다.
# 프로세스가 끝나면 커넥션과 함께 행 잠금이 풀린다. 그러면 다음 beat의 회수 쿼리가
# 시도 횟수를 센 뒤 되돌린다(`export_service.reclaim_stale`).
# 30분은 7만 클립 조립(수 분)의 여러 배라 정상 실행을 끊지 않는다.
_EXPORT_TIME_LIMIT_S = 30 * 60


@app.task(name="worker_io.tasks.run_pending_exports", time_limit=_EXPORT_TIME_LIMIT_S)
def run_pending_exports() -> int:
    """30초마다 호출된다. 대기 중인 내보내기를 만든다.

    워커 없이 남은 `running`도 회수한다.
    """
    from api_gateway.services.exports.export_service import process_pending
    return _run_with_fresh_session(process_pending)


@app.task(name="worker_io.tasks.purge_audit_events")
def purge_audit_events() -> int:
    """매일 KST 03:00에 호출된다. 보존 기간(3년)이 지난 감사 행을 지운다.

    5,000행씩 묶어 5분 예산 안에서 되풀이해 지우고, 남은 행은 다음 회차가 이어서 지운다.

    Returns: 지운 행 수(관찰용).
    """
    from api_gateway.services.ops.audit_retention import purge
    return _run_with_fresh_session(purge)


@app.task(name="worker_io.tasks.run_pending_storage_imports")
def run_pending_storage_imports() -> dict[str, int]:
    """10초마다 호출된다. 폴더째 등록 작업(pending/running 묶음)을 서버에서 처리한다.

    advisory try-lock으로 한 번에 하나만 돈다. 페이지마다 커밋하고 token을 저장한다.
    그래서 worker가 비정상 종료돼도 다음 주기가 끊긴 지점부터 이어서 한다
    (등록은 source_uri로 중복을 막으므로 재시도해도 안전하다).
    """
    from api_gateway.services.media.storage_import import run_pending_imports
    return _run_with_fresh_session(run_pending_imports)


@app.task(name="worker_io.tasks.run_pending_clip_thumbnails")
def run_pending_clip_thumbnails() -> int:
    """1분마다 호출된다. 씬 썸네일 키가 없는 클립의 썸네일을 영상 프레임에서 만든다.

    새 MAIU 클립도 포함한다.
    """
    from api_gateway.services.media.clip_thumbnail import process_pending
    return _run_with_fresh_session(process_pending)


@app.task(name="worker_io.tasks.run_pending_video_thumbnails")
def run_pending_video_thumbnails() -> int:
    """1분마다 호출된다. 포스터가 없는 영상의 앞 프레임을 추출해 대표 이미지를 만든다.

    MAIU 클립과 관계없이 동작한다.
    """
    from api_gateway.services.media.video_thumbnail import process_pending
    return _run_with_fresh_session(process_pending)


@app.task(name="worker_io.tasks.probe_pending_videos")
def probe_pending_videos() -> int:
    """1분마다 호출된다. MI 요청 전에 ffprobe로 규격을 검사한다.

    등록 요청 안에서 probe하지 않는다. 폴더 단위 대량 등록에서 동기 probe는 병목이 된다.
    presigned URL을 Range 요청으로 읽어 전체를 다운로드하지 않는다
    (ffmpeg가 필요하므로 worker-io 이미지에서만 돈다).
    """
    from api_gateway.services.media.probe import process_pending
    return _run_with_fresh_session(process_pending)
