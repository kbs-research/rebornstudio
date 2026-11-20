# reborn_contracts

## 목적

서버와 화면이 함께 쓰는 Pydantic v2 스키마 모듈입니다. 한 도메인을 한 파일에 두고, `__init__.py`가 화면 계약에 쓰는 모델을 다시 내보냅니다.
모델 이름 목록은 각 파일과 `__init__.py`의 `__all__`을 기준으로 봅니다. 이 README에는 파일별 범위만 적습니다.

## 주요 파일

| 파일 | 다루는 것 |
|---|---|
| `__init__.py` | 아래 모듈의 공개 모델을 다시 내보냅니다(`__all__`). `analysis`·`clip_metadata`·`queues`는 다시 내보내지 않습니다 |
| `auth.py` | 로그인·세션·비밀번호 재설정·사용자 정보·역할과 상태 변경 |
| `user.py` | 프로필 수정, 개인정보 동의, 온보딩 상태 |
| `admin.py` | 계정 생성, 시스템 상태, 최초 설정, 연동 진단, 초기 구성 |
| `project.py` | 프로젝트 설정(장면 분할·변환·품질검수 옵션), 멤버 가입·승인·기간·한도·역할, 프로젝트가 고른 분석·저장소 연결 |
| `connection.py` | 자격증명 연결. 시크릿은 요청으로만 받고, 응답에는 설정 여부(`has_secret`)와 시각만 싣습니다 |
| `video.py` | 영상 등록·업로드(멀티파트 포함)·반입 묶음·준비 단계 타임라인 |
| `video_meta_import.py` | 등록 묶음 영상 메타 일괄 입력의 미리 보기·반영 요청과 응답. 반영 항목의 `fields`는 `VideoUpdateRequest`를 그대로 쓰고 `null`(값 지우기)은 받지 않습니다. 행 수 상한 `BULK_META_MAX_ROWS` |
| `clip.py` | 클립 조회·클립 상태(`ClipRightsStatus`·`RIGHTS_CLEARED`)·비식별화 신고 목록과 처리 |
| `clip_metadata.py` | 납품 클립 메타데이터 모델. 납품 패키지에 넣는 JSON Schema를 이 모델에서 만듭니다 |
| `analysis.py` | 분석 결과 도메인 모델과 어댑터 능력 선언. 외부 분석 서비스의 응답을 이 모양으로 옮깁니다 |
| `storage.py` | 키 목록·폴더 경로 일괄 등록의 요청과 결과 DTO |
| `storage_backend.py` | 저장소 어댑터가 지켜야 할 모양(객체 참조·업로드 세션·능력) |
| `work.py` | 작업 가져오기·제출·임시 저장·화자 이름·신고, 호출한 사용자의 작업 기록, 반려 누적 목록 |
| `review.py` | 검수 판정 요청과 반려 사유 코드, 라벨 체인과 검수 이력 행 |
| `export.py` | 내보내기(납품 패키지), 완료 목록과 상세, 캡션 품질검수 결과·보류 처리 |
| `dashboard.py` | 개요·성과·사업 KPI·처리 능력 등 통계 화면 DTO |
| `stats.py` | 프로젝트 진행 요약 |
| `maiu.py` | MI 분석 요청·잡 상태 |
| `event.py` | 감사 이벤트(`domain_events`) 조회 |
| `workforce.py` | 본인 검수 판정 목록 |
| `queues.py` | 워커 태스크 이름·큐·라우팅 표(`TASK_ROUTES`·`JOB_TASKS`·`CALLBACK_TASKS`). 스키마가 아니며 `from reborn_contracts.queues import …`로 씁니다 |

## 설계 이유

- 값의 범위는 `Literal`로 선언해 DB의 CHECK 제약과 맞춥니다. 어댑터 능력처럼 코드가 분기하는 값은 `StrEnum`을 씁니다(`analysis.py`·`storage_backend.py`).
- 입력 제약(`min_length`·`max_length`·`ge`·`le`)은 이 계층에서 선언합니다. 그래서 서버 핸들러가 같은 검사를 다시 쓰지 않습니다.
- `analysis.py`와 `storage_backend.py`는 화면 DTO가 아니라 어댑터 경계입니다. 화면 DTO와 같은 파일에 두면 화면 스키마를 고칠 때 어댑터 계약도 같이 바뀝니다.
- `queues.py`의 태스크 이름은 모듈 위치와 무관하게 `worker_io.tasks.<함수명>`입니다. 큐에 쌓인 메시지, beat 스케줄, 하트비트 Redis 키가 이 이름을 가리킵니다.

## 의존성

- 이 패키지가 부르는 것: `pydantic`, 표준 라이브러리
- 부르는 쪽: `apps/api-gateway`(라우터·서비스·어댑터), `workers/worker-io`·`workers/worker-jobs`(`queues`)

## 남은 일

- 없음.
