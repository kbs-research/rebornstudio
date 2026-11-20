# 큐 계약

HTTP 계약은 `docs/api/openapi.json`이 검사한다. 큐 계약은 이 문서와 `queues.py`가 정한다.
태스크를 추가하거나 바꾸면 이 문서를 같은 커밋에서 고친다(worker-io README의 새 태스크 확인 목록 마지막 항목).

## 코드가 검사하는 부분

이 문서의 일부는 코드로 옮겨져 있다. 태스크 이름·큐·라우팅 표의 단일 출처는
[`python/src/reborn_contracts/queues.py`](python/src/reborn_contracts/queues.py)이고, 두 워커가 같은 표를 읽는다.

| 무엇 | 어디서 | 검사 |
|---|---|---|
| 라우팅 표(`TASK_ROUTES`) | `worker_io.celery_app`·`worker_jobs.celery_app`의 `task_routes` | 두 앱이 같은 객체를 쓰는지 양쪽 테스트가 확인한다 |
| 무DB 잡 이름(`JOB_TASKS`) | worker-jobs만 등록한다. worker-io의 `tasks.dispatch`는 이름으로 `send_task`한다 | `workers/worker-jobs/tests`(정확히 넷과 그 신뢰성 옵션) · 루트 `.importlinter`(`worker_io`에서 `worker_jobs`로 가는 import 금지) |
| 회신 이름(`CALLBACK_TASKS`) | worker-io만 등록한다. 잡은 이 이름으로 회신한다 | `workers/worker-io/tests`가 회신은 모두 등록되고 잡은 하나도 등록되지 않는지 확인한다 |
| 이름마다 소비자가 하나 | 라우팅 표의 모든 이름이 두 앱 중 정확히 한 곳에 등록된다 | `test_every_routed_task_has_exactly_one_home` |
| 무DB 경계 | worker-jobs는 api_gateway·worker_io를 import하지 않는다 | 루트 `.importlinter`의 `jobs-standalone` |

태스크 이름은 어느 패키지에 있든 `worker_io.tasks.<함수명>`이다. 큐에 쌓인 메시지·beat 스케줄·하트비트 키가
그 이름을 가리키므로 배포 순서와 무관하게 받는 쪽이 메시지를 처리한다.
페이로드 스키마·멱등 근거·트리거는 이 문서에만 있다. 태스크를 더하면 코드와 이 문서를 같은 커밋에서 고친다.

## 큐: 자원 사용 양상이 같은 태스크끼리 묶는다

워크로드마다 처리 시간과 부하가 다르므로 큐를 스케일 단위로 쓴다.
워커가 소비할 큐 목록에서 빠진 큐의 태스크는 처리되지 않는다. 컨테이너별 소비 큐는 compose 파일이 정한다.

| 큐 | 특성 | 소비 컨테이너 | 스케일 신호 |
|---|---|---|---|
| `q.maiu` | 외부 API I/O(폴링·시간과금) | worker(beat 포함) | 고정(폴링 주기가 상한) |
| `q.media` | 초 단위 ffmpeg 경량 작업(probe·썸네일) | worker | CPU |
| `q.transcode` | 수십 분 동안 CPU 100% | worker-gpu (`prefetch=1`, worker-jobs 이미지, `q.deident`와 같은 워커) | 큐 깊이(대기가 없으면 0대) |
| `q.deident` | 비식별화. 모델이 워커 안에서 GPU(없으면 CPU)를 쓴다 | worker-gpu (`prefetch=1`, worker-jobs 이미지, 늘 뜬다) | 고정(GPU 수, `DEIDENT_MAX_INFLIGHT`) |
| `q.qc` | 캡션 품질검수. 한 건이 모델 응답을 수십 초 기다리는 I/O 대기 | worker-qc (`prefetch=1`, `-c ${QC_CONCURRENCY:-4}`) | 큐 깊이(검사량은 라벨러·검수자 수에 비례) |
| `default` | DB 경량 운영 | worker | 고정 |

큐를 섞으면 생기는 일:

- `q.transcode`를 `q.media`에 섞으면 긴 인코딩 두 건이 슬롯을 모두 차지해 probe가 처리되지 않는다.
  probe는 반입 흐름의 게이트라서 화면 전체가 멈춘 것처럼 보인다.
- `q.deident`를 인코딩 큐와 섞으면 GPU 워커가 인코딩 잡을 가져가 GPU가 유휴 상태가 된다.
- `q.qc`를 `default`에 섞으면 검사 몇 건이 슬롯을 모두 차지해 내보내기와 무DB 잡의 회신이 밀린다.
  검사량은 라벨러·검수자 수에 비례하므로 따로 늘릴 수 있어야 한다.

## 태스크 대장

유형은 셋이다. `sweep`은 beat 주기로 도는 멱등 스윕이다. 페이로드가 없고, 메시지가 유실되면 다음 주기까지 늦어진다.
`targeted`는 대상을 지정하는 태스크다. 재전달을 전제로 하므로 리스와 펜싱이 필요하다.
`targeted-lite`는 무DB 잡이 보내는 하트비트·완료 회신이다.

| 태스크 | 큐 | 유형 | 트리거 | 페이로드 | 멱등 근거 |
|---|---|---|---|---|---|
| run_maiu_reconciler | q.maiu | sweep | 5s | — | pg advisory lock 싱글턴, 상태 기계 재진입 안전 |
| probe_pending_videos | q.media | sweep | 1분 | — | `probe_status='pending'`만 선별 |
| run_pending_clip_thumbnails / video_thumbnails | q.media | sweep | 1분 | — | 키 없는 행만 선별. 각각 advisory lock 싱글턴(클립 `0x52425443`·포스터 `0x52425456`)이라 한 종류가 q.media 워커(`-c 2`)의 한 자리 넘게 쓰지 않는다. 한 주기 상한은 클립 40건·포스터 20건, 시간 예산은 40초·15초(합 55초 < 주기 60초)라 규격 검사가 기다리는 시간은 최대 한 예산이다. 후보는 프로젝트마다 번갈아 고른다(`fair_select`). 처리량 산식은 `media/clip_thumbnail.py` 모듈 설명 |
| transcode_one | q.transcode | targeted·무DB | dispatch_transcodes가 투입 | 잡 dict(아래 스키마) | `acks_late`(실행 자식만 죽으면 다시 넣지 않는다) · 워커는 DB에 접근하지 않는다. 펜싱·리스는 DB 쪽(build·complete·heartbeat)이 맡는다. 중복 실행의 결과는 complete의 펜싱이 버린다 |
| transcode_heartbeat | default | targeted-lite | 무DB 워커가 시작할 때 한 번(진행 0초)과 인코딩 중 주기마다 보낸다 | `video_id, token` | 자기 토큰일 때만 리스를 연장한다(조건 UPDATE). 토큰이 다르면 아무것도 하지 않는다. 투입 때 건 큐 대기 리스(`_QUEUE_WAIT_S`)를 시작 하트비트가 평소 리스(5분)로 줄인다 |
| transcode_complete | default | targeted-lite | 무DB 워커가 종료 시 보낸다 | `video_id, token, ok, error?, transient?, elapsed_s?, outputs?` | 펜싱 판정을 한 곳에서 한다. 먼저 산출 검사로 `outputs`를 원본과 대조한다. 길이 차가 0.5초를 넘거나 읽지 못한 산출은 영구 실패로 처리한다. 성공이면 done이고 측정값은 `extra.transcode_output`에 남긴다. 일시 실패면 pending으로 돌리고 시도를 환불한다. 이어진 횟수(`transcode_transient_streak`)와 다음 투입 시각(`transcode_retry_after`, 60초부터 두 배·최대 1800초)을 두고 10회 이어지면 failed다. 영구 실패는 상한까지 재시도한 뒤 failed다. 성공하면 그 시도의 스테이징 마스터를 마스터 키로 옮긴다. `outputs`는 선택 인자다. `outputs`가 없는 이전 버전 워커의 회신은 검사 없이 받는다 |
| publish_complete | default | targeted-lite | 무DB 워커(`publish_one`)가 종료 시 보낸다 | `video_id, token, ok, error?, transient?, elapsed_s?, outputs?` | transcode_complete와 같은 판정(재생본 산출 검사 포함). 성공이면 마스터를 최종 키로 복사하고 스테이징을 정리한다. 측정값은 `extra.publish_output`에 남긴다. 검사에서 떨어지면 최종화하지 않는다 |
| dispatch_transcodes | default | sweep | 30s | — | 전역 in-flight 상한 안에서 `pending`만 `queued`로 바꾼다 |
| scene_split_one | q.transcode | targeted·무DB | dispatch_scene_splits가 투입 | 잡 dict(아래 스키마) | `acks_late`(실행 자식만 죽으면 다시 넣지 않는다) · 워커는 DB에 접근하지 않는다. 중복 실행의 결과는 complete의 펜싱이 버린다. 시작할 때 한 번과 검출 중 주기마다 하트비트를 보낸다. 실행 상한(`timeout_s`, 1시간)은 리스와 따로 둔다 |
| scene_split_heartbeat | default | targeted-lite | 무DB 워커가 시작할 때 한 번과 검출 중 주기(`heartbeat_every_s`)마다 보낸다 | `video_id, token` | 자기 토큰일 때만 리스를 연장한다. 투입 때 건 큐 대기 리스(`_QUEUE_WAIT_S`)를 시작 하트비트가 평소 리스(5분)로 줄이고, 시작한 토큰을 `payload.started_token`에 남긴다 |
| scene_split_complete | default | targeted-lite | 무DB 워커가 종료 시 보낸다 | `video_id, token, ok, segments?, basis?, error?, transient?` | 펜싱 판정을 한 곳에서 한다. 성공이면 클립을 만들고 done이다. 일시 실패면 pending으로 돌리고 시도를 환불한다. 이어진 횟수(`payload.transient_streak`)와 다음 투입 시각(`payload.retry_after`, 60초부터 두 배·최대 1800초)을 두고 10회 이어지면 failed다. 영구 실패는 상한까지 재시도한 뒤 failed다 |
| dispatch_scene_splits | default | sweep | 1분 | — | in-flight 상한 안에서만 보낸다. 회수된 pending을 먼저 고른다. 새 행만 고르면 회수한 행이 다시 투입되지 않는다 |
| reclaim_expired_scene_splits | default | sweep | 5분 | — | 리스가 만료된 `running`만 조건 UPDATE로 되돌린다. 토큰을 지워서 다시 응답한 옛 워커가 새 시도의 결과를 덮어쓰지 못하게 한다. 시작 하트비트 뒤 회수된 시도만 시도로 센다. 지운 토큰은 메시지 취소로 보낸다(아래 4) |
| deident_one | q.deident | targeted·무DB | dispatch_deidents가 투입 | 잡 dict(아래 스키마) | `acks_late`(실행 자식만 죽으면 다시 넣지 않는다) · GPU 작업 전용 큐다. 모델은 워커 프로세스 안에서 돈다. 어댑터는 `DEIDENT_MODEL`이 정하고, 비우면 `essential-yunet`이다(등록되지 않은 이름이면 영구 실패). 중복 실행의 결과는 complete의 펜싱이 버린다. `JobError`가 아닌 예외에도 완료 보고를 보낸다(하트비트는 best-effort). 보고가 없으면 리스가 만료될 때까지 기다렸다가 처음부터 다시 돈다 |
| deident_complete | default | targeted-lite | 무DB 워커가 종료 시 보낸다 | `video_id, token, ok, elapsed_s?, report?, error?, transient?` | 펜싱 판정을 한 곳에서 한다. 성공이면 비식별본을 마스터로 승격하고 done이다. 일시 실패면 pending으로 돌리고 시도를 환불한다. 이어진 횟수(`deident_transient_streak`)와 다음 투입 시각(`deident_retry_after`, 60초부터 두 배·최대 1800초)을 두고 10회 이어지면 failed다. 영구 실패는 상한까지 재시도한 뒤 failed이고 publish를 열지 않는다. 낡은 토큰의 성공 보고는 버린다. 다만 회수한 시도의 토큰이고, 영상이 대기이며, 스테이징 산출이 그 시도를 보낸 뒤에 올라온 경우에는 새 토큰으로 시도를 열어 이어받는다(`_adopt_late_result`). 승격(copy)이 실패하면 성공으로 처리하지 않고 되감은 뒤 사유를 남기고 횟수를 센다. 예외를 그대로 내면 트랜잭션이 되감겨 마스킹 결과가 모두 버려진다. 실패가 확정되면 스테이징 산출도 지운다 |
| deident_heartbeat | default | targeted-lite | 무DB 워커가 처리 중 주기마다 보낸다 | `video_id, token, done_seconds?` | 자기 토큰일 때만 리스를 연장한다. 진행 초는 `None`일 수 있다. 계산할 수 없는 진행을 0으로 보내면 화면이 0%에서 멈춘 것으로 잘못 표시한다 |
| dispatch_deidents | default | sweep | 30s | — | `deident_status=pending`을 전역 in-flight 상한(`DEIDENT_MAX_INFLIGHT`, 기본 1) 안에서만 보낸다. 회수돼 돌아온 영상(`deident_reclaimed` 표식)을 먼저, 그다음 오래된 영상부터 고른다(`media.deident._pick`). 일시 오류로 재시도를 기다리는 영상(`deident_retry_after`가 미래)은 고르지 않는다. worker-gpu의 `-c`(`GPU_WORKER_CONCURRENCY`, 기본 2)가 인코딩 하나와 비식별화 하나를 같이 돌린다 |
| reclaim_expired_deidents | default | sweep | 5분 | — | 리스가 만료된 `running`만 pending으로 되돌린다(시도 환불). 그 시도의 단계 이력을 일시 오류로 닫고 회수 표식(`deident_reclaimed`: 지운 토큰과 그 시도의 시작 시각)을 남긴다. 다음 투입이 그 영상을 먼저 보낸다. 늦게 온 옛 토큰의 완료 보고를 이어받을 때도 이 표식을 근거로 쓴다 |
| reclaim_expired_transcodes | default | sweep | 5분 | — | 리스 만료와 queued 정체만 조건 UPDATE로 되돌린다. 시작 하트비트 뒤 회수된 시도는 시도로 세고 상한(3)에서 failed, 시작하지 못한 시도는 환불한다(publish·deident 회수도 같다). 지운 토큰은 메시지 취소로 보낸다(아래 4). 시작 하트비트가 오지 않은 시도는 "처리 서버가 작업을 시작하지 못했습니다"로 닫는다 |
| dispatch_caption_qc | default | sweep | 5s | — | 검사를 받지 못한 제출·승인만 선별한다(`caption_qc.backlog.find_owed`: 마지막 라벨 행이나 승인 판정 뒤에 같은 시점의 실행 기록이 없고, 24시간 안이며, 실패가 3회 미만인 건). 보낸 건은 Redis 표시(`reborn:qc:lease:*`, 300초)로 다시 보내지 않는다. 실패한 시도는 표시를 60초로 줄여 그 뒤 다시 보낸다. 회수도 이 태스크가 한다. 표시가 끝나도 기록이 없으면 다시 보낸다. api는 브로커에 보내지 않는다 |
| caption_qc_run | q.qc | targeted | dispatch_caption_qc가 투입 | `work_item_id, trigger`(`submit`·`approve`) | `acks_late`·`reject_on_worker_lost` · 실행 직전 `is_owed`로 다시 확인한다(상태가 바뀐 낡은 시점은 건너뛴다) · 쓰기 직전 작업 행을 잠그고 다른 실행의 기록이 있으면 버린다(기록과 게이트 갱신은 하나). 모델 호출 실패는 기록하지 않고 다시 시도한다(세 번째 시도에서 규칙 판정으로 확정). 기록을 남기지 못한 실패와 함께 `caption_qc.failed` 감사 이벤트로 센다 |
| run_pending_exports | default | sweep | 30s | — | pending만 선별 |
| run_pending_storage_imports | default | sweep | 10s | — | advisory lock · 페이지마다 커밋하고 token으로 재개 · source_uri로 중복 제거 |

## targeted 태스크를 새로 만들 때

`transcode_one`을 본보기로 삼는다. 다른 GPU 워크로드도 잡 페이로드와 무DB 워커로 같은 프로토콜을 쓴다.
최소 요건은 다음과 같다.

1. 페이로드는 JSON으로 직렬화할 수 있는 원시값이다(UUID는 str). 스키마를 이 문서에 적는다.
2. `acks_late=True, ignore_result=True`. 무DB 잡은 `reject_on_worker_lost=False`다. 실행 자식만 죽은
   메시지를 다시 넣으면 자식을 죽이는 입력이 같은 토큰으로 곧바로 다시 돌아 시도 상한이 동작하지 않는다.
   그 복구는 회수가 맡고, 시작 하트비트 뒤 회수된 시도는 시도로 세어 상한에서 failed로 확정한다.
3. 시작은 상태·토큰·리스를 한 번에 바꾸는 조건 UPDATE이고, 종결은 자기 토큰일 때만 바꾸는 펜싱 UPDATE다.
4. 리스 만료를 pending으로 되돌리는 회수 sweep을 함께 만든다.
   메시지 id는 펜싱 토큰이다(`dispatch._send_job`의 `task_id`). 회수는 지운 토큰을
   `app.control.revoke`로 취소해, 아직 큐에 있던 옛 메시지를 워커가 시작 전에 버리게 한다.
   워커는 DB를 읽지 않으므로 시작 전에 토큰이 낡았는지 확인할 수단은 이 취소뿐이다. 이미 돌고 있는
   시도는 멈추지 않는다. 취소를 놓친 메시지의 결과는 완료 펜싱이 버린다.
5. 리스는 두 단계로 건다. 투입 때는 큐 대기를 덮는 `transcode._QUEUE_WAIT_S`(4시간)를 걸고,
   워커가 일을 시작하기 전에 보내는 하트비트가 평소 리스(`_LEASE_S`, 5분)로 줄인다.
   잡 URL은 투입 때 서명하므로 유효 기간(`_JOB_URL_TTL_S`)은 큐 대기에 처리 상한(입력 길이 상한 × 3)을
   더한 값이다. 큐 대기만 덮으면 늦게 시작한 잡의 마지막 업로드가 403으로 실패해 처음부터 다시 돈다.
   러너는 인코딩뿐 아니라 산출 검사와 업로드 동안에도 하트비트를 보낸다(`job_runner.heartbeating`).
   상한은 단계마다 따로 세고 재생본·씬 분할이 같은 `q.transcode`를 쓰므로 대기가 5분을
   넘는 것은 정상이다. 투입 때부터 5분을 걸면 대기 중에 회수·재투입되고 옛 메시지도 실행돼
   같은 영상을 두 번 변환한다.
6. 브로커 재전달 시간(`visibility_timeout`)은 두 앱이 같은 값(`BROKER_VISIBILITY_TIMEOUT_S`, 24시간)을 쓴다.
   늦은 확인 응답 잡은 끝날 때까지 확인 응답을 보내지 않는다. 확인 응답 없는 메시지는 같은 Redis에 붙은
   어느 워커든 자기 설정값이 지나면 되돌린다. 한 앱이라도 기본값(1시간)이면 긴 잡이 실행 중에 다시
   전달되어 같은 토큰으로 두 번 돈다. 값은 잡 URL 유효 기간보다 길게 둔다(`test_broker_visibility`).


## transcode_one 잡 스키마

생산자는 `media.transcode.build_jobs`(DB 쪽)이고 소비자는 `reborn_transcode.run_job`(무DB)이다.
워커의 바깥 접점은 셋이다. 입력 presigned GET, 산출 presigned PUT, 하트비트·완료 큐 메시지다.
다른 GPU 워커도 같은 형태를 쓰고, 페이로드의 `targets`만 그 모델의 입출력 사양으로 바꾼다.

```json
{
  "video_id": "uuid",
  "token": "펜싱 토큰. 완료 보고에 그대로 되돌린다",
  "input_url": "presigned GET (TTL = 큐 대기 4h + 처리 상한 18h = 22h, `transcode._JOB_URL_TTL_S`)",
  "targets": [
    {"label": "analysis-720p", "height": 720, "crf": 24, "preset": "fast", "threads": 2,
     "put_url": "presigned PUT", "content_type": "video/mp4"},
    {"label": "play-480p", "height": 480, "crf": 26, "preset": "veryfast", "threads": 2,
     "put_url": "...", "content_type": "video/mp4"}
  ],
  "heartbeat_every_s": 60
}
```

`targets[]`에는 필요한 산출만 싣는다. 원본이 `h264 · 720p · mp4`이면 `play-480p` 하나뿐이다.
워커는 개수를 가정하지 않고 순회한다.

`preset`·`threads`는 생산자가 정한다. 산출마다 쓰는 쪽이 달라 프리셋도 다르다.
재생본은 사람이 보는 프록시라 `veryfast`(1.7배 빠름)를 쓰고, 분석본은 MI 분석과 비식별화의 입력이라 `fast`를 쓴다.
`threads`가 0이면 ffmpeg가 스레드 수를 자동으로 정한다. 컨테이너에서는 `nproc`이 호스트 코어 수를 보고해
자동 값이 너무 크다. 키가 없는 이전 형식의 잡은 워커 기본값(`fast`, 자동)으로 인코딩한다.

`height`가 null이면 스케일을 생략하고 재인코딩만 한다. 업스케일하지 않는다. `put_url`은 그 시도의 스테이징 키
(`media.transcode.staging_key`, `work/{video_id}/{token}/{label}.mp4`)를 가리킨다. 완료 판정이 현재 토큰의 키만
마스터 키(변환)나 최종 재생본 키(publish)로 옮기므로, 회수된 시도의 늦은 업로드는 자기 키에만 남는다.
옮길 키는 토큰으로 정해지므로 완료 보고에 되돌리지 않는다.

### 완료 회신의 산출 측정값 `outputs`

성공 회신(`transcode_complete`·`publish_complete`)은 타깃마다 산출 측정값을 하나씩 싣는다.
워커(`reborn_transcode.job_runner._inspect`)가 올리기 전에 로컬 파일을 ffprobe로 한 번 읽은 값이다.
ffmpeg는 입력이 도중에 끊겨도 rc=0으로 짧은 사본을 쓰므로 종료 코드만으로는 성공을 판정할 수 없다.

```json
{"outputs": [{"label": "analysis-720p", "duration_seconds": 2366.7, "fps": 29.97,
              "video_codec": "h264", "width": 1280, "height": 720, "size_bytes": 812345678}]}
```

읽지 못했으면 `{"label": "…", "error": "ffprobe 끝줄"}`을 싣고 예외를 내지 않는다.
검사가 제한 시간을 넘기면 제한 시간을 두 배로 늘려 한 번 더 읽고, 그래도 넘기면 `"timed_out": true`를
더한다. 판정은 받는 쪽 한 곳(`media.transcode.judge_outputs`)이 원본 probe와 비교해 한다. 길이 차가
0.5초(`reborn_transcode.output_check.DURATION_TOLERANCE_S`)를 넘거나 읽지 못했으면 영구 실패
(`media_stage_runs.detail.cause` = `output_duration_mismatch`·`output_unreadable`)다.
검사 시간 초과는 시도를 쓰지 않는 일시 실패(`output_check_timeout`, `output_check.TRANSIENT_CAUSES`)다.

`outputs`는 선택 인자다. 키가 없는 이전 버전 워커의 회신은 검사 없이 받는다. 배포는 받는 쪽
(`default` 큐 워커)을 먼저 한다. 이전 버전의 받는 쪽은 `outputs` 인자를 받지 못해 새 회신을 버리고, 그 영상은
리스 만료 뒤 다시 돈다. 두 워커는 이미지가 다르지만(worker-io·worker-jobs) `deploy.sh`가 같은 sha 태그로
한 번에 올린다. 회신 인자를 새로 더하는 변경은 선택 인자로 두어 어느 쪽이 먼저 떠도 받게 한다.

## scene_split_one 잡 스키마

생산자는 `media.scene_split.build_jobs`(DB 쪽)이고 소비자는 `reborn_transcode.scene_split.run_job`(무DB)이다.
워커의 바깥 접점은 입력 presigned GET 하나다. 산출은 파일이 아닌 구간 목록이라 PUT이 없다.

```json
{
  "video_id": "uuid",
  "token": "펜싱 토큰. 완료 보고에 그대로 되돌린다",
  "input_url": "presigned GET (TTL = 큐 대기 4h + 실행 상한 1h. 만료 403은 일시 오류로 분류해 다시 투입할 때 새로 서명한다)",
  "duration_seconds": 2366.733,
  "threshold": 0.3,
  "max_seconds": 40,
  "heartbeat_every_s": 60,
  "timeout_s": 3600
}
```

회신(`scene_split_complete`)은 묶은 결과와 묶기 전 개수를 같이 싣는다.

```json
{"segments": [{"start": 0.0, "end": 38.4}, …],
 "basis": {"raw_count": 412, "merged_count": 47, "rule": "샷 경계 · 최대 40초 · 무음 구간 우선"}}
```

`raw_count`가 없으면 화면이 47개가 많은지 적은지 판단할 근거를 보여 줄 수 없다.
`duration_seconds`는 생산자가 채운다. 워커가 0을 받으면 영구 실패로 보고한다.
재시도해도 값이 바뀌지 않으므로 일시 실패로 다시 돌리지 않는다.


## deident_one 잡 스키마

생산자는 `media.deident.build_jobs`(DB 쪽)이고 소비자는 `reborn_transcode.run_deident_job`(무DB)이다.
입력과 출력이 하나씩이라 `transcode_one`의 `targets[]` 대신 평면 구조다.

```json
{
  "video_id": "uuid",
  "token": "펜싱 토큰. 완료 보고에 그대로 되돌린다",
  "input_url": "presigned GET (TTL 22h, 변환과 같다)",
  "put_url":   "presigned PUT (deident_key = work/{video_id}/{token}/analysis-720p.deid.mp4)",
  "content_type": "video/mp4",
  "coordinates_put_url": "presigned PUT, application/json (coordinates_staging_key = work/{video_id}/{token}/analysis-720p.faces.json)",
  "heartbeat_every_s": 60
}
```

`coordinates_put_url`은 늘 실린다. 워커는 어댑터가 좌표 파일을 썼을 때만 그 URL에 올리고
보고에 `coordinates`를 싣는다. 검증용 `passthrough`는 좌표를 쓰지 않으므로 이 필드를 쓰지 않는다.
좌표는 실제로 가린 얼굴 박스(출력 픽셀 · 프레임 단위)이고 mp4보다 먼저 올린다.
워커가 올리는 키는 모두 그 시도의 스테이징 키(`work/{video_id}/{token}/…`)다. 성공 판정이 mp4를 마스터로,
좌표를 분석본 옆 최종 위치(`transcoded/720p/{video_id}.faces.json`)로 옮긴다. 키에 토큰이 들어가므로
회수된 시도가 아직 유효한 URL로 늦게 올려도 마스킹된 마스터와 좌표를 덮어쓰지 못한다.

워커는 `DEIDENT_MODEL`의 어댑터(비우면 `essential-yunet`, 내장 비식별화 모듈)를 이 프로세스에서
돌린다. 입력은 `input_url`로 받고 산출은 `put_url`로 올린다. presigned URL만 쓰므로 워커에는 저장소
자격증명이 없다. 다운로드와 어댑터 실행 내내 하트비트를 보낸다. 진행 초는 `None`이다.

`report`는 `deident_complete`가 받아 `extra["deident"]`에 남긴다:
`{model, model_version, device, masked, review_needed, review[코드], notices[코드], frames,
detected_frames, detection_rate, boxes, elapsed_s, coordinates?}`. `coordinates`는 어댑터가 쓴 좌표 파일의 설명
(`{uploaded: true, schema_version, content_type, coordinate_space}`)이고 올렸을 때만 실린다.
서버는 그때만 `extra["deident"].coordinates = {bucket, key, schema_version, coordinate_space}`로
바꿔 남긴다(단계 이력 detail도 같다). 영구 실패면
스테이징 mp4와 함께 좌표 JSON도 지운다. `notices`는 처리 쪽 신호다. 검수 사유는 아니지만 처리 조건에 관한 사실이라 남긴다.
`masked`는 실제 결과대로 싣는다. 얼굴이 0건이면 `false`이고 `review_needed`가 참이다.
