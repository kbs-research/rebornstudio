# tests/maiu

MI 연동 도메인(`services/maiu`) 테스트입니다. 분석 결과 회수 작업(`reconciler`)의 단계와 폴링 제한, 분석 요청 게이트와 보류, 취소 큐, 슬롯 카운터, 비용, 분석 자원 진단, 네이버 클라이언트를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_maiu_job_dto_fields.py` | 분석 요청 응답과 잡 목록이 영상을 `video_id`로 싣고, 호환 필드 `work_item_id`에 같은 값을 싣고 스키마에 사용 중단으로 표시하는지 검사합니다. |
| `test_dispatch_ready_gate.py` | 분석 요청이 규격 검증 통과·길이 있는 영상만 보내는지, 고른 영상을 막은 사유(`held_reasons`)를 돌려주는지, 반입 집계가 검증 전 영상을 요청 대기로 세지 않는지 검사합니다. |
| `test_dispatch_skips_clipped.py` | 영상 티켓이 남아 있어도 클립이 이미 있는 영상은 프로젝트 전체 요청과 선택 요청 모두 MI에 보내지 않는지(사유 `not_waiting`), 영상 목록의 MI 단계가 분석 완료인지 검사합니다. |
| `test_dispatch_registers_once.py` | 분석 요청 한 번에 MI 에셋이 한 번만 등록되는지 검사합니다. 요청이 만든 잡은 등록 완료 대기에서 시작하고, 에셋 번호가 이미 있는 대기 잡은 분석 결과 회수 작업이 다시 등록하지 않습니다. |
| `test_dispatch_input_ext.py` | 확장자 없는 업로드 원본이 분석 요청 때 같은 경로에 확장자를 붙인 키로 옮겨져 형식 오류 없이 나가는지, 분석본을 만들지 않는지 검사합니다. |
| `test_dispatch_deferred_reason.py` | 보류된 분석 요청의 사유 표시. 종류는 상태 코드로 정하고(429 요청량 제한·408/425 응답 지연·5xx 오류·응답 없음 연결 실패), 영상 응답 `dispatch_deferred`가 짧은 이름·전체 사유·다음 재요청 시각을 싣습니다. 종류가 없는 이전 표시는 일반 이름으로 싣습니다. |
| `test_dispatch_deferred_scope.py` | 분석 요청 본문에 다른 프로젝트 영상 id를 넣어도 그 영상의 보류 표시를 지우지 않는지 검사합니다. |
| `test_dispatch_gate_and_deferral.py` | 분석 요청 게이트(규격 검증 실패)와 요청 대기 분류, 일시 보류의 표시와 프로젝트 클립 길이로 다시 요청하는지를 검사합니다. 요청에 클립 길이를 실으면 422인지는 `org/test_fixed_options.py`가 봅니다. |
| `test_fake_client_cross_process.py` | 가짜 MI 클라이언트가 프로세스를 나눠도 같은 답을 주는지 검사합니다. 한 인스턴스가 발급한 에셋·분석 id를 새 인스턴스가 완료로 읽는지, 가짜가 발급하지 않은 id는 모르는 에셋으로 답하는지, 단계마다 새 인스턴스로 돌려도 잡이 `done`까지 가는지 봅니다. |
| `test_maiu_audit_target.py` | 분석 작업 감사 이벤트의 대상 id가 잡이 분석하는 클립·영상 id인지 검사합니다(잡 번호로 만든 `00000000…` 값을 쓰지 않습니다). |
| `test_maiu_cancel_queue.py` | MAIU 분석 취소와 Celery 큐 분리를 검증합니다. |
| `test_maiu_cost.py` | MAIU 비용 통계가 영상 길이로 비용을 예측하는지 검증합니다(1시간 ≈ 1만원). |
| `test_maiu_dispatch_no_input.py` | 경로가 없는 영상의 분석 요청을 검증합니다. 요청하지 않고 읽을 수 있는 사유를 남깁니다. |
| `test_maiu_probe_gate.py` | ffprobe 규격 게이트를 검증합니다. dispatch는 규격 위반(rejected) 영상을 MI에 보내지 않습니다. |
| `test_maiu_provisioning.py` | MI 분석 자원 진단을 검증합니다. 진단은 읽기만 하고, 없는 워크스페이스와 읽지 못한 워크스페이스를 다른 안내로 알리고, 자원을 만드는 API 경로가 없는지 봅니다. |
| `test_poll_throttle_gate.py` | 폴링 배치에서 429를 받으면 같은 연결의 남은 잡을 조회·기록하지 않는지, 다른 연결과 429가 아닌 일시 오류는 막지 않는지 검사합니다. |
| `test_poll_intensity.py` | MI 폴링 강도 규칙을 검사합니다. 진행 중인 잡의 다음 조회 시각, 사이클마다 httpx 클라이언트 하나를 같이 쓰는지, 429를 받은 연결을 Retry-After 동안 조회하지 않는지, 일시 오류 감사 이벤트를 연속이 시작될 때 한 번만 남기는지, 외부 id를 받은 잡을 바로 커밋하는지 봅니다. |
| `test_maiu_reconciler.py` | MAIU 5단계 reconciler 흐름을 Fake 어댑터로 검증합니다. |
| `test_maiu_scene_summary.py` | MAIU 장면 요약 클라이언트를 검증합니다. NaverMaiuClient 매핑과 FakeMaiuClient를 봅니다. |
| `test_maiu_slot_counter.py` | maiu_slot_counter가 normal과 deferred를 10:1로 섞는지 검증합니다. |
| `test_maiu_sweep_stale.py` | MAIU reconciler의 sweep_stale_in_flight를 검증합니다. |
| `test_maiu_path_segments.py` | MI 클라이언트가 워크스페이스·프로젝트 값을 경로 한 칸으로 인코딩하고 `.`·`..`을 거절하는지 확인합니다. |
| `test_naver_maiu_client.py` | NaverMaiuClient를 단위 테스트합니다. |
| `test_naver_maiu_live.py` | NaverMaiuClient가 실제 NCP API를 호출하는 통합 테스트입니다. 환경 변수로 켭니다. |
| `test_scene_grouping.py` | _group_segments_by_scene 순수 헬퍼를 DB 없이 단위 테스트합니다. |
| `test_summary_fail_blocked.py` | 장면 요약을 받지 못한 영상(요약 요청 실패, MI의 요약 실패 응답, 요약 요청 번호 없이 클립만 있는 잡)을 막힘으로 분류하는지, 요약 재요청이 분석을 다시 하지 않고 요약만 다시 요청하는지 검사합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_naver_maiu_live.py`는 실 NCP를 부르는 `live` 마커 테스트라 환경 변수로 켤 때만 실행됩니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
