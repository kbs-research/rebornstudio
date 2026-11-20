# models

SQLAlchemy 2.0 ORM 모델(Declarative, `Mapped`)입니다. 서비스가 import하는 테이블 정의를 둡니다.
스키마는 `alembic/versions/`의 마이그레이션이 실제로 바꿉니다. 모델과 마이그레이션은 같은 커밋에서 함께 고칩니다.

## 주요 파일

| 파일 | 테이블 | 역할 |
|---|---|---|
| `__init__.py` | — | 공개 모델 목록. 새 모델은 여기서 import해야 `Base.metadata`에 등록됩니다 |
| `base.py` | — | `Base`(DeclarativeBase)와 `TimestampMixin`(`created_at`·`updated_at`, DB 시각) |
| `user.py` | `users`, `password_reset_tokens` | 전역 사용자(이메일, `global_role`·`approval_status`(활성·정지), 필수·선택 개인정보 동의 시각, `password_changed_at`)와 관리자가 발급한 1회용 재설정 토큰(해시만 저장). 시작 안내 시각 `onboarded_at`은 읽지 않는 컬럼입니다 |
| `org.py` | `organizations`, `projects`, `project_members` | 기본 조직 한 행(상수 식 유일 인덱스), 프로젝트, 멤버십. 프로젝트 `status`와 `settings`(JSONB), `deleted_at` soft delete. 멤버 `role`·`status`. 동시 작업 한도·보유 만료 시간·참여 기간·전문 분야 컬럼은 읽지 않습니다 |
| `connection.py` | `connections` | 자격증명 연결. 시크릿은 Fernet 암호문(`secret_ciphertext`)으로만 저장하고, 마지막 점검 결과를 함께 둡니다 |
| `video.py` | `videos` | 영상 메타, 준비 단계 상태(probe·transcode·deident·publish와 리스·시도 횟수), 권리 정책·상태 컬럼(`copyright_policy`는 `pre_cleared`, `copyright_status`는 `cleared` 하나뿐입니다), 영상 단위 메타(`broadcaster`·`program_name`·`genre`·`aired_at` 등), soft delete |
| `clip.py` | `clips` | 영상을 나눈 구간. 구간 시각, `rights_status`(생성 시 `auto_cleared`, 비식별화 미흡 신고 중 `legal_review`, 신고 제외 처리 뒤 `rejected`), 자동 캡션(`auto_caption`), `extra`(DB 컬럼 `metadata`) |
| `work.py` | `work_items`, `caption_qc_runs` | 라벨링·검수 작업 큐(상태, 점유·리스, 초안, 반려 횟수, `qc_status`)와 캡션 품질검수 실행 이력 |
| `label.py` | `labels`, `reviews` | 라벨 수정 이력(`parent_label_id` 자기 참조)과 검수 판정 이력(`verdict`, 반려 사유 코드 `reason_codes`, 자유 입력 `comment`) |
| `analysis_job.py` | `analysis_job` | 어댑터와 무관한 분석 작업 상태. `clip_id`가 비면 영상 단위, 있으면 클립 단위입니다. 부분 성공은 `partial` 상태로 나타냅니다 |
| `media_run.py` | `media_stage_runs` | 준비 단계의 시도 이력(단계·상태·소요). 관측용이며 진행 판정에는 쓰지 않습니다 |
| `ingest_batch.py` | `ingest_batches` | 한 번에 반입한 묶음. 경로(`upload`·`ncp`)와 폴더 등록의 비동기 상태 |
| `export.py` | `exports` | 납품 패키지 생성 기록. 상태, 표본 수, 선택 항목 생략 여부, 저장 위치(`manifest_s3_key`·`storage_bucket`), 시도 횟수 |
| `event.py` | `domain_events` | 감사 로그와 통합 이벤트 outbox. 행은 추가만 합니다. 행을 지우는 곳은 보존 기간 정리(`services/ops/audit_retention`) 하나입니다 |

상태 값 목록은 각 모델의 `CheckConstraint`와 `Literal` 타입을 기준으로 봅니다. 이 문서에 옮겨 적지 않습니다.

## 설계 이유

- PK는 UUID이고 파이썬에서 `uuid4()`로 만듭니다. 추가만 하는 테이블(`media_stage_runs`)과 `analysis_job`은 정수 자동 증가 PK를 씁니다.
- 열거 값은 PostgreSQL ENUM 타입 대신 `CheckConstraint`로 제한합니다. 그러면 값을 더할 때 제약을 지우고 다시 만드는 마이그레이션 하나로 끝납니다.
- 시각은 모두 `DateTime(timezone=True)`입니다. 날짜 경계 집계는 KST 기준으로 자릅니다.
- `videos.extra`·`clips.extra`(DB 컬럼 `metadata`)에는 여러 워커가 같은 행에 씁니다. 파이썬에서 읽어 합친 뒤 통째로 쓰면, 그 사이 다른 워커가 쓴 키가 사라집니다. 그래서 병합은 `merge_extra`(`metadata || patch`)로만 합니다. `.extra`에 직접 대입하는 코드는 `scripts/ci/check-jsonb-merge.py`가 찾아냅니다.
- `deleted_at` soft delete의 조회 필터는 서비스가 겁니다. 모델에는 기본 필터를 두지 않습니다.
- 읽지 않는 컬럼 가운데 일부는 RebornStudio Pro와 같은 스키마를 유지하려고 남깁니다. 그런 컬럼은 모델 주석에 그 사실을 적습니다.
- `domain_events`의 FK에는 cascade delete를 두지 않습니다. 사용자나 프로젝트를 지워도 감사 기록은 남아야 하기 때문입니다.

## 의존성

- 부르는 것: SQLAlchemy, `base.py`
- 부르는 쪽: `api_gateway/services/*`, 라우터(주로 `User` 타입), `alembic/env.py`(`Base.metadata`), `workers/worker-io`

## 남은 일

- RLS는 핵심 테이블 6개(`projects`·`videos`·`work_items`·`clips`·`labels`·`reviews`)에 켜져 있습니다. 다만 정책은 모두 허용하는 기본 정책이고, 프로젝트 단위 정책은 아직 없습니다.
- `projects.conversations_enabled`는 읽는 코드가 없는 미사용 컬럼입니다.
- 프로젝트 slug의 전체 유일성은 DB 제약 없이 코드로만 지킵니다.
