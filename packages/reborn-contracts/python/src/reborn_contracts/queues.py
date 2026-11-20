"""워커 태스크의 이름·큐·라우팅 표. worker-io와 worker-jobs가 같이 읽는다.

두 워커는 서로를 import하지 않고 태스크를 이름으로 주고받는다. 그래서 이름과 큐가
한쪽에만 적혀 있어도 빌드는 통과한다. 그러면 메시지가 소비자 없는 큐에 쌓이거나, 받는 워커에
등록되지 않은 태스크라 버려진다. 이 모듈이 그 이름의 단일 출처다.

태스크 이름은 모듈 위치와 무관하게 `worker_io.tasks.<함수명>`으로 고정한다.
worker-jobs가 등록하는 잡도 같은 접두사를 쓴다. 큐에 이미 쌓인 메시지와 beat 스케줄,
하트비트 Redis 키(`reborn:beat:<태스크 이름>`)가 모두 이 이름을 가리키기 때문이다.

페이로드 스키마와 멱등 근거는 `packages/reborn-contracts/queues.md`에 있다.
"""

#: DB 경량 운영 큐(reclaim·export와 무DB 잡의 회신)
QUEUE_DEFAULT = "default"
#: MAIU 폴링과 reconciler 큐
QUEUE_MAIU = "q.maiu"
#: 초 단위 ffmpeg 작업(썸네일·probe) 큐
QUEUE_MEDIA = "q.media"
#: 수십 분짜리 인코딩·publish·씬 분할 큐
QUEUE_TRANSCODE = "q.transcode"
#: 비식별화 큐
QUEUE_DEIDENT = "q.deident"
#: 캡션 품질검수 큐. 한 건이 모델 응답을 수십 초 기다린다
QUEUE_QC = "q.qc"

#: 영상 1건의 분석본 트랜스코딩 잡(worker-jobs)
TRANSCODE_ONE = "worker_io.tasks.transcode_one"
#: 영상 1건의 씬 분할 잡(worker-jobs)
SCENE_SPLIT_ONE = "worker_io.tasks.scene_split_one"
#: 영상 1건의 재생본 인코딩 잡(worker-jobs)
PUBLISH_ONE = "worker_io.tasks.publish_one"
#: 영상 1건의 비식별화 잡(worker-jobs)
DEIDENT_ONE = "worker_io.tasks.deident_one"

#: 트랜스코딩 하트비트 회신(worker-io)
TRANSCODE_HEARTBEAT = "worker_io.tasks.transcode_heartbeat"
#: 트랜스코딩 완료 회신(worker-io)
TRANSCODE_COMPLETE = "worker_io.tasks.transcode_complete"
#: 씬 분할 하트비트 회신(worker-io)
SCENE_SPLIT_HEARTBEAT = "worker_io.tasks.scene_split_heartbeat"
#: 씬 분할 완료 회신(worker-io)
SCENE_SPLIT_COMPLETE = "worker_io.tasks.scene_split_complete"
#: publish 하트비트 회신(worker-io)
PUBLISH_HEARTBEAT = "worker_io.tasks.publish_heartbeat"
#: publish 완료 회신(worker-io)
PUBLISH_COMPLETE = "worker_io.tasks.publish_complete"
#: 비식별화 하트비트 회신(worker-io)
DEIDENT_HEARTBEAT = "worker_io.tasks.deident_heartbeat"
#: 비식별화 완료 회신(worker-io)
DEIDENT_COMPLETE = "worker_io.tasks.deident_complete"
#: 커밋된 제출·승인 한 건의 캡션 품질검수(worker-io)
CAPTION_QC_RUN = "worker_io.tasks.caption_qc_run"
#: 검사를 받지 못한 제출·승인을 찾아 `CAPTION_QC_RUN`으로 보내는 주기 태스크(worker-io)
DISPATCH_CAPTION_QC = "worker_io.tasks.dispatch_caption_qc"

#: worker-jobs가 등록하는 무DB 잡 이름. worker-io는 이 이름을 등록하지 않고 보내기만 한다.
JOB_TASKS = frozenset({TRANSCODE_ONE, SCENE_SPLIT_ONE, PUBLISH_ONE, DEIDENT_ONE})

#: 무DB 잡이 보내는 회신 이름. worker-io가 등록하고 DB에 반영한다.
CALLBACK_TASKS = frozenset({
    TRANSCODE_HEARTBEAT, TRANSCODE_COMPLETE, SCENE_SPLIT_HEARTBEAT, SCENE_SPLIT_COMPLETE,
    PUBLISH_HEARTBEAT, PUBLISH_COMPLETE, DEIDENT_HEARTBEAT, DEIDENT_COMPLETE,
})

# 워크로드별로 큐를 나눈다. 큐마다 워커를 따로 띄워 자원·실패 범위·배포를 분리하고,
# 오케스트레이터 없이 prod compose에서 서비스 단위로 스케일한다.
# 큐별 특성과 큐를 섞을 때 생기는 문제는 `packages/reborn-contracts/queues.md`의 큐 절에 있다.
#: celery `task_routes`에 그대로 넣는 라우팅 표. 여기 없는 태스크는 `default`로 간다.
TASK_ROUTES: dict[str, dict[str, str]] = {
    "worker_io.tasks.run_maiu_reconciler": {"queue": QUEUE_MAIU},
    "worker_io.tasks.run_pending_clip_thumbnails": {"queue": QUEUE_MEDIA},
    "worker_io.tasks.run_pending_video_thumbnails": {"queue": QUEUE_MEDIA},
    "worker_io.tasks.probe_pending_videos": {"queue": QUEUE_MEDIA},
    TRANSCODE_ONE: {"queue": QUEUE_TRANSCODE},
    # dispatch·reclaim·하트비트·완료 태스크는 DB를 쓰므로 default 큐에서 돈다.
    # q.transcode에 두면 긴 인코딩에 밀려 늦게 처리된다.
    # 무DB 잡의 DB 접점은 잡 종류마다 이 네 종류의 태스크로 좁혀진다.
    "worker_io.tasks.dispatch_transcodes": {"queue": QUEUE_DEFAULT},
    "worker_io.tasks.dispatch_publishes": {"queue": QUEUE_DEFAULT},
    "worker_io.tasks.reclaim_expired_publishes": {"queue": QUEUE_DEFAULT},
    PUBLISH_ONE: {"queue": QUEUE_TRANSCODE},
    PUBLISH_HEARTBEAT: {"queue": QUEUE_DEFAULT},
    PUBLISH_COMPLETE: {"queue": QUEUE_DEFAULT},
    # q.deident: GPU를 쓰는 워크로드다. q.transcode와 섞으면
    # GPU 워커가 인코딩 잡을 가져가 GPU가 유휴 상태가 된다.
    DEIDENT_ONE: {"queue": QUEUE_DEIDENT},
    "worker_io.tasks.dispatch_deidents": {"queue": QUEUE_DEFAULT},
    "worker_io.tasks.reclaim_expired_deidents": {"queue": QUEUE_DEFAULT},
    DEIDENT_HEARTBEAT: {"queue": QUEUE_DEFAULT},
    DEIDENT_COMPLETE: {"queue": QUEUE_DEFAULT},
    # 씬 분할은 원본 전체를 디코딩하므로 자원 사용 양상이 인코딩과 같다.
    # 큐를 나누면 CPU 예산이 둘로 나뉘어 어느 쪽도 정확히 지킬 수 없다(publish와 같은 근거).
    SCENE_SPLIT_ONE: {"queue": QUEUE_TRANSCODE},
    "worker_io.tasks.dispatch_scene_splits": {"queue": QUEUE_DEFAULT},
    "worker_io.tasks.reclaim_expired_scene_splits": {"queue": QUEUE_DEFAULT},
    SCENE_SPLIT_HEARTBEAT: {"queue": QUEUE_DEFAULT},
    SCENE_SPLIT_COMPLETE: {"queue": QUEUE_DEFAULT},
    "worker_io.tasks.reclaim_expired_transcodes": {"queue": QUEUE_DEFAULT},
    TRANSCODE_HEARTBEAT: {"queue": QUEUE_DEFAULT},
    TRANSCODE_COMPLETE: {"queue": QUEUE_DEFAULT},
    # 대상을 고르는 주기 태스크는 조회 두 번이라 default에서 돈다.
    # 모델을 기다리는 검사만 q.qc로 간다.
    DISPATCH_CAPTION_QC: {"queue": QUEUE_DEFAULT},
    CAPTION_QC_RUN: {"queue": QUEUE_QC},
}

#: 확인 응답이 없는 메시지를 다시 전달하기까지의 시간(초). celery `visibility_timeout`이다.
#: 같은 Redis에 붙은 모든 워커가 자기 값으로 되돌리므로 worker-io와 worker-jobs가 같은 값을 쓴다.
#: 값은 잡 URL 유효 기간(`media.transcode._JOB_URL_TTL_S`, 22시간)에 여유 2시간을 더한다.
#: 강제 종료된 워커가 남긴 메시지는 이 시간 뒤에 다시 전달되고, 그 사이 복구는 리스 회수가 맡는다.
BROKER_VISIBILITY_TIMEOUT_S = 24 * 3600

#: 워커 노드의 동작 기록 Redis 키 접두사. 뒤에 노드 이름의 `@` 앞부분(worker·qc·gpu)이 붙는다.
#: 두 앱이 기동과 이벤트 하트비트 때 epoch을 쓰고 api의 연동 진단이 경과 시간을 잰다.
#: 주기 태스크 성공 기록(`reborn:beat:`)은 beat를 품은 worker만 남기므로 worker-qc·worker-gpu가
#: 멈춘 것은 그 기록으로 알 수 없다.
WORKER_NODE_KEY_PREFIX = "reborn:node:"
