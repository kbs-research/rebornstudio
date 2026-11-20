# services/maiu/reconciler

분석 결과 회수 작업입니다. MI에 보낸 분석 잡을 등록, 분석, 장면 요약 순서로 폴링해 끝까지 진행시킵니다. 결과는 장면 단위
클립과 요약으로 되돌립니다. 워커 beat가 5초마다 `run_once`를 부릅니다. 상태 전이 표는 `stages.py` 모듈 docstring에 있습니다.
잡 상태는 `analysis_job`의 `stage`(MI 단계)와 `status`(중립 상태)에 있습니다. 단계 전이는 `analysis.job_state.advance`로 합니다.

## 주요 파일

패키지 바깥에서는 `reconciler` 경로만 씁니다. `reconciler/__init__.py`가 하위 모듈의 최상위 이름을 모두 다시
내보내므로, 워커·테스트·시드 스크립트는 하위 모듈을 몰라도 됩니다.

| 파일 | 역할 | 주요 이름 |
|---|---|---|
| `__init__.py` | 파사드. 하위 모듈 이름을 다시 내보냅니다 | — |
| `jobs.py` | 워커 진입점. advisory lock으로 한 번에 한 워커만 단계를 실행하고, 슬롯이 정상 사이클과 보류 사이클 중 하나를 고릅니다. 한 사이클의 MI 요청은 httpx 클라이언트 하나를 같이 씁니다(`naver_client.shared_session`) | `run_once` · `_run_stages` · `_RECONCILER_LOCK` |
| `stages.py` | 단계 1~4(등록·분석·장면 요약·보류 승격·멈춘 잡 회수·보류 재투입)와 배치 선택 | `bulk_*` · `promote_failures_to_deferred` · `sweep_stale_in_flight` · `retry_deferred` · `_lock_batch` · `_resume_status` · `_analysis_id`. 요약을 받지 못한 잡(요약 요청 실패, MI의 요약 실패 응답, 요약 요청 번호 없이 클립만 있는 잡)은 `done`으로 닫고 단계 이력에 `summarize` 실패를 남깁니다. 영상의 막힘 판정은 `media.video`가, 요약 재요청은 `maiu.service.retry_failed`가 맡습니다. 단계 함수 여섯은 배치마다 `ThrottleGate`를 둡니다. 429를 받은 연결의 남은 잡은 조회하지 않습니다. 진행 중인 잡은 다음 조회 시각(`payload.next_poll_at`, 30초에서 잡이 오래될수록 60초까지)이 지나야 배치에 들어갑니다. 배치는 잡마다 행을 다시 잠그고(`_relock`) 그 잡을 끝내면 바로 커밋합니다(`_job_tx`) |
| `errors.py` | 단계 예외를 일시 오류(백오프 후 재시도)와 확정 실패로 나눕니다. 판정은 이 파일 한 곳에서 합니다 | `_on_stage_error` · `_STAGE_BY_CALLER` · `ThrottleGate`. 잡마다의 백오프(5초부터 두 배씩, 최대 120초)는 그 잡만 미루므로, 진행 중인 분석이 많으면 한 사이클에 요청량 제한(429)을 여러 번 받습니다. `ThrottleGate`는 429를 받은 분석 연결(잡에 기록한 연결·워크스페이스)의 남은 잡을 조회하지 않습니다. 실제 분석 연결이면 Retry-After(없으면 30초) 동안 Redis 키(`maiu:throttle:…`)에 기록해 다음 사이클에도 막습니다. 일시 오류 감사 이벤트와 경고는 연속이 시작될 때 한 번만 남깁니다 |
| `ingest.py` | 분석 결과를 영상 메타와 클립에 반영합니다. 쓰기는 media가 소유한 함수를 거칩니다 | `_create_clips_from_summary` · `_attach_scene_summaries` · `_apply_import_metadata` |
| `dto.py` | 외부 분석 서비스의 응답을 클립 입력과 납품 필드 용어로 바꾸는 순수 함수. DB와 외부 호출이 없습니다 | `_group_segments_by_scene` · `_merge_keywords` |
| `common.py` | 배치 크기, 일시 오류 표시, 감사 이벤트 대상 id | `_BATCH` · `TRANSIENT_MARK` · `_job_target` |

## 설계 이유

- 모듈 사이의 의존 방향은 아래와 같습니다. 앞쪽 모듈은 뒤쪽 모듈을 import하지 않습니다. 그래서 하위 모듈 사이에 순환이 생기지 않습니다.

  ```
  common · dto → errors · ingest → stages → jobs → __init__
  ```

- 단계 함수는 한 파일(`stages.py`)에 둡니다. 단계 함수는 `_lock_batch`, 클라이언트 호출, 판정,
  `_on_stage_error` 순서를 함께 따르고, 파일을 위에서 아래로 읽으면 상태 머신의 순서가 됩니다. 단계마다 파일을 나누면 공통 순서가 여러 파일에 흩어집니다.
- 폴링 배치는 잡을 단계(`stage`)로 고르고 어댑터(`adapter_key == MAIU_KEY`)로 거릅니다. `status`는 진행 중인 단계를 모두 `running`으로 합치므로 단계를 구분할 수 없습니다. 어댑터로 거르지 않으면 다른 스택(장면 분할 등)의 잡을 MI 결과 회수 작업이 가져갑니다.

### 주의

- `_on_stage_error`는 호출한 함수의 이름(`inspect.stack()[1].function`)으로 단계를 정합니다. 단계 함수 이름을
  바꾸면 `_STAGE_BY_CALLER`와 `scripts/ci/check-event-labels.mjs`의 `ENUMS`도 함께 고칩니다.
- 테스트에서 `monkeypatch.setattr`로 이름을 바꿀 때는 그 이름을 실제로 찾는 하위 모듈의 이름을 바꿉니다.
  파사드(`reconciler`)의 이름을 바꾸면 하위 모듈의 전역 이름은 바뀌지 않습니다. 클라이언트 교체는
  `maiu_client.set_client`·`maiu_client.get_client`로 하므로 이 문제가 없습니다.
- import-linter 허용 줄은 가져오는 쪽 하위 모듈 이름으로 적습니다(`apps/api-gateway/pyproject.toml`).

## 의존성

- 부르는 것: `models`(AnalysisJob·Clip·Video) · `services.audit`·`error_text` · `services.joblock.single_runner` ·
  `maiu.client`·`naver_client`·`slot_counter`·`service._synthetic_uuid` · `analysis`(job_state·scene_options·
  registry) · `media`(clip·video·resolver·stage_runs) · `work.service`(늦은 import)
- 부르는 쪽: `workers/worker-io`(`run_maiu_reconciler`에서 `run_once` 호출) · 테스트(`tests/maiu/`) · `scripts/dev/seed_dev_clip.py`·`seed_dev_volume.py`
