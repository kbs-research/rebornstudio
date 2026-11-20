# services/media

미디어 자산 도메인입니다. 영상·클립 메타, 오브젝트 저장소 접근 관문, 준비 단계, 장면 분할, 썸네일, 원본 수명을 맡습니다. 준비 단계는 규격 검사, 변환, 비식별화, 재생본 확정 순서로 진행됩니다.
MI 분석 요청과 결과 반영은 `services/maiu`가 맡고, 작업 판정은 `services/work`가 맡습니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `video/` | 영상 서비스 패키지. 등록·조회·수정·soft delete, 반입 상태 판정, 커서 목록, 상태별 집계, 업로드, 준비 타임라인([README](video/README.md)) |
| `storage.py` | 오브젝트 저장소 접근 관문. presigned GET·멀티파트, 객체 존재 확인(`object_exists`), 폴더 나열(`browse_bucket`, 폴더 단위 등록 작업과 원본 정리가 씁니다). 쓰기 경로는 저장소 값(`Store`, `for_project`·`for_video`)을 받고, 영상 행을 만들 때 저장소를 `extra.storage`에 고정해 기록합니다(`pin_for`·`bucket_of`). 클라이언트 설정은 일반(연결 5초·읽기 60초·재시도 2회)과 대용량(읽기 600초·재시도 4회) 두 가지이고, 서버 복사·멀티파트 완료·패키지 업로드·본문 스트림 읽기만 대용량을 씁니다(`_config`) |
| `app_paths.py` | 앱이 직접 쓰는 최상위 경로(`APP_PREFIXES`)와 그 아래 키·prefix를 원본으로 등록하지 않는 판정. 등록 API와 폴더 단위 등록 작업이 같은 함수를 씁니다 |
| `storage_import.py` | 폴더 단위 등록. API가 작업 사양을 묶음 행에 기록하고(`queue_prefix_import`), beat가 버킷을 페이지 단위로 순회하며 등록합니다(`run_pending_imports`). 이미 등록한 원본은 건너뛰고 건너뛴 수를 묶음 사양의 `skipped_existing`에 남깁니다 |
| `ingest_batch.py` | 반입 묶음 행 생성(`ensure_batch`, 다른 프로젝트의 묶음 id는 404 `batch_not_found`), 묶음 목록 집계, 상세(묶음 프로젝트의 영상만 셉니다), 이름·메모 수정 |
| `meta_import.py` | 등록 묶음 영상 메타 일괄 입력. 양식 만들기(`template`), 행을 영상과 맞추고 검사하는 미리 보기(`preview`), 한 트랜잭션 반영(`apply`). 영상 ID, 파일(원래 파일 이름·저장소 경로·경로의 파일 이름), 제목 순서로 찾고, 여러 영상과 맞으면 고르지 않습니다. 값 검사는 `VideoUpdateRequest`, 저장은 단건 편집과 같은 `video.apply_meta`를 씁니다 |
| `meta_sheet.py` | 메타 일괄 입력의 표 파일(.xlsx·CSV) 쓰기와 읽기. DB를 읽지 않습니다. 양식은 셀을 문자열 형식으로 저장해 수식 기호로 시작하는 제목이 수식으로 실행되지 않게 합니다. 읽을 때는 파일 크기·압축 해제 크기·행 수 상한, UTF-8·CP949 CSV, 방영일 형식(KST 자정)을 처리합니다 |
| `clip.py` | 클립 조회·건수, 분석 결과로 클립 생성(`create_scene_clip`, 정상 상태 `auto_cleared`로 만들고 끝이 영상 길이를 넘으면 영상 끝까지로 줄입니다), 사람이 넣는 구간(만들 때 라벨링 작업을 열고 영상 티켓을 지웁니다), `merge_extra` |
| `progress.py` | 클립 진척 요약(승인·진행 중·작업 전)과 역할별 응답. 라벨러에게 줄 클립과 필드를 정합니다(`clip_audience`·`visible_rights`) |
| `pipeline.py` | 준비 단계의 순서와 각 단계를 여는 조건. 다음 단계 열기(`open_next`) |
| `resolver.py` | 용도별로 쓸 파일(원본·분석본·재생본)을 고릅니다. 다른 코드는 `source_uri`를 직접 읽지 않습니다 |
| `probe.py` | MI 요청 전 ffprobe 규격 검사(용량·길이·코덱·컨테이너). 규격 위반은 `rejected`, 검사 실패는 `error`로 기록합니다. 망 순단·5xx·제한 시간 초과는 백오프 뒤 다시 검사하고, 연속 여섯 번을 넘으면 `error`로 확정합니다. 배치는 `single_runner`로 하나만 돕니다. 화면에 보여 줄 비트레이트는 `bit_rate`가 정합니다. 측정값이 없는 기록은 용량과 재생 시간으로 평균값을 계산하고, 영상 상세가 이 함수를 씁니다 |
| `transcode.py` | 분석본 변환 잡의 DB 쪽 처리. 변환 필요 판정(`needs_transcode`·`plan_outputs`), 잡 페이로드, 토큰으로 낡은 완료 회신을 버리는 판정, 산출 검사(`judge_outputs`), 재시도 |
| `deident.py` | 비식별화 잡의 DB 쪽 처리. 전역 투입 상한(`DEIDENT_MAX_INFLIGHT`), 잡 페이로드(입력·산출·얼굴 좌표 presign), 회수, 일시 오류 백오프, 비식별화를 끈 프로젝트의 건너뛰기(`skip_disabled`·`skip_failed`) |
| `publish.py` | 재생본 변환과 확정. 마스터를 최종 키로 복사하고 스테이징을 정리합니다. 일시 오류의 간격과 연속 상한은 변환과 같습니다 |
| `scene_split.py` | 장면 분할 잡의 DB 쪽 처리. MI를 쓰지 않는 분석 방식에서 클립을 만들고 영상 티켓을 지웁니다(`work.service.retire_video_ticket`). 리스는 변환과 같이 큐 대기 시간을 포함하고, 처리 중에는 워커 하트비트(`extend_lease`)가 연장합니다 |
| `stage_runs.py` | 준비 단계 시도 이력 기록(`start`·`finish`)과 조회. 비식별화 리스 회수(`open_started_at`), 건너뛰기 감사 기록의 시도 수(`attempt_counts`), 타임라인의 단계별 최신 상태(`latest_runs`)가 읽습니다 |
| `retention.py` | 원본 정리(분석 결과가 나온 영상, 지우는 원본은 체크섬 뒤), 분석본 정리, 보고 없이 끝난 시도의 스테이징 파일 정리(`sweep_orphan_staging`) |
| `checksum.py` | 원본 sha256 체크섬을 주기적으로 채웁니다. 실패한 영상은 시도 시각을 남기고 후보 순서의 뒤로 보내므로, 읽지 못하는 영상이 있어도 다른 원본의 체크섬은 계속 채워집니다 |
| `clip_thumbnail.py`·`video_thumbnail.py` | 클립 장면 썸네일과 영상 대표 이미지 생성. 주기당 상한과 시간 예산, 프로젝트별 번갈아 고르기(`fair_select`) |
| `thumbnail_source.py` | 썸네일을 뽑을 파일 선택. 비식별화를 쓰는 영상은 비식별화본에서만 뽑습니다 |

## 설계 이유

- 저장소 자격증명과 버킷은 프로젝트가 고른 저장소 연결에서만 읽습니다(`services/platform_credentials.py`). 기본 저장소와 환경 변수 대체값은 없습니다. 대체값이 있으면 아무도 고르지 않은 버킷에 고객 원본이 쌓일 수 있습니다. URI의 버킷만 아는 경로(재생 주소·변환 입력)는 그 버킷을 고른 연결을 찾아 읽고, 어느 연결에도 없는 버킷은 읽지 않습니다.
- 저장소 클라이언트와 서명 방식은 하나만 씁니다. SigV4는 host를 서명에 넣으므로 호출 위치별로 클라이언트를 나누면 브라우저가 보낸 서명을 저장소가 403으로 거절합니다. 객체 key는 항상 서버가 정하고, 업로드 완료는 HEAD로 크기를 확인한 뒤 기록합니다.
- 변환·비식별화·재생본·장면 분할 워커는 DB에 접속하지 않습니다. 이 패키지가 잡 페이로드(presigned URL과 회신 확인용 토큰)를 만들고, 회신을 받아 판정합니다. 토큰이 낡은 회신은 버립니다.
- 워커가 올리는 키에는 그 시도의 토큰이 들어갑니다(`transcode.staging_key`, `work/{video_id}/{token}/…`). 완료 판정은 현재 토큰의 파일만 마스터 키·최종 재생본 키·최종 좌표 키로 옮깁니다. 잡 URL은 시도가 끝나도 유효 기간 동안 쓸 수 있어서, 키가 같으면 회수된 시도의 늦은 업로드가 마스킹된 마스터를 마스킹 전 파일로 덮어쓰기 때문입니다. 늦은 업로드는 자기 키에 남고, 그 시도의 성공 보고가 펜싱에 막히면 완료 판정이 그 키를 지웁니다.
- 앱 경로에는 버킷 만료 규칙을 두지 않으므로(`docs/install.md`) 보고 없이 끝난 워커의 파일은 하루 한 번 `retention.sweep_orphan_staging`이 지웁니다. 현재 시도와 비식별화 회수 표식의 토큰은 남기고, 잡 URL 유효 기간보다 오래된 파일만 지웁니다. 유효 기간 안의 파일은 아직 보고하지 않은 시도의 업로드일 수 있습니다.
- 고아 스테이징 정리는 이 DB에 행이 있는 영상(soft delete 포함)의 키만 지웁니다. 버킷을 다른 설치본과 같이 쓰면 `work/` 아래에 그 설치본의 진행 중인 시도가 있는데, 이 DB에는 그 시도의 토큰이 없어 토큰만으로 판정하면 다른 설치본의 파일을 지웁니다. 다른 정리 경로(원본 정리·분석본 정리·완료 판정의 스테이징 정리)도 접두사로 목록을 읽지 않고 이 DB의 영상 행에서 만든 키만 지웁니다.
- 단계 순서는 `pipeline.py` 한 곳에 둡니다. 순서를 여러 곳에 적으면 단계를 더할 때 한 곳이 빠지고, 그 경로의 영상은 오류 없이 `pending`에 머뭅니다.
- `videos.extra`·`clips.extra`에는 여러 워커가 같은 행에 씁니다. 그래서 병합은 `merge_extra`(DB의 `metadata || patch`)로만 합니다.
- 일시 오류(429·5xx·타임아웃)는 시도 횟수에 세지 않고 다시 시도합니다(`services/transient.py`). 영구 오류만 세고, 상한에 이르면 실패로 확정합니다. 일시 오류가 이어지면 변환·비식별화·재생본·장면 분할 모두 간격을 60초부터 두 배씩 늘리고(최대 30분), 10회 이어지면 실패로 확정합니다. 만료된 URL처럼 다시 시도해도 낫지 않는 일시 오류가 끝없이 반복되지 않게 합니다.
- 원본은 분석 결과가 나온 뒤에만 정리합니다. 원본이 이미 MI 규격이면 분석본이 따로 없고 원본이 분석 입력이 되기 때문입니다.
- 원본을 지우는 프로젝트(`retain_original=false`)는 체크섬을 기록한 뒤에 원본을 정리합니다. 지운 원본은 다시 잴 수 없어서 정리가 먼저 돌면 납품 패키지의 `source_checksum`이 빕니다. 체크섬 재시도가 상한(`checksum._MAX_RETRIES`)에 닿은 영상은 원본이 스테이징에 영구히 남지 않도록 체크섬 없이 정리합니다. 상한까지 세는 실패는 원본이 없는 응답(NoSuchKey·404)과 해석할 수 없는 원본 위치뿐입니다. 자격증명·권한 오류는 설정을 고치면 다시 읽을 수 있어 세지 않고, 그동안 정리도 기다립니다. 원본을 보관하는 프로젝트는 보관 위치에서도 잴 수 있어 기다리지 않습니다.
- 비식별화를 쓰는 영상의 썸네일은 비식별화본에서만 뽑습니다. 라벨러도 목록에서 썸네일을 보므로 원본 프레임을 쓰면 가리지 않은 얼굴이 보입니다.

## 의존성

- 부르는 것: `models`(Video·Clip·IngestBatch·MediaStageRun·AnalysisJob·WorkItem), `services.audit`·`authz`·`platform_credentials`·`transient`·`error_text`·`joblock`·`deident_gap`, `reborn_transcode`(함수 안에서 import), boto3, openpyxl(메타 양식), 허용 목록의 `analysis.*`·`work.service`·`stacks.storage_adapters.ncp`·`stacks.stack_lock`
- 부르는 쪽: `api/v1/videos/*`·`storage.py`, `maiu`(클립 생성·미디어 메타·단계 이력), `work.deident_reports`, `exports`, `analytics`, `workers/worker-io`의 beat와 회신 태스크

## 알려진 제약

- `media`와 `work`가 서로 import합니다. 그래서 `__init__.py`는 서브모듈을 미리 import하지 않습니다.
- 영상을 지우면 끝나지 않은 멀티파트 업로드를 중단합니다(`video/commands.py`). 영상을 지우지 않고 방치한 업로드는 정리하지 않습니다.
- `video_thumbnail`과 `probe`의 후보 선택에는 `FOR UPDATE SKIP LOCKED`가 없습니다. 둘 다 `single_runner`로 배치를 하나만 돌려 같은 영상을 함께 검사하지 않습니다.
