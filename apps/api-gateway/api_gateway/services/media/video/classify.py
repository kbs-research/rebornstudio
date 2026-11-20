"""영상 상태를 판정하는 SQL 식과 CASE.

목록 필터·집계·행 표시가 같은 판정을 쓰도록 판정 규칙은 이 모듈에만 둔다.
"""

from sqlalchemy import and_, case, or_, select

from api_gateway.models import AnalysisJob, Clip, MediaStageRun, Project, Video, WorkItem


def _has_clips_expr():
    """영상에 클립이 있으면 참인 조건을 만든다. 분석이 클립을 만들어 냈는지 판정할 때 쓴다."""
    return (
        select(Clip.clip_id).where(Clip.video_id == Video.video_id).limit(1)
        .correlate(Video).exists()
    )


def _has_ticket_expr():
    """MAIU 디스패치 티켓(영상 단위 available work_item)이 있으면 참인 조건을 만든다.

    티켓은 dispatch가 분석 대상으로 가져가는 표식이다.
    """
    return (
        select(WorkItem.work_item_id)
        .where(
            WorkItem.video_id == Video.video_id,
            WorkItem.clip_id.is_(None),
            WorkItem.status == "available",
        )
        .limit(1).correlate(Video).exists()
    )


def _maiu_status_expr():
    """영상의 MI 단계를 읽는 식을 만든다(없으면 NULL). 화면이 이 값을 그대로 표시한다.

    운영자가 MI의 현재 단계를 확인할 수 있도록
    중립 상태(`analysis_job.status`) 대신 벤더 단계를 쓴다.
    중립 상태는 네 단계를 `running` 하나로 합친다.
    판정에는 벤더 단계를 쓰지 않고 `job_state`의 중립 상태를 쓴다.

    주의: 어댑터 조건을 반드시 건다.
    `analysis_job`은 어댑터 중립 테이블이라 한 영상에 여러 행이 있을 수 있다.
    조건이 없으면 씬 분할(`scenedetect`) 행이 선택되어 MI가 실행 중이라고 잘못 표시한다.
    """
    from api_gateway.services.analysis.registry import MAIU_KEY

    return (
        select(AnalysisJob.stage).where(
            AnalysisJob.video_id == Video.video_id, AnalysisJob.adapter_key == MAIU_KEY
        )
        .limit(1).correlate(Video).scalar_subquery()
    )


def _summary_failed_expr():
    """영상의 최신 장면 요약 시도가 실패로 끝났으면 참인 조건을 만든다. NULL이 되지 않는다.

    요약 실패 경로 셋(요약 요청 실패, MI의 요약 실패 응답, 요약 요청 번호 없이 클립만 있는 잡)은
    모두 잡을 `done`으로 닫고 단계 이력에 `summarize` 실패를 남긴다.
    그래서 잡 단계로는 요약을 받은 영상과 가릴 수 없고 단계 이력의 최신 시도로 판정한다.
    요약 재요청이 새 시도를 열고(`running`), 요약을 받으면 `ok`로 닫으므로 판정이 풀린다.

    `ix_msr_video_id`(영상·id) 인덱스로 영상마다 최신 행을 찾는다.
    시도가 없는 영상은 서브쿼리가 NULL이라 `is_not_distinct_from`으로 거짓이 되게 한다.
    """
    latest = (
        select(MediaStageRun.status)
        .where(MediaStageRun.video_id == Video.video_id, MediaStageRun.stage == "summarize")
        .order_by(MediaStageRun.id.desc())
        .limit(1).correlate(Video).scalar_subquery()
    )
    return latest.is_not_distinct_from("failed")


def _summary_blocked_expr():
    """MI 분석이 끝나 클립은 있지만 장면 요약을 받지 못한 영상이면 참인 조건을 만든다.

    두 분류 축(`_ingest_bucket_case`·`_prep_stage_case`)과 막힌 위치(`_prep_blocked_stage_case`),
    요약 재요청(`maiu.service.retry_failed`)이 이 조건 하나를 쓴다.
    그래서 막힘으로 보이는 영상과 재요청 대상이 같다.
    요약은 클립에 붙는 결과라서 클립이 없는 영상은 `no_clips` 막힘으로 따로 분류한다.
    """
    return and_(_maiu_status_expr() == "done", _has_clips_expr(), _summary_failed_expr())


def _maiu_requested_at_expr():
    """영상의 MI 분석 요청 시각을 읽는 식을 만든다(없으면 NULL).

    `_maiu_status_expr`와 같은 행을 읽는다.
    """
    from api_gateway.services.analysis.registry import MAIU_KEY

    return (
        select(AnalysisJob.created_at).where(
            AnalysisJob.video_id == Video.video_id, AnalysisJob.adapter_key == MAIU_KEY
        )
        .limit(1).correlate(Video).scalar_subquery()
    )


def _ingest_bucket_case():
    """반입 작업 공간의 상태를 5가지로 분류한다.

    목록 필터·집계·행 표시가 함께 쓰는 단일 규칙이다.
    화면이 같은 판정을 다시 구현하면 결과가 어긋나므로 판정은 이 함수에만 둔다.

    분기 순서가 규칙이다. 분석 이력(MAIU job·클립)을 업로드 상태보다 먼저 본다.
    분석된 영상을 업로드 상태로 다시 분류하면 완료된 영상이 '막힘'으로 바뀐다.

    `ready`(요청 대기)는 지금 분석을 요청할 수 있는 영상이다. 분석 요청 게이트
    (`maiu.service._dispatch_core`의 `rejected_videos`)가 거르는 영상은 `ready`에서도 뺀다.
    두 조건이 다르면 화면의 요청 대기 수와 실제로 요청되는 수가 달라진다.
    """
    maiu = _maiu_status_expr()
    return case(
        # MI가 끝났는데 클립이 0인 영상은 다시 구할지 제외할지 사람이 정해야 해서 `blocked`다.
        # 준비 여정 축과 함께 바꾼다. 여정 축만 바꾸면 `준비 완료`에서는 빠지는데
        # Job은 여전히 `이슈 0`으로 표시한다.
        # 장면 요약을 받지 못한 영상도 `blocked`다. 라벨러의 AI 초안 요약이 비어 있어
        # 사람이 요약을 다시 요청해야 한다(`_summary_blocked_expr`).
        (and_(maiu == "done", _has_clips_expr(), ~_summary_failed_expr()), "done"),
        (maiu == "done", "blocked"),
        (maiu == "failed", "blocked"),
        (maiu.is_not(None), "analyzing"),
        # MAIU 행이 정리됐어도 클립이 있으면 분석이 끝난 영상으로 본다. 상태를 클립 수와 맞춘다.
        (_has_clips_expr(), "done"),
        # 규격 검증을 하지 못한 영상(`error`)은 분석 요청 게이트가 거르므로 막힘이다.
        (Video.probe_status.in_(("rejected", "error")), "blocked"),
        # 변환 중인 영상을 `ready`로 분류하면 변환 전 원본(비-h264)을 분석에 보낼 수 있다.
        # 화면만 막으면 API로 우회할 수 있어서 분류 SQL과 dispatch 게이트 양쪽에서 막는다.
        # 게이트가 재생본 확정(publish)까지 세 단계를 모두 막으므로 분류도 세 단계를 본다.
        (Video.transcode_status.in_(("pending", "queued", "running")), "preparing"),
        (Video.publish_status.in_(("pending", "queued", "running")), "preparing"),
        (Video.transcode_status == "failed", "blocked"),
        (Video.publish_status == "failed", "blocked"),
        # 비식별화 중인 영상은 `preparing`, 실패한 영상은 `blocked`다.
        # 이 두 분기가 없으면 `deident: failed`인 영상이 `else_="ready"`로 분류되어
        # 필수 단계가 실패했는데 화면은 처리할 일이 없다고 표시한다.
        # `skipped`(비식별화를 쓰지 않음)는 정상 상태라서 여기서 막힘으로 보지 않는다.
        (Video.deident_status.in_(("pending", "queued", "running")), "preparing"),
        (Video.deident_status == "failed", "blocked"),
        (Video.upload_status != "uploaded", "blocked"),
        (~_has_ticket_expr(), "blocked"),
        # 규격 검증 전 영상은 분석 요청 게이트가 거르므로 `preparing`으로 분류한다.
        (Video.probe_status == "pending", "preparing"),
        # 검증은 통과했지만 길이 정보가 없는 영상도 게이트가 거른다(`_analysis_hold_case`).
        (_no_duration_expr(), "blocked"),
        # 맨 끝에 둔다. `skipped`는 열 기본값이라 업로드·검증 중인 영상도 같은 값이다.
        # 위로 올리면 비식별화 단계에 이르지 않은 영상까지 막힘으로 분류한다.
        # 여기까지 내려온 행은 나머지 단계가 모두 끝났으므로 가려지지 않았으면 막힘이다.
        (_unmasked_expr(), "blocked"),
        else_="ready",
    )


def _prep_stage_case():
    """준비 여정 축을 판정한다. 영상이 등록부터 분석까지의 과정 중 어디에 있는지 분류한다.

    준비 여정 판정은 이 함수에만 둔다.
    `_ingest_bucket_case`(사람이 처리할 일의 축)와는 분류 기준이 다른 별도 축이다.
    대기 사유는 bucket이, 진행 위치는 이 축이 나타낸다.

    분기 순서 규칙은 bucket과 같다.
    완료된 영상을 업로드 상태로 분류하지 않도록 분석 이력을 먼저 본다.
    비식별화와 재생본 확정(publish)은 값을 따로 두지 않고 `transcoding`으로 분류한다.
    """
    maiu = _maiu_status_expr()
    return case(
        # `ready_for_work`는 이후가 사람 단계라는 뜻이라 클립이 있을 때만 쓴다.
        # 장면을 하나도 만들지 못한 영상을 `준비 완료`로 두면 작업할 클립이 있는 것처럼 보인다.
        # 장면 요약을 받지 못한 영상은 요약을 다시 받을 때까지 `blocked`다(`_summary_blocked_expr`).
        # 반입 상태 축과 함께 바꾼다. 한 축만 바꾸면 `준비 완료`에서 빠진 영상이 이슈 수에 없다.
        (and_(maiu == "done", _has_clips_expr(), ~_summary_failed_expr()), "ready_for_work"),
        (maiu == "done", "blocked"),
        (maiu == "failed", "blocked"),
        (maiu == "summarizing", "summarizing"),
        (maiu.in_(("analyzing", "analyzed")), "analyzing"),
        # pending·registering·registered는 MI 등록 구간(요청부터 import 완료까지)이다.
        (maiu.is_not(None), "registering"),
        (_has_clips_expr(), "ready_for_work"),
        (Video.probe_status == "rejected", "blocked"),
        # publish(재생본·최종화)와 비식별화도 사용자에게는 같은 "변환 중"이라
        # `transcoding`으로 분류한다.
        # 단계 구분은 타임라인이 표시한다. 축에 값을 더하면 칩·필터·라벨만 늘어난다.
        (Video.transcode_status.in_(("pending", "queued", "running")), "transcoding"),
        (Video.publish_status.in_(("pending", "queued", "running")), "transcoding"),
        (Video.deident_status.in_(("pending", "queued", "running")), "transcoding"),
        (Video.transcode_status == "failed", "blocked"),
        (Video.publish_status == "failed", "blocked"),
        # 비식별화가 실패하면 파이프라인이 멈춘다. 이슈로 표시해야 사람이 처리한다.
        (Video.deident_status == "failed", "blocked"),
        (Video.upload_status == "pending", "uploading"),
        (Video.upload_status != "uploaded", "blocked"),
        # probe error(파일을 읽지 못함)는 검증을 마치지 못한 상태라서
        # 다음 단계로 넘어갈 수 없으므로 막힘이다.
        # 반입 상태 축과 분석 요청 게이트도 같은 판정을 쓴다.
        (Video.probe_status == "error", "blocked"),
        (Video.probe_status == "pending", "probing"),
        (~_has_ticket_expr(), "blocked"),
        # 길이 정보가 없는 영상은 분석 요청 게이트가 거르므로 `blocked`로 분류한다.
        (_no_duration_expr(), "blocked"),
        # 비식별화를 켜 놓고 가리지 않은 영상은 `blocked`로 분류한다.
        # 요청 대기로 두면 분석 요청으로 가리지 않은 원본이 외부로 나갈 수 있다.
        # `_ingest_bucket_case`와 같이 맨 끝에 둔다.
        (_unmasked_expr(), "blocked"),
        # 남은 영상은 업로드·검증을 통과했고 변환이 끝났거나 필요 없으며
        # MI를 요청하지 않은 요청 대기다.
        else_="awaiting_analysis",
    )


def _deident_expected_expr():
    """영상이 속한 프로젝트가 비식별화를 켰는지 읽는 식을 만든다.

    열 기본값이 `skipped`라서 `skipped`는 "쓰지 않기로 했다"와 "아직 하지 않았다"를 함께 뜻한다.
    그래서 켜 놓고 가리지 않은 영상을 찾으려면 `deident_status`와 프로젝트 설정을 함께 본다.

    이 식을 쓰는 `case`는 `Project`를 조인하지 않으므로 상관 서브쿼리로 둔다.
    조인을 더하면 이 식을 쓰는 모든 쿼리의 행 수가 바뀔 수 있다.
    """
    return (
        select(Project.deident_enabled)
        .where(Project.project_id == Video.project_id)
        .correlate(Video).scalar_subquery()
    )


def _unmasked_expr():
    """비식별화를 켜 놓고 가리지 않은 영상이면 참인 조건을 만든다.

    이 조건이 참인 영상을 외부 분석에 보내면 가리지 않은 원본 얼굴이 벤더에 전달된다.
    """
    return and_(_deident_expected_expr(), Video.deident_status == "skipped")


# 준비 단계가 진행 중인 상태 값.
_PREP_RUNNING = ("pending", "queued", "running")


def _no_duration_expr():
    """영상 길이 정보가 없거나 0 이하이면 참인 조건을 만든다."""
    return or_(Video.duration_seconds.is_(None), Video.duration_seconds <= 0)


def _analysis_hold_case():
    """영상을 분석에 넘길 수 없는 사유 코드를 판정한다. 넘길 수 있으면 NULL이다.

    MI 분석 요청 게이트(`maiu.service._dispatch_core`)와 자체 장면 분할 게이트
    (`media.scene_split`)가 이 판정 하나를 쓴다. 조건을 따로 두면 한쪽만 고쳐졌을 때
    검증 전 원본이나 가리지 않은 원본이 다른 쪽 게이트를 통과한다.
    MI 게이트는 고른 영상이 빠진 사유를 이 코드로 돌려준다.

    규격 검증 통과를 요구한다. 필요한 단계는 검증을 통과한 뒤에야 열린다(`pipeline.open_next`).
    검증 전에는 변환·비식별화·재생본이 모두 열 기본값 `skipped`라 끝난 것처럼 보인다.
    이때 넘기면 변환 전 원본(비-h264·720p 초과)이나 비식별화 전 원본이 나간다.
    길이도 요구한다. 장면 분할은 길이 없이 구간을 만들 수 없고,
    MI는 시간당 과금이라 길이 정보가 없으면 비용을 미리 계산할 수 없다.

    코드는 다음과 같다.
    - `not_probed`: 규격 검증 전인 경우
    - `probe_failed`: 규격 위반이거나 파일을 읽지 못한 경우
    - `prep_failed`: 변환·비식별화·재생본 중 하나가 실패한 경우
    - `preparing`: 변환·비식별화·재생본 중 하나가 진행 중인 경우
    - `unmasked`: 비식별화를 켜 놓고 가리지 않은 경우(`_unmasked_expr`)
    - `no_duration`: 규격 검증은 통과했지만 길이 정보가 없는 경우

    주의: 두 게이트는 `_prep_unfinished_expr`를 파사드(`media.video`)의 이름으로 부른다.
    한쪽만 하위 모듈 이름으로 부르면 같은 판정을 쓰는지 확인하는 테스트가
    그 게이트를 검사하지 못한다.
    """
    stages = (Video.transcode_status, Video.deident_status, Video.publish_status)
    return case(
        (Video.probe_status == "pending", "not_probed"),
        (Video.probe_status != "passed", "probe_failed"),
        (or_(*(s == "failed" for s in stages)), "prep_failed"),
        (or_(*(s.in_(_PREP_RUNNING) for s in stages)), "preparing"),
        (_unmasked_expr(), "unmasked"),
        (_no_duration_expr(), "no_duration"),
        else_=None,
    )


def _prep_unfinished_expr():
    """준비가 끝나지 않았거나 막혀 영상을 분석에 넘길 수 없으면 참인 조건을 만든다.

    사유는 `_analysis_hold_case`가 정한다. 이 식은 사유가 있는지만 확인한다.
    """
    return _analysis_hold_case().is_not(None)


def _prep_blocked_stage_case():
    """막힌 위치를 판정한다. prep_stage=blocked 행에서만 쓴다.

    화면이 "변환에서 막힘"처럼 위치를 함께 표시할 때 쓴다.
    분기 순서는 `_prep_stage_case`의 blocked 분기 순서와 같아야 한다.
    순서가 다르면 같은 행이 두 판정에서 서로 다른 위치를 가리킨다.
    """
    return case(
        # `_prep_stage_case`에서 `done & 클립 없음`이 `failed`보다 앞이므로 여기서도 앞에 둔다.
        (and_(_maiu_status_expr() == "done", ~_has_clips_expr()), "no_clips"),
        # 요약은 클립에 붙는 결과라 클립이 없는 영상은 위의 `no_clips`가 먼저 잡는다.
        (_summary_blocked_expr(), "summary"),
        (_maiu_status_expr() == "failed", "analysis"),
        (Video.probe_status == "rejected", "probe"),
        (Video.transcode_status == "failed", "transcode"),
        (Video.publish_status == "failed", "publish"),
        (Video.deident_status == "failed", "deident"),
        (Video.upload_status.notin_(("uploaded", "pending")), "upload"),
        (Video.probe_status == "error", "probe"),
        # 길이 정보가 없으면 규격 검증이 길이를 읽지 못한 것이라 위치를 `probe`로 둔다.
        (_no_duration_expr(), "probe"),
        # `_prep_stage_case`에서 맨 끝이므로 여기서도 맨 끝에 둔다.
        # `deident`는 비식별화를 시도했다가 실패한 경우이고, 이 값은 비식별화를 하지 않은 경우다.
        # 두 경우는 사람이 할 일도 화면 문구도 달라서 값을 나눈다.
        (_unmasked_expr(), "deident_unmasked"),
        else_="ticket",
    )


def _pipeline_stage_case():
    """MI 처리 단계를 판정한다. 배지와 `pipeline/summary`가 쓰는 축이다(반입 상태와 별개).

    MI 잡이 없어도 클립이 있으면 `analyzed`다. 사람이 넣은 구간과 자체 장면 분할도 클립을 만든다.
    준비 여정 축(`_prep_stage_case`)이 같은 영상을 `ready_for_work`로 판정하므로 두 축을 맞춘다.
    클립 판정을 티켓 판정보다 앞에 둔다. 그래서 클립이 있는데 티켓이 남은 영상도
    분석 대기로 표시하지 않는다.
    """
    maiu = _maiu_status_expr()
    return case(
        (maiu == "done", "analyzed"),
        (maiu == "failed", "failed"),
        (maiu.is_not(None), "analyzing"),
        (_has_clips_expr(), "analyzed"),
        (_has_ticket_expr(), "awaiting_analysis"),
        else_="registered",
    )

