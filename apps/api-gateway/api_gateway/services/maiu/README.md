# services/maiu

NAVER Media Intelligence(MI, 옛 이름 MAIU) 연동 도메인입니다. 영상을 MI에 분석 요청하고 등록, 분석, 장면 요약 단계를 차례로 폴링합니다. 결과는 장면 단위 클립과 요약으로 되돌립니다.
분석 요청 게이트, 일시 오류 보류와 재요청, 비용 집계, MI 워크스페이스·프로젝트의 읽기 전용 진단도 맡습니다. 라우터는 `service`·`provisioning`을 부르고, 워커는 `reconciler`를 부릅니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `service.py` | 분석 요청(`dispatch`)과 요청 전 게이트, 일시 오류로 보내지 못한 영상의 보류 표시(`dispatch_deferred`)와 재요청(`redispatch_deferred`), 잡 목록·상세, 비용 집계, 실패 잡 재시도. 장면 요약을 받지 못한 잡은 재시도가 같은 분석 번호로 요약만 다시 요청합니다(`_resummarize`) |
| `client.py` | MI 클라이언트 Protocol과 `FakeMaiuClient`. `MAIU_ENABLED`가 켜지면 `NaverMaiuClient`를 씁니다. `get_client(target)`은 분석 대상(연결·워크스페이스·MI 프로젝트)별로 클라이언트를 캐시합니다. 잡의 폴링과 취소는 잡에 기록한 대상으로 `client_for_job`을 씁니다. MI가 받는 입력 확장자(`INPUT_EXTS`)도 여기에 있습니다 |
| `naver_client.py` | NCP MI REST 클라이언트(HMAC-SHA256 서명). 등록·상태·분석·요약·장면 요약·목록 조회. 경로 선택 설정 `maiu_use_index_api`. 429·408·425·5xx·전송 오류는 `MaiuTransientError`로 내고 Retry-After 초를 싣습니다. `shared_session` 블록 안의 요청은 httpx 클라이언트 하나를 같이 씁니다 |
| `provisioning.py` | 워크스페이스·연동 버킷·MI 프로젝트의 읽기 전용 진단(`diagnose`)과 분석 대상 점검이 함께 쓰는 판정(`bucket_matches`·`is_not_found`). 벤더 계정에 자원을 만들지 않습니다. 워크스페이스와 MI 프로젝트는 운영자가 NCP 콘솔에서 만듭니다(`docs/install.md`) |
| `reconciler/` | 분석 잡 상태 머신. 단계 폴링, 결과 반영, 일시 오류 백오프, 보류 승격·재투입([README](reconciler/README.md)) |
| `slot_counter.py` | 분석 결과 회수 작업(`reconciler`)의 정상 사이클과 보류 재처리 사이클을 10:1로 배분합니다. Redis Lua로 원자적으로 셉니다 |

## 설계 이유

- 분석 잡 상태는 `analysis_job`에 씁니다. MI의 원래 단계(`registering`·`summarizing` 등)는 `stage`에, 중립 상태는 `status`에 둡니다(`services/analysis/README.md`).
- 일시 오류와 영구 실패를 구분합니다. 폴링은 같은 엔드포인트를 반복해 호출하므로 스로틀과 타임아웃이 정상 경로에 섞입니다. 일시 오류를 실패로 세면 오래 걸리는 영상일수록 실패로 끝납니다. 판정은 `reconciler/errors.py`의 `_on_stage_error` 한 곳에 둡니다.
- 자격증명, API 주소, 워크스페이스, MI 프로젝트는 프로젝트가 고른 분석 연결과 분석 대상에서 읽습니다(`services/platform_credentials.py`). 세 값을 모두 고른 대상만 실제 클라이언트를 받습니다. 기본값은 없습니다.
- 분석을 요청할 때는 그때 쓴 대상을 잡의 `payload.maiu`에 기록합니다. 이후 폴링은 프로젝트 설정을 다시 읽지 않습니다. 다시 읽으면 요청 뒤에 대상이 바뀐 잡이 다른 MI 프로젝트에서 에셋을 찾다가 404를 받기 때문입니다. 대상을 고르는 규칙은 `services/stacks/project_stack.py`에 있습니다.
- 분석 요청 게이트는 준비 판정(`media.video._prep_unfinished_expr`)을 씁니다. 규격 검사를 통과하고, 길이가 있고, 준비 단계가 끝난 영상만 보냅니다. 막은 영상은 사유별 수를 돌려줍니다. 클립이 이미 있는 영상은 티켓이 남아 있어도 보내지 않습니다(사유 `not_waiting`).
- 해상도, 재생 시간, fps 같은 메타는 MI 응답(`get_import_status`의 `metaData`)에서 받아 저장합니다. 요청 전 ffprobe 검사(`media/probe.py`)는 분석 비용을 아끼기 위한 규격 게이트입니다.
- 장면 요약을 받지 못한 잡은 `done`으로 닫고 단계 이력에 `summarize` 실패를 남깁니다. 영상은 요약을 다시 받을 때까지 막힘으로 분류됩니다(`media.video._summary_blocked_expr`). 재시도(`retry_failed`)는 같은 조건으로 그 잡을 골라 요약만 다시 요청하고 잡을 `summarizing`으로 되돌립니다. 분석을 다시 요청하지 않아 분석 비용은 다시 들지 않고, 이미 작업에 풀린 클립과 work_item은 그대로 둡니다. 요약 요청 한 건은 비용 집계의 단계별 호출 수(`by_stage.summarize_requested`)에 더해집니다. 추정 비용은 처리 완료 영상의 길이로 한 번만 셉니다.
- 테스트는 `FakeMaiuClient`로 파이프라인 전체를 실행합니다. Fake도 MI가 받지 않는 확장자를 실제 MI처럼 거절합니다.
- `FakeMaiuClient`는 상태를 인스턴스 메모리에 두지 않고 id의 접두어(`fake-asset-`·`fake-analysis-`)로 정합니다. 분석 연동을 끈 서버(`MAIU_ENABLED=false`)에서는 api가 에셋을 등록하고 워커가 상태를 조회하는데, 두 프로세스가 서로 다른 인스턴스를 쓰기 때문입니다. 메모리에 기록하면 워커가 에셋을 찾지 못해 잡이 등록 완료 대기에 머뭅니다. 결과는 고정된 장면 두 개입니다. 분석 요청은 영상 길이를 싣지 않으므로 장면 끝이 영상 길이를 넘을 수 있고, 클립 저장(`media.clip.create_scene_clip`)이 영상 끝으로 줄입니다.
- 분석 연동이 꺼진 서버의 연결 점검과 분석 대상 점검은 외부에 접속하지 않고 `FAKE_MODE_DETAIL` 문장으로 정상을 돌려줍니다. 접속하면 개발용 주소에서 실패가 기록되어 그 연결을 분석 대상으로 고를 수 없습니다.

## 의존성

- 부르는 것: `models`(AnalysisJob·Video·Clip·WorkItem·Project), `services.audit`·`platform_credentials`·`transient`·`error_text`·`joblock`, httpx, Redis(`slot_counter`), 허용 목록의 `analysis.*`·`media.video`·`media.clip`·`media.storage`·`media.resolver`·`media.stage_runs`·`work.service`·`ops.diagnostics`
- 부르는 쪽: `api/v1/maiu.py`·`projects.py`(분석 자원 진단)·`storage.py`, `stacks.project_stack`·`stacks.connections`, `workers/worker-io`(`run_maiu_reconciler`), `scripts/dev/seed_dev_clip.py`·`seed_dev_volume.py`
- 설정: `MAIU_ENABLED`, `MAIU_SCENE_RANGE`·`MAIU_PERSON_COUNT` 등 동작 조정값. 키의 뜻은 `docs/env-catalog.md`에 있습니다.
