# api/v1/videos

영상·클립·반입 묶음 REST 라우터입니다. 경로가 많아서 하위 리소스별 모듈로 나누었습니다.
`main.py`는 이 패키지의 `router` 하나만 include합니다(`app.include_router(videos.router)`). 경로는 모두 전체 경로(`/projects/{project_id}/videos`·`/videos/{video_id}/…`)로 선언합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `__init__.py` | 하위 라우터의 경로를 `_ORDER` 순서대로 `router` 하나에 모읍니다 |
| `catalog.py` | 영상 등록, 목록(커서 페이지), 반입 상태별 건수(`buckets`), 조건 선택(`select`), 상세, soft delete, 재생 주소(`source-url`), 메타 수정 |
| `upload.py` | 업로드 상한(`upload-limits`), 멀티파트 시작·파트 서명·완료 확정 |
| `ingest_batches.py` | 반입 묶음 목록, 등록 Job 상세, 이름·메모 수정, 영상 메타 일괄 입력(양식 내려받기 `meta-template`·미리 보기 `meta-import/preview`·반영 `meta-import/apply`). 미리 보기는 파일 업로드(multipart)로 받습니다 |
| `prep.py` | 준비 타임라인(단계별 현재 상태·실패 사유·재시도 가능 여부), 비식별화·장면 분할·변환·썸네일 재시도, 규격 검사(probe) 재설정, 비식별화를 끈 프로젝트의 실패한 비식별화 건너뛰기(`deident/skip`) |
| `artifacts.py` | 영상 한 편의 분석본 정리. 원본이 있을 때만 지웁니다 |
| `clips.py` | 클립 직접 추가, 프로젝트 클립 목록·건수·단건, 영상별 클립 |

## 설계 이유

- 경로 등록 순서가 OpenAPI 문서(`docs/api/openapi.json`)의 경로 순서와 매칭 순서를 정합니다. 같은 경로를 여러 라우트가 받으면 먼저 등록한 라우트가 매칭됩니다. 하위 라우터를 차례로 include하면 경로가 모듈별로 묶여 순서가 바뀌고, 계약 문서도 달라집니다. 그래서 `_ORDER`에 순서를 적고 그 순서대로 조립합니다.
- 하위 모듈에 경로를 더하고 `_ORDER`에 빠뜨리면 그 경로는 등록되지 않아 404가 됩니다. 그래서 하위 모듈의 엔드포인트와 `_ORDER` 항목이 하나씩 짝지어지지 않으면 `_assemble`이 import 시점에 `RuntimeError`를 냅니다.
- 새 경로는 `_ORDER`의 끝에 더해도 됩니다. 기존 경로의 순서를 바꾸면 OpenAPI 문서가 바뀝니다.

## 의존성

- 부르는 것: `api_gateway.dependencies`(`get_current_user`·`get_db`), `services.media`(`video`·`clip`·`ingest_batch`·`meta_import`·`meta_sheet`·`deident`·`scene_split`·`transcode`·`video_thumbnail`·`retention`), `services.stacks.project_stack`, `services.analysis.presets`, `services.audit`·`services.authz`, `reborn_contracts`
- 부르는 쪽: `main.py`. 테스트는 `tests/media/`에 있습니다.

## 남은 일

- `artifacts.py`의 `purge_video_analysis`는 라우터에서 `db.get(Video, …)`로 영상을 읽고 감사 기록과 커밋까지 합니다. 서비스(`services/media/retention.py`)로 옮길 후보입니다.
