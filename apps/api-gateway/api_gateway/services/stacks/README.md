# services/stacks

교체할 수 있는 외부 스택 도메인입니다. 자격증명 연결(저장소·MI·캡션 검수 모델)을 관리합니다. 프로젝트마다 분석 대상과 저장소 대상을 고르는 규칙, 점검, 잠금도 맡고, 저장소 백엔드 어댑터를 둡니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `connections.py` | 연결 CRUD와 저장 직후 실제 접속 점검(`check_saved`·`probe_one`, 실패해도 저장은 남기고 실패를 행에 기록), 주기 재점검(`recheck_all`), 점검 집계(`health_summary`), 프로젝트 설정용 연결 선택지(`choices_for_project`). 시크릿은 Fernet으로 암호화해 저장하고 응답에 싣지 않습니다. 생성·수정은 주소 검사(`address_problem`)를 거칩니다. 요청이 나가는 곳을 정하는 키(`ROUTING_KEYS`, 판정 표 `packages/reborn-contracts/connection-address.json`)를 바꾸는 수정은 저장된 시크릿이 있으면 같은 요청에 시크릿을 다시 받습니다(422 `secret_required_for_address_change`). 프로젝트가 쓰는 연결의 삭제와 쓰는 버킷을 허용 범위에서 빼는 수정은 409(`connection_in_use`·`allowed_bucket_in_use`)로 거절하고 막는 프로젝트 이름을 응답에 싣습니다 |
| `project_stack.py` | 프로젝트별 분석 대상(MI 연결·워크스페이스·MI 프로젝트)의 선택지(`choices`)·점검(`check`)·저장(`save`), 영상 등록과 분석 요청 직전 관문(`gate`), 저장소 대상과 함께 보는 주기 점검(`recheck_all`)과 결과 기록(`record`). 분석 연동이 꺼진 서버에서 분석 대상을 고르지 않았으면 점검은 연동이 꺼져 있다는 정상 항목 하나입니다 |
| `project_storage.py` | 프로젝트별 저장소 대상(저장소 연결·버킷)의 선택지·점검·저장. 점검은 버킷 접근·목록·쓰기·읽기·삭제·업로드 주소 서명·CORS를 봅니다. CORS가 업로드(PUT)를 모든 출처(`*`)에 허용하면 "브라우저 업로드 허용" 항목이 경고이고 허용 주소를 서비스의 웹 주소로 좁히라고 안내합니다. 경고는 저장과 영상 등록을 막지 않고 주기 점검 알림도 내지 않습니다. 버킷은 연결의 허용 범위(설정의 `bucket`과 `allowed_buckets`의 합) 안에서만 고르고 쓰며, 판정은 `require_allowed` 하나가 합니다(선택지·저장·등록 직전·`api/v1/storage.py`의 탐색·등록 라우트). 연결 수정이 범위를 좁힐 때 쓰던 버킷을 잃는 프로젝트는 `users_losing_bucket`이 같은 판정으로 찾습니다 |
| `stack_lock.py` | 두 대상이 함께 쓰는 잠금 판정(`lock_state`)과 행 잠금(`lock_for_change`·`hold_for_new_video`), 미설정 오류 코드(`storage_stack_unset`·`analysis_stack_unset`) |
| [`storage_adapters/`](storage_adapters/README.md) | 저장소 어댑터(NCP, GCS 더미). 계약은 `reborn_contracts.storage_backend`에 있습니다 |

두 대상은 프로젝트 설정의 `stack` 블록(`platform_credentials.AnalysisTarget`·`StorageTarget`)에 저장합니다. 이 블록에 쓰는 곳은 이 패키지뿐입니다.

## 설계 이유

- 연결 주소(`endpoint`·`base_url`)는 서버가 요청을 보내는 곳이라 생성·수정 때 검사합니다. http(s)만 받고 사용자 이름·비밀번호를 넣지 못합니다. 루프백·링크 로컬(클라우드 메타데이터 169.254.169.254)·미지정·멀티캐스트·예약 대역 IP, 메타데이터 호스트 이름, `localhost`, 점 없는 이름(compose 서비스 이름 postgres·redis·api 등)은 막고 `CONNECTION_HOST_ALLOWLIST`에 적은 호스트만 예외로 엽니다. `2130706433`·`0x7f.1` 같은 다른 IPv4 표기와 IPv4 매핑 IPv6도 같은 판정을 탑니다. 사설 대역은 사내 품질검수 모델 서버가 쓰므로 받습니다. 호스트 이름을 DNS로 풀지 않으므로 공용 이름이 내부 주소로 풀리는 경우와 검사 전에 저장된 연결은 막지 못합니다.
- Bedrock은 키가 없으면 boto3가 기본 자격증명 체인으로 넘어가 서버 인스턴스 메타데이터의 역할 자격증명을 씁니다. 그래서 점검과 품질검수 호출 모두 액세스 키와 비밀 키가 있을 때만 Bedrock을 부릅니다. 다른 서비스는 키 없이 먼저 등록하고 나중에 채울 수 있지만, Bedrock 연결은 생성·수정 뒤 두 키가 모두 남지 않으면 422 `bedrock_keys_required`로 저장하지 않습니다(`_check_bedrock_keys`). 이미 저장된 키 없는 Bedrock 연결은 점검이 접속하지 않고 실패로 기록하므로 주기 점검이 조직 담당자에게 알립니다.

- 기본 연결과 환경 변수 대체값은 없습니다. 분석 대상은 연결, 워크스페이스, MI 프로젝트 세 값을 함께 저장해야 합니다(422 `analysis_stack_incomplete`). 저장소 대상은 연결과 버킷을 함께 저장해야 합니다(422 `storage_stack_incomplete`). 후보는 그 프로젝트 조직의 연결 중 자격증명이 있고 마지막 점검이 정상인 연결입니다.
- 대상을 기록하지 않은 프로젝트는 영상 등록과 분석 요청을 받지 않습니다(409 `storage_stack_unset`·`analysis_stack_unset`). 분석 대상은 MI를 쓰는 분석 방식에서만 요구합니다. MI를 쓰지 않는 방식에서는 분석 대상 저장과 점검을 422로 막습니다(`require_supported`).
- 첫 영상 행이 생기면 두 대상 모두 바꿀 수 없습니다(409 `analysis_stack_locked`·`storage_stack_locked`). 한 프로젝트의 원본·산출·에셋이 두 버킷이나 두 MI 프로젝트에 나뉘면, 폴링·정리·재생·납품이 어느 쪽을 봐야 할지 알 수 없기 때문입니다. 저장하는 쪽은 프로젝트 행을 `FOR UPDATE`로 잡고, 영상을 만드는 쪽은 같은 행을 `FOR KEY SHARE`로 잡습니다. 그래서 잠금 판정과 첫 영상 생성이 엇갈리지 않습니다. 잠근 뒤 다시 읽은 설정에 대상이 없으면 영상을 만들지 않습니다.
- 저장하기 전에 실제로 점검합니다. 분석 대상 점검은 워크스페이스 접근, 연동 저장소와 프로젝트 버킷의 일치, MI 프로젝트 존재, 에셋 목록 권한을 봅니다. MI 워크스페이스는 연동 버킷 하나만 읽습니다. 그래서 버킷이 다르면 등록은 되고 분석만 실패합니다. 같은 점검을 등록·분석 요청 직전에 5분 캐시로 다시 실행하고, 주기 점검(`check_connections`, 15분)도 다시 실행합니다.
- 연결 관리는 super_admin만 합니다. 프로젝트 관리자는 캡션 품질검수 모델 연결을 고르기 위해 연결 선택지(`/projects/{project_id}/connection-choices`)만 받습니다. 선택지에는 이름, 서비스, 시크릿 유무, 마지막 점검 결과, 사용 가능한 품질검수 방식만 있습니다.
- 프로젝트가 고른 연결은 사용처로 세어 지우지 못하게 합니다(`users_by_connection`).
- MI 연결 점검은 분석 요청과 같은 클라이언트(`maiu.naver_client`)로 워크스페이스를 읽습니다. 점검이 서명을 따로 구현하면 점검은 통과하는데 분석만 실패하는 연결이 생깁니다.
- 서브모듈을 미리 import하지 않습니다. `media.storage`는 `storage_adapters.ncp`만 씁니다. 패키지를 열 때 `connections`까지 불러오면 `caption_qc`와 `ops.diagnostics`가 함께 로드됩니다.

## 의존성

- 부르는 것: `models`(Connection·Project·Video·DomainEvent·User), `services.audit`·`crypto`·`platform_credentials`, `reborn_contracts.connection`·`storage_backend`, 허용 목록의 `ops.diagnostics`·`caption_qc.adapters`·`caption_qc.settings`·`media.storage`·`maiu.client`·`maiu.naver_client`·`maiu.provisioning`·`analysis.presets`
- 부르는 쪽: `api/v1/connections.py`·`admin.py`·`projects.py`, 등록·분석 요청 경로(`api/v1/storage.py`·`videos/`·`maiu.py`의 `project_stack.gate`), `media.storage`(어댑터)·`media.video.commands`(`stack_lock`), `workers/worker-io`의 `check_connections`

## 남은 일

- 대상 점검 캐시는 프로세스마다 따로 있습니다. 점검 버튼은 그 요청을 받은 api 프로세스의 캐시만 갱신합니다.
- 다른 프로세스는 연결 변경을 최대 60초 뒤에 읽습니다(`platform_credentials.REFRESH_SECONDS`). 연결을 바꾼 프로세스는 바로 다시 읽습니다.
- 프로젝트 버킷이 아닌 곳에 원본이 있는 영상은 그 영상에 고정 기록한 연결로 읽습니다(`platform_credentials.storage_for_bucket`). 그 연결에 권한이 없는 버킷이면 읽기가 실패합니다.
- 백업 컨테이너는 DB 없이 실행되므로 연결을 읽지 못합니다. 백업 키는 환경 변수(`BACKUP_*`)에 남아 있습니다.
- MI 연결 설정의 워크스페이스는 연결 점검에만 씁니다. 프로젝트는 분석 대상에서 워크스페이스를 따로 고릅니다.
