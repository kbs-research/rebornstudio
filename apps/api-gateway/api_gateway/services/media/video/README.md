# services/media/video

영상 자산의 메타데이터와 상태를 다룹니다. 등록·수정·삭제, 원본 업로드, 목록·집계 조회,
반입 상태와 준비 단계의 판정, 준비 타임라인이 여기에 있습니다.
판정·조회·쓰기·변환을 하위 모듈로 나눈 패키지입니다. 호출하는 쪽은 `media.video` 파사드를 씁니다.

## 주요 파일

| 파일 | 역할 | 대표 이름 |
|---|---|---|
| `__init__.py` | 파사드. 하위 모듈의 이름을 모두 다시 내보냅니다 | `__all__` |
| `dto.py` | ORM 행을 `VideoDTO`로 바꾸는 변환과 라벨러용 가림. probe 결과에 비트레이트가 없으면 평균값을 채운 사본을 `metadata`로 내보냅니다 | `LibraryStatus`·`_to_dto`·`_redact_for_labeler` |
| `classify.py` | 판정 SQL과 CASE. 목록 필터·집계·행 표시가 같은 규칙을 쓰도록 판정은 이 파일에만 둡니다. MI 분석 요청과 자체 장면 분할의 준비 판정(`_analysis_hold_case`)도 여기 있습니다. 준비 판정은 규격 검증 통과, 길이 있음, 준비 단계 끝, 가림을 보고, 막으면 사유 코드를 냅니다. 장면 요약을 받지 못한 영상(MI 완료, 클립 있음, 단계 이력의 최신 요약 시도 실패)은 두 축 모두 막힘(`summary`)이고, 요약 재요청도 같은 조건으로 대상을 고릅니다 | `_analysis_hold_case`·`_prep_unfinished_expr`·`_ingest_bucket_case`·`_prep_stage_case`·`_prep_blocked_stage_case`·`_summary_blocked_expr` |
| `keys.py` | 원본 오브젝트 키와 URI 해석 | `_upload_key`·`archive_key`·`_parse_s3_uri` |
| `access.py` | 여러 모듈이 함께 쓰는 권한 확인 | `_require_member`·`_load_for_admin` |
| `commands.py` | 쓰기. 등록·수정·삭제와 JSONB 병합. 올려도 쓸 수 없는 파일(0바이트·상한 초과)은 행을 만들기 전에 거절합니다. 분석 입력이 확장자 없는 업로드 원본이면 같은 경로에 확장자를 붙인 키로 옮깁니다(`ensure_input_ext`). 분석본을 만들지 않으므로 납품 시각 보정은 바뀌지 않습니다. 원래 파일 이름은 업로드 등록이면 요청의 `original_filename`을, 버킷 등록이면 키의 마지막 부분을 `extra["original_filename"]`에 두고, 납품 패키지의 `sources.csv`가 읽습니다 | `create`·`ensure_input_ext`·`register_keys`·`update`·`merge_extra`·`absorb_media_meta`·`soft_delete`·`reset_probe`·`reject_unuploadable` |
| `queries.py` | 목록·단건 조회와 상태별 집계, 재생 URL 발급. 비식별화본이 필요한 영상은 라벨러에게 원본을 재생 매체로 주지 않습니다 | `list_for_project`·`get_one`·`select_ids`·`bucket_counts`·`ingest_bucket_totals`·`issue_source_url` |
| `upload.py` | 원본 멀티파트 업로드의 시작·파트 서명·확정. 확정이 크기 상한으로 거절하면 합쳐진 객체를 지웁니다 | `upload_limits`·`initiate_multipart`·`sign_upload_parts`·`complete_upload` |
| `timeline.py` | 준비 타임라인 조립. 분석 서비스(MI)를 쓰지 않는 방식(샷 검출)에는 분석 등록·분석·장면 요약 줄을 싣지 않습니다. 단계별 현재 상태, 실패·건너뜀 사유, 재시도·건너뛰기 가능 여부만 싣고(장면 요약 실패 줄은 요약 재요청이나 분석 재요청을 받을 수 있을 때만 `retryable`) 시각·소요·시도 수는 싣지 않습니다. 비식별화를 끈 뒤 건너뜀으로 넘어간 영상은 실패한 마지막 시도 대신 판정(`deident_gap`)으로 그립니다 | `prep_timeline` |

## 설계 이유

- 다른 모듈·테스트·워커는 `from api_gateway.services.media import video as video_service`
  또는 `from api_gateway.services.media.video import merge_extra`로 씁니다. `__init__.py`가 하위 모듈의
  최상위 이름을 빠짐없이 다시 내보내므로 호출하는 쪽은 하위 모듈 위치를 몰라도 됩니다.
- `merge_extra`는 JSONB 병합을 DB가 하게 하는 표준 함수입니다. 새 워커도 파사드 이름
  `media.video.merge_extra`로 부릅니다. 정의는 `commands.py`에 있습니다.
- 판정 SQL은 `classify.py` 한 곳에 둡니다. `ingest_batch`·`analytics.dashboard`도 파사드를 거쳐
  같은 CASE를 씁니다. 조건을 다른 모듈에 다시 쓰면 같은 화면의 수가 서로 달라집니다.

### 하위 모듈 사이의 의존 방향

아래쪽 모듈은 위쪽 모듈을 import하지 않습니다. 순환 import를 막는 규칙입니다.

```
__init__  → 전부
upload    → commands · dto · keys · access
commands  → dto · keys
queries   → dto · classify · access
timeline  → classify (함수 안에서)
dto · classify · keys · access   ← 형제를 import하지 않음
```

`classify`와 `timeline`은 `analysis.registry`를 함수 안에서 import합니다.
`queries`는 `media.clip`과의 순환을 피하려고 `media.clip`을 함수 안에서 import합니다.
`dto`는 비트레이트 표시 규칙(`media.probe.bit_rate`)을 함수 안에서 import합니다. `media.probe`가 이 패키지를 import하므로 모듈 머리에서 부르면 순환합니다.

### 테스트에서 이름을 바꿀 때

`monkeypatch.setattr(video_service, "merge_extra", 가짜)`는 파사드의 이름만 바꿉니다.
하위 모듈은 자기 전역 이름으로 서로를 부르므로 가짜 함수가 적용되지 않습니다.
예를 들어 `upload.complete_upload`가 부르는 `merge_extra`를 바꾸려면 `video.upload` 모듈의 이름을 바꿉니다.
테스트에서는 가짜 함수가 실제로 불렸는지도 확인합니다.

## 의존성

- 부르는 것: `models`, 커널 평면 모듈(`audit`·`authz`·`error_text`·`deident_gap`),
  같은 패키지의 `probe`·`storage`·`stage_runs`·`ingest_batch`·`progress`·`resolver`·`transcode`·`scene_split`·`clip`
- 교차 도메인(`pyproject.toml` 허용 목록): `work.service`(commands·queries·upload),
  `analysis.registry`(classify·timeline), `analysis.presets`(timeline), `stacks.stack_lock`(commands)
- 부르는 쪽: `api/v1/videos/`·`api/v1/storage.py`, `media`의 워커 잡들(`probe`·`transcode`·`publish`·`deident`·
  `scene_split`·`retention`·`checksum`·`clip_thumbnail`·`storage_import`·`ingest_batch`),
  `maiu.reconciler`·`maiu.service`, `analytics.dashboard`

## 알려진 제약

- 다른 모듈이 쓰고 있어서 파사드가 밑줄로 시작하는 이름(`_to_dto`·`_parse_s3_uri`·판정 CASE)도 내보냅니다.
- `media`와 `work`가 서로 import합니다. `commands` 등이 `work.service`를, `work.deident_reports`가 `media.clip`을 import합니다.
