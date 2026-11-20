# services

업무 로직 계층입니다. 라우터(`api/v1/`)와 워커(`workers/worker-io`)가 이 계층의 함수를 부릅니다.
도메인마다 패키지 하나를 둡니다. 모든 도메인이 함께 쓰는 작은 규칙은 이 폴더 바로 아래에 평면 모듈로 둡니다.
도메인 사이의 import는 `apps/api-gateway/pyproject.toml`의 import-linter 계약이 CI에서 검사합니다.

## 주요 파일(도메인 패키지)

| 패키지 | 역할 |
|---|---|
| [`auth/`](auth/README.md) | 계정 생성·로그인·세션·개인정보 동의·최초 설정, 로그인 잠금과 rate limit |
| [`org/`](org/README.md) | 기본 조직·프로젝트·멤버 |
| [`work/`](work/README.md) | 라벨링 가져오기·제출, 검수, 비식별화 신고 처리, 반려 누적 목록, 우선순위 |
| [`media/`](media/README.md) | 영상·클립 자산과 준비 단계(업로드·규격 검사·변환·비식별화·장면 분할·재생본·썸네일), 저장소 접근 관문 |
| [`analysis/`](analysis/README.md) | 분석 스택 카탈로그, 분석 상태 어휘, 어댑터 능력 선언. 외부 분석 서비스의 응답을 도메인 값으로 바꾸고, 판정은 하지 않습니다 |
| [`maiu/`](maiu/README.md) | NAVER Media Intelligence(MI) 연동. 요청·폴링·클립 생성·비용·분석 자원 진단 |
| [`workforce/`](workforce/README.md) | 본인 판정 목록, 본인 로그인 이력 |
| [`analytics/`](analytics/README.md) | 대시보드·통계·운영 지표. 읽기만 합니다 |
| [`exports/`](exports/README.md) | 납품 패키지 생성과 완료 목록. 납품 판정 `check_row`가 여기에 있습니다 |
| [`caption_qc/`](caption_qc/README.md) | 캡션 품질검수. 규칙 검사와 모델 검사, 보류 처리, 검사 대기 판정 |
| [`ops/`](ops/README.md) | 연동 진단, 감사 로그 조회와 보존 기간 정리 |
| [`stacks/`](stacks/README.md) | 자격증명 연결 관리, 프로젝트별 분석 대상·저장소 대상의 선택·점검·잠금, 저장소 어댑터 |

## 주요 파일(평면 모듈)

여러 도메인이 같은 규칙을 써야 하는 모듈은 평면에 둡니다. 평면 모듈은 모두 커널 모듈입니다. 커널 모듈은 도메인 패키지를 import하지 않고, 커널 계약이 간접 import까지 검사합니다.

| 파일 | 역할 |
|---|---|
| `audit.py` | 감사 이벤트 기록(`emit`)과 조회 도우미. 상태 변경과 같은 트랜잭션에 이벤트를 넣습니다 |
| `authz.py` | 프로젝트 역할 기반 인가(`require_role`·`require_project_admin`·`require_work_result_viewer`·`require_member`·`require_participation`) |
| `operating_time.py` | 날짜·달 경계의 기준 시간대(Asia/Seoul). `today`·`month_key_expr` |
| `error_text.py` | 실패 원문을 운영자가 읽을 사유로 바꿉니다(`operator_reason`·`operator_text`). 원문은 `raw_detail`로 따로 남깁니다. 분석 일시 오류의 분류(`analysis_transient_kind`)와 보류 표시도 맡습니다 |
| `transient.py` | 외부 호출 오류의 일시·영구 판정(HTTP 상태, ffmpeg, boto3) |
| `joblock.py` | 주기 작업의 단일 실행 락(`single_runner`)과 트랜잭션 범위의 임계 구역 락(`lock_for_transaction`). 단일 실행 락은 전용 커넥션에서 advisory lock을 잡고 풉니다. 임계 구역 락은 잡 디스패처가 전역 상한을 세기 전에 잡아 동시에 도는 디스패처가 상한을 넘겨 발급하지 않게 합니다 |
| `crypto.py` | 연결 시크릿의 Fernet 암호화·복호화. 새 키(`CREDENTIALS_KEY`)로 암호화하고 새 키와 이전 키(`CREDENTIALS_KEY_PREVIOUS`)로 복호합니다(MultiFernet). 이전 키 암호문을 새 키로 바꾸는 `rekey_connections`도 둡니다 |
| `platform_credentials.py` | 등록한 저장소(`ncp`)·분석(`maiu`) 연결의 자격증명을 프로세스 메모리에 두고 읽습니다. 프로젝트 설정 `stack` 블록의 모양(`AnalysisTarget`·`StorageTarget`)도 정의합니다 |
| `reject_reasons.py` | 반려 사유 목록. 고정 기본 목록(`BUILTIN`), 저장된 목록 읽기(`of_project`), 판정의 사유 코드 검증, 코드를 현재 이름으로 바꾸기. 목록을 바꾸는 API는 없습니다 |
| `caption.py` | 캡션 payload의 문장 목록·최종 문자열·토큰 수와 제출 최소 길이 판정(`too_short`) |
| `csv_cell.py` | CSV로 내보내는 문자열 칸이 수식 시작 글자(`=`·`+`·`-`·`@`·탭·CR)로 시작하면 앞에 작은따옴표를 붙입니다(`safe_cell`). 납품 패키지의 원본 영상 목록과 메타 일괄 입력 양식이 씁니다 |
| `text_search.py` | 검색어를 LIKE 패턴으로 바꿉니다. `%`·`_`·`\`를 이스케이프합니다 |
| `video_meta.py` | 클립이 물려받는 영상 단위 메타(방송사·프로그램명·장르·방영일) |
| `deident_gap.py` | 비식별화 결과가 없는 이유(`off`·`before_on`·`waiting`·`running`·`failed`) 판정. 열 기본값 `skipped`를 파이프라인 위치(`reached_deident`)로 나눕니다 |

## 설계 이유

### 쓰기 소유권

DB는 하나지만 테이블·컬럼마다 쓰기를 맡는 도메인을 정합니다.

- 다른 도메인의 테이블·컬럼은 소유 도메인의 공개 함수를 불러 바꿉니다.
- 다른 도메인의 테이블을 읽는 것은 허용합니다. 판정에는 소유 도메인의 함수나 SQL을 씁니다. 판정을 복제하면 두 화면이 같은 행을 다르게 분류합니다.
- reporting(`analytics`·`exports`·감사 조회)은 모든 도메인을 읽고, 업무 테이블에는 쓰지 않습니다.
- 워커 태스크는 서비스 함수를 부르는 얇은 함수입니다. 판정을 태스크에 두지 않습니다.

| 도메인 | 쓰기 소유 |
|---|---|
| auth·org | `users`, `password_reset_tokens`, `organizations`, `projects`, `project_members`. 단 `projects.settings`의 `stack` 블록은 stacks가 씁니다 |
| stacks | `connections`, `projects.settings.stack` |
| media | `videos`, `clips`, `ingest_batches`, `media_stage_runs`, `analysis_job`의 장면 분할 행. 단 `clips`의 클립 상태 컬럼(`rights_status`·`rights_decided_*`)은 클립을 만들 때를 빼면 work가 씁니다 |
| maiu | `analysis_job`의 MI 행. 클립 생성·미디어 메타·`metadata` 병합은 media의 `create_scene_clip`·`absorb_media_meta`·`merge_extra`를 부릅니다 |
| work | `work_items`, `labels`, `reviews`, 위의 클립 상태 컬럼. 멤버 제거·나가기(org)와 계정 정지(auth)가 보유를 풀 때도 `work.service.release_holds`를 부릅니다 |
| caption_qc | `caption_qc_runs`, `work_items`의 품질검수 상태 |
| exports | `exports` |
| ops | `domain_events` 보존 기간 삭제 |

`domain_events`에 행을 추가하는 곳은 평면 모듈(`audit.emit`)뿐입니다. 새 이벤트를 더할 때도 모델을 직접 만들지 않습니다.

작업자의 품질 등급을 매기는 기능은 두지 않습니다. 판정은 사람이 하고, 도구는 수를 보여 줍니다.

### import 계약

- 도메인 독립 계약: `services/` 아래 패키지를 모두 서로 독립으로 둡니다. 허용하는 교차 import는 `ignore_imports`에 줄마다 이유와 함께 적습니다. 없는 import를 목록에 남기면 import-linter가 `No matches for ignored import`로 실패합니다. 그래서 교차 import를 지울 때 목록에서도 지웁니다.
- 커널 계약: 위 평면 모듈이 도메인 패키지를 import하지 못하게 합니다.
- 레이어 계약: `api`가 `services`를, `services`가 `models`를 부르는 방향만 허용합니다.
- 저장소 루트의 `.importlinter`: 워크스페이스 패키지 사이의 방향을 검사합니다. `reborn_transcode`·`reborn_contracts`·`worker_jobs`가 무엇을 import하지 못하는지 정합니다.
- 패키지나 평면 모듈을 새로 만들면 `tests/contract/test_import_contracts.py`가 실패합니다. 새 패키지는 독립 계약과 커널 계약의 `forbidden_modules`에 넣습니다. 새 평면 모듈은 커널 계약의 `source_modules`에 넣습니다.

### 이벤트 카탈로그(통합 이벤트)

`domain_events.kind`는 감사 기록(`audit`)과 다른 도메인이 구독할 사실(`integration`)을 구분합니다. integration 이벤트의 이름과 payload는 계약입니다.

| event_type | 발행 위치 | payload | 의미 |
|---|---|---|---|
| `video.analysis_done` | `maiu/reconciler/ingest.py` | `maiu_asset_id`, `clip_count` | 분석이 끝나 클립이 생겼습니다. 구독자는 아직 없습니다 |

### 서비스 함수 규칙

- DB 세션(`AsyncSession`)은 인자로 받고, 커밋은 서비스가 합니다. 감사 이벤트는 커밋 전에 같은 세션에 넣습니다.
- 요청 오류는 서비스가 `HTTPException`으로 던집니다. `detail`은 snake_case 코드입니다.
- 외부 호출(MI, NCP Object Storage, Bedrock)은 클라이언트·어댑터 모듈에 둡니다. 자격증명은 프로젝트가 고른 연결에서 `platform_credentials`로 읽습니다. 서버 환경 변수로 대신 넣는 값은 없습니다.

## 의존성

- 부르는 것: `api_gateway.models`, `api_gateway.config`, `reborn_contracts`, `reborn_transcode`(변환 사양), boto3, httpx, redis
- 부르는 쪽: `api_gateway/api/v1/*`, `workers/worker-io`의 태스크, `api_gateway/main.py`(lifespan의 주기 루프)
- 테스트: `apps/api-gateway/tests/<패키지 이름>/`. 평면 모듈은 `tests/kernel/`, 도메인을 가로지르는 계약은 `tests/contract/`에 둡니다([`tests/README.md`](../../tests/README.md)).

## 남은 일

- `media`와 `work`가 서로 import합니다. `media.video`가 `work.service`를, `work.deident_reports`가 `media.clip`을 import합니다. 그래서 두 패키지의 `__init__.py`는 서브모듈을 미리 import하지 않습니다. 미리 import하면 패키지 수준의 순환이 생깁니다.
