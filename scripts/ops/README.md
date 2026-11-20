# scripts/ops

## 목적

운영 데이터, 외부 서비스(NCP Object Storage·Media Intelligence), 운영 서버에 필요할 때 한 번씩 실행하는 스크립트입니다.
게이트가 아니고, 정해진 일정으로 실행되지도 않습니다. 실행하기 전에 각 파일 docstring의 전제(beat 정지 여부 등)를 읽습니다.

## 주요 파일

| 파일 | 역할 | 실행 |
|---|---|---|
| `backfill_clip_thumbnails.py` | 클립 썸네일을 한꺼번에 만듭니다(멱등). 정기 작업(`run_pending_clip_thumbnails`, 1분)과 같은 서비스 함수 `clip_thumbnail.backfill`을 불러 규칙이 같습니다. 같은 락을 잡으므로 같은 클립을 두 번 만들지 않고, beat를 멈추지 않아도 됩니다. 정기 작업보다 빨리 끝내야 할 때만 씁니다 | `uv run --package api-gateway python scripts/ops/backfill_clip_thumbnails.py [--force] [--project <id>]` |
| `measure_profanity_hits.py` | 비속어 사전이 실제 캡션에서 무엇을 잡는지 낱말별로 셉니다. 판정은 `caption_qc.rules.check_profanity`를 그대로 부릅니다 | `uv run --package api-gateway python scripts/ops/measure_profanity_hits.py <project_id>` |
| `maiu_orphan_assets.sql` | 읽기 전용 점검. 분석 이벤트에 남은 MI 에셋 번호 가운데 지금 그 영상의 분석 잡이 가리키지 않는 번호를 프로젝트별로 셉니다. 트랜잭션을 읽기 전용으로 열고 `ROLLBACK`으로 닫습니다. 지울지는 사람이 MI 목록과 맞춰 보고 정합니다 | `psql "$DATABASE_URL" -f scripts/ops/maiu_orphan_assets.sql`(psql 형식 URL) |
| `ncp_oss_upload.py` | NCP Object Storage에 파일 하나를 PUT으로 올립니다(raw HTTP + AWS SigV4). 키는 앱 DB의 자격증명 연결(`--connection`)에서 읽고, 버킷·주소·리전을 비우면 그 연결의 값을 씁니다 | `uv run --package api-gateway python scripts/ops/ncp_oss_upload.py --connection <이름> --local x.mp4 --key k [--bucket b]` |

## 설계 이유

- 서버 모델과 서비스 함수를 그대로 부릅니다. 판정을 여기서 다시 구현하면 서비스와 스크립트의 규칙이 달라집니다.
- 멱등으로 만듭니다. 중간에 끊겨도 다시 실행하면 됩니다.
- NCP 키를 env로 받지 않습니다. 앱과 같은 방식으로 DB의 자격증명 연결을 이름으로 읽거나, 프로젝트가 고른 대상을 씁니다.
  키가 env와 DB 두 곳에 있으면 어느 쪽 키가 쓰였는지 알 수 없습니다.

## 의존성

- `apps/api-gateway`(모델·서비스·설정·자격증명 복호)

| 환경변수 | 쓰는 스크립트 | 설명 |
|---|---|---|
| `DATABASE_URL` | DB를 쓰는 스크립트 전부 | 운영이면 서버의 값을 줍니다 |
| `CREDENTIALS_KEY` | `ncp_oss_upload.py` | 자격증명 연결 복호 키. 운영이면 서버의 값을 줍니다 |

`ncp_oss_upload.py`의 SigV4 서명은 `apps/api-gateway/tests/ops/test_ncp_oss_upload.py`가 외부 호출 없이 확인합니다.

## 남은 일

- 없음.
