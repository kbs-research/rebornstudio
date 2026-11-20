# tests/media

미디어 도메인(`services/media`) 테스트입니다. 영상 등록·목록·수정, 클립, 저장소 등록과 사용량, 변환(transcode), 비식별화, ffprobe 규격 검사, 준비 여정, 재생본 publish를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_analysis_purge.py` | 영상 한 편의 분석본 정리를 검사합니다. 파생물은 캐시라는 규칙을 실행하는 부분입니다. 원본이 없거나 분석이 끝나지 않았으면 409로 막는지, 정리 시각과 감사 기록을 남기는지 봅니다. |
| `test_clip_extra_merge.py` | 클립 JSONB 병합을 검사합니다. 전체 치환이 다른 쓰기의 키를 지우지 않는지 봅니다. |
| `test_clip_fits_video.py` | 분석 결과로 만드는 클립의 끝을 영상 길이 안으로 줄이는지 검사합니다. 영상 끝 이후 구간과 줄여서 하한보다 짧아진 구간은 만들지 않고, 영상 길이를 모르면 그대로 만듭니다. |
| `test_clip_labeler_view.py` | 라벨러가 받는 클립 응답에서 클립 상태 구분과 남의 작업 상태를 가리는지 검사합니다. |
| `test_clip_progress.py` | 클립 진척 요약을 검사합니다. 클립 행의 진척 값과 제외된 클립의 빈 값, 관리자·검수자가 운영 상태를 그대로 받는지 봅니다. |
| `test_clip_thumbnail_service.py` | clip_thumbnail 서비스 단위 테스트입니다. ffmpeg와 storage는 모두 mock으로 바꿉니다. |
| `test_clips_endpoint.py` | 클립 조회 엔드포인트를 검사합니다. |
| `test_video_field_visibility.py` | 라벨러에게 버킷 경로와 처리 이력을 내보내지 않습니다. |
| `test_deident_blocked.py` | 비식별화가 실패한 영상은 막힌 것으로 표시됩니다. |
| `test_deident_coordinates.py` | 비식별화 얼굴 좌표 JSON의 업로드 주소를 잡에 늘 싣고, 올렸다는 보고가 있을 때만 키를 남깁니다. 영구 실패하면 좌표 JSON도 지웁니다. |
| `test_deident_dispatch.py` | 비식별화 잡에 `remote`·`staged`가 없고 좌표 업로드 주소가 늘 실리는지, 전역 동시 상한을 지키는지, 서비스 `deid` 연결과 프로젝트의 서버 선택이 422인지, 마이그레이션 0105가 `deid` 연결과 `settings.deident`만 지우는지 검사합니다. |
| `test_deident_skip_manual.py` | 비식별화를 끈 프로젝트에서 실패로 확정된 영상은 관리자가 건너뛰면 건너뜀이 되고 재생본 단계가 열리며, 누가 넘겼는지와 직전 실패 사유가 감사 기록(`video.deident_skip_manual`)에 남습니다. 관리자 외 403, 켠 프로젝트 409 `deident_enabled`, 실패가 아닌 영상 409 `deident_not_failed`. 타임라인은 넘기기 전 실패 줄에만 `skippable`을 엽니다. |
| `test_deident_switch_off.py` | 비식별화를 끈 프로젝트의 대기 영상은 서버로 보내지 않고 건너뜀으로 넘어가 재생본 단계가 열리며 감사 기록이 남습니다. 처리 중인 영상은 끝나기를 기다리고, 끈 프로젝트의 실패는 다시 넣지 않습니다. 감사 기록의 시도 수는 단계 이력에 남은 실제 전송 횟수이고 상한에 센 수는 따로 싣습니다. |
| `test_job_queue_wait.py` | 변환·재생본·비식별화 잡이 큐에서 기다리는 동안 리스 만료로 회수되지 않는지(투입 리스가 큐 대기 시간을 덮고, 시작 하트비트가 리스를 평소 길이로 줄임), 회수가 옛 토큰을 메시지 취소로 넘기는지, 시작하지 못한 시도를 "응답이 끊겼습니다"로 적지 않는지 검사합니다. |
| `test_deident_reclaim.py` | 비식별화·재생본 잡 회수가 단계 이력을 닫는지, 회수된 영상을 먼저 다시 보내는지, 회수한 시도의 늦은 완료 보고를 산출 근거가 맞을 때만 이어받는지 검사합니다. |
| `test_deident_stage.py` | 비식별화 단계의 순서와 안전장치를 검사합니다. |
| `test_deident_transient.py` | 비식별화 잡의 일시 오류가 이어지는 동안의 재시도 간격·상한, 준비 타임라인의 대기 표시, 재시도 요청 거절 사유를 검사합니다. |
| `test_deident_unmasked.py` | 비식별화를 켰는데 가리지 않은 영상은 외부 분석에 보내지 않고, 지금 가릴 수 있게 합니다. |
| `test_deident_waiting.py` | 비식별화를 켠 프로젝트에 새로 올린 영상이 규격 검증·변환을 기다리는 동안 비식별화 단계를 대기로 표시하고 재시도를 받지 않는지, 켜기 전에 준비를 마친 영상은 실행 필요와 재시도로 표시하는지 검사합니다. |
| `test_frame_input_guard.py` | 썸네일 프레임 추출 명령의 입력 앞에 프로토콜·디먹서 제한(`reborn_transcode.input_guard`)이 붙는지 검사합니다. |
| `test_extra_concurrency.py` | `videos.metadata`(JSONB)에 동시에 써도 키가 사라지지 않는지 검사합니다. |
| `test_ingest_batch_project_scope.py` | 직접 등록과 버킷 등록이 다른 프로젝트의 묶음 id를 404 `batch_not_found`로 거절하고 그 묶음에 영상이 붙지 않는지, 묶음의 메타 양식 대상이 묶음 프로젝트의 영상만 싣는지 확인합니다. |
| `test_ingest_role_gate.py` | 영상 반입 네 경로(직접 등록·버킷 키 등록·폴더 등록·업로드 시작)가 검수자를 403으로 거절하고 행을 남기지 않는지 검사합니다. |
| `test_manual_clip.py` | 사람이 직접 넣는 씬 구간을 검증합니다. 기존 클립과 겹치는 구간과 동시에 넣은 겹치는 구간을 막는지, 출처가 응답에 실리는지, 허용 오차 안에서 영상 길이를 넘은 끝을 영상 끝으로 줄이는지, 만든 구간에 라벨링 작업이 하나만 열리는지도 봅니다. |
| `test_media_search_literal.py` | 영상·클립 검색어를 글자 그대로 찾는지(`%`·`_`·NUL)와 클립 분모를 검사합니다. |
| `test_media_resolver.py` | 매체 결정자가 용도별 입력·재생 매체를 고르는 규칙(사본·마스터 우선, 재생본 키 확인, scheme 허용 목록)을 DB 없이 검증합니다. |
| `test_prep_stage.py` | 준비 여정 축(prep_stage)의 판정·합계 불변식·필터 일치·배치 분포를 검증합니다. |
| `test_prep_timeline.py` | 준비 타임라인과 단계 이력을 검증합니다. 타임라인은 단계별 현재 상태와 사유만 싣고 시각·소요·시도 수는 싣지 않습니다. |
| `test_probe_bit_rate.py` | 영상 비트레이트 표시 규칙을 검증합니다. ffprobe 측정값을 먼저 쓰고, 측정값이 없는 영상은 용량과 재생 시간으로 계산한 평균값을 영상 상세가 보여 주는지, 측정값이 규격 검증 단계 기록에 남는지 봅니다. |
| `test_probe_retry.py` | 규격 검사의 일시 오류(망 순단·5xx·제한 시간 초과)를 대기로 두고 백오프 뒤 다시 검사하는지, 연속 상한을 넘으면 `error`로 확정하는지, 겹친 배치가 아무 영상도 검사하지 않는지 검사합니다. |
| `test_publish_stage.py` | publish 단계의 재생본 생성·분석본 확정·스테이징 정리를 검증합니다. |
| `test_publish_transient.py` | 재생본 잡의 연속 일시 오류에 재시도 간격을 늘리고 상한에서 실패로 확정하는지 검증합니다. |
| `test_reclaim_cap.py` | 시작 하트비트 뒤 회수된 변환·재생본·비식별화 시도를 시도로 세고 상한에서 실패로 확정하는지, 시작하지 못한 시도는 세지 않는지 검증합니다. |
| `test_scene_split_gate.py` | 장면 분할이 준비가 끝난 영상만 자르는지(규격 검증·비식별화 전 원본 금지), 선행 조건 실패가 조건이 갖춰지면 다시 열리는지, 운영자 재시도(권한·감사·클립 있으면 409)와 MI 게이트와 같은 판정 함수를 쓰는지 검증합니다. |
| `test_scene_split_service.py` | 씬 분할 잡 프로토콜의 생산(build_jobs), 소비(DB 없는 러너), 판정(complete)을 검증합니다. |
| `test_source_archive_order.py` | 원본을 보관 경로로 옮기는 순서(복사, `source_uri` 갱신과 커밋, 원래 키 삭제)를 검사합니다. 어느 단계에서 멈춰도 행이 없는 파일을 가리키지 않습니다. |
| `test_source_checksum.py` | 원본 체크섬의 스트리밍 해시와 beat 채우기를 검증합니다(참조: `services/media/checksum`). |
| `test_source_retention.py` | 원본은 파생물이 모두 쓰인 뒤에만 정리하는지 검증합니다. |
| `test_source_url_routing.py` | source-url presign(단일 클라이언트)과 source_uri 파싱을 검증합니다. |
| `test_staging_sweep.py` | 고아 스테이징 정리가 보고 없이 끝난 시도의 파일만 지우는지 검사합니다. 현재 토큰·회수 표식 토큰·마스터·유효 기간 안의 파일·이 DB에 없는 영상의 파일은 남기고, 한 실행의 삭제 수 상한을 지킵니다. |
| `test_staging_tokens.py` | 시도마다 다른 스테이징 키를 검증합니다. 회수된 변환·비식별화 시도가 늦게 올린 파일이 승격된 마스터와 좌표를 바꾸지 못합니다. |
| `test_storage_register_permission_first.py` | 키 목록 등록과 폴더 등록이 저장소 대상 확인보다 권한을 먼저 보아, 관리자가 아닌 멤버가 저장소 설정 여부 대신 403을 받는지 검사합니다. |
| `test_storage_client_timeouts.py` | 저장소 클라이언트의 시한·재시도(일반 5초·60초·재시도 2회, 대용량 600초·재시도 4회)가 실제 클라이언트에 실리는지, 두 용도를 따로 캐시하는지, 서버 복사·멀티파트 완료·패키지 업로드·본문 스트림은 대용량을, 화면 요청 안의 호출은 일반을 고르는지 확인합니다. |
| `test_storage_app_paths.py` | 앱 경로(`work/`·`archive/` 포함)의 원본 등록 거절, 앱 경로에 닿는 prefix(`intak`·`t`·`/`) 거절, 폴더째 등록 작업의 키별 재검사, 원본 정리가 자기 `intake/{video_id}/` 키만 지우는지, 앱이 쓰는 키 생성 함수의 결과가 모두 `APP_PREFIXES` 안에 있는지 검사합니다. |
| `test_storage_prefix_import.py` | 폴더째 등록(서버 작업)을 검증합니다. 큐, beat 러너, 재개 규약과 같은 폴더를 다시 등록할 때 이미 등록한 원본을 건너뛰는지(키를 골라 등록하는 경로는 다시 등록) 봅니다. |
| `test_storage_register.py` | storage 일괄 등록을 검증합니다. 등록한 영상이 `cleared`로 시작하는지, 저장소에 없는 키를 입력하면 하나도 등록하지 않고 422로 거절하는지, 분석 요청까지의 흐름을 봅니다. |
| `test_thumbnail_deident_source.py` | 비식별화를 쓰는 영상의 썸네일·대표 이미지를 비식별화본이 확정된 뒤에만 그 파일에서 뽑고, 원본에서 뽑은 옛 썸네일을 다시 뽑게 하는지 검증합니다. |
| `test_thumbnail_throughput.py` | 썸네일·포스터의 한 주기 상한(40·20건)과 시간 예산(40·15초, 합은 주기 60초보다 짧음)을 봅니다. 예산을 넘기면 남은 항목을 다음 주기로 미루는지, 락을 잡은 실행이 있으면 후보를 고르지 않는지, 후보를 프로젝트마다 번갈아 고르는지(`fair_select`)도 봅니다. |
| `test_thumbnail_backfill.py` | 수동 일괄 썸네일(`clip_thumbnail.backfill`, 운영 스크립트)이 정기 작업과 같은 규칙인지 봅니다. 비식별화 전에는 만들지 않는지, 확정 뒤 비식별화본에서 뽑아 출처를 남기는지, 비식별화를 쓰지 않는 영상은 원본에서 뽑고 출처를 기록하는지, 정기 작업이 락을 잡고 있으면 시작하지 않는지 확인합니다. |
| `test_thumbnail_retry.py` | 썸네일 다시 만들기를 검증합니다. |
| `test_transcode_gate.py` | 파이프라인이 실제 경로에서 켜지는 게이트 지점을 검증합니다. |
| `test_transcode_retry.py` | 변환 재시도를 검증합니다. |
| `test_transcode_service.py` | DB를 쓰지 않는 잡 프로토콜을 검증합니다. |
| `test_transcode_skeleton.py` | 스케일 아웃 골격을 검증합니다. 판정, 빌드 경쟁, 회수를 봅니다. |
| `test_transcode_transient.py` | 변환의 연속 일시 오류에 재시도 간격을 늘리고 상한에서 실패로 확정하는지, 잡 URL이 큐 대기와 처리 상한을 덮는지 검증합니다. |
| `test_transcode_verify.py` | 변환 산출 검사를 검증합니다. 판정은 회신을 받는 서버 한 곳에서 합니다. 검사 시간 초과는 시도를 쓰지 않는 일시 오류입니다. |
| `test_upload_reject_cleanup.py` | 업로드 완료 확인이 크기 상한으로 거절한 멀티파트 객체를 지우는지, 삭제가 실패해도 거절(422)이 그대로인지, 상한 안의 업로드는 객체를 남기는지 검사합니다. |
| `test_upload_storage_off_loop.py` | 업로드 시작·완료의 저장소 호출(멀티파트 시작·파트 목록·합치기·HEAD)이 이벤트 루프 스레드 밖에서 도는지 검사합니다. |
| `test_upload_multipart.py` | 멀티파트 업로드를 검증합니다. 3GB가 90%에서 끊겨도 끊긴 파트부터 이어 올립니다. |
| `test_video_buckets.py` | 반입 상태 분류(ingest_bucket), 커서 페이지네이션, 상태별 집계를 검증합니다. |
| `test_video_probe.py` | ffprobe 규격 게이트를 검증합니다. check_spec 규칙과 변환 대상 판정을 봅니다. |
| `test_video_probe_reset.py` | 규격 재검사 엔드포인트(POST /projects/{pid}/videos/{vid}/probe/reset)를 검증합니다. |
| `test_video_metadata_isolation.py` | 영상 등록 요청의 `metadata`가 `extra`에 들어가지 않는지, 포스터 주소를 고정 버킷과 썸네일 워커 키 형식일 때만 서명하는지 검사합니다. |
| `test_video_original_filename.py` | 원래 파일 이름의 요청 검증(경로 떼기·제어 문자·길이)과 업로드 등록·버킷 등록이 `extra["original_filename"]`에 저장하는지 검사합니다. |
| `test_video_register_rules.py` | 영상 등록 입구(0바이트·상한 초과), 메타 지우기·방영일 범위, 저장소 등록 기록을 검사합니다. |
| `test_video_source.py` | Vidstack용 presigned GET URL 엔드포인트(GET /videos/{id}/source-url)를 검증합니다. |
| `test_video_update.py` | 영상 메타 편집(PATCH /videos/{id})을 검증합니다. |
| `test_meta_import.py` | 등록 묶음 영상 메타 일괄 입력을 검사합니다. 양식이 묶음 영상만 모두 싣는지, 영상 ID·파일·제목 순서의 찾기와 찾지 않는 경우(일치 없음·중복 일치·같은 영상 여러 행), 필드별 오류와 빈 셀, 권한, 반영의 감사 기록과 전부 아니면 전무를 봅니다. |
| `test_meta_sheet.py` | 메타 일괄 입력의 표 파일 읽기·쓰기(`meta_sheet`)를 DB 없이 검사합니다. 머리글 찾기, 수식 셀, CSV 인코딩, 거절 코드, 행 수 상한, 방영일 형식을 봅니다. |
| `test_videos.py` | Video 도메인을 검증합니다. 등록 초기 상태, 영상 권리 상태 제약(`cleared`만 허용), 권한, 422, soft delete를 봅니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
MAIU dispatch를 거치는 등록·변환 게이트 테스트도 테스트 대상이 media 함수라 여기 둡니다. 가장 큰 폴더라 파일 이름 접두어(`test_video_*`·`test_transcode_*`·`test_storage_*`·`test_deident_*`)로 묶어 읽습니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- `test_video_original_filename.py`는 버킷 등록 헬퍼(`_new_project`·`_register`)를 `tests.media.test_storage_register`에서 가져옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
