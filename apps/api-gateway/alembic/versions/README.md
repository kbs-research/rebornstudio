# alembic/versions

Alembic 마이그레이션 파일입니다. 파일들은 하나의 선형 체인을 이룹니다. `revision`은 네 자리 번호(`"0092"`)이고, `down_revision`은 바로 앞 번호입니다.
파일 목록과 순서는 `ls`로 봅니다. 각 파일이 무엇을 바꾸는지는 그 파일의 docstring에 있으므로, 이 문서에 목록을 옮겨 적지 않습니다.

```bash
ls apps/api-gateway/alembic/versions/*.py | tail -5                       # 코드의 head
psql … -c "select version_num from alembic_version"                       # DB 장부
```

## 주요 파일

| 파일 | 역할 |
|---|---|
| `NNNN_<slug>.py` | 마이그레이션 하나. `upgrade()`와 `downgrade()`를 모두 둡니다 |
| `0001_initial.py` | 조직·사용자·프로젝트·멤버 테이블과 기본 조직 한 행. 프로젝트와 계정은 만들지 않습니다 |
| `0002_super_admin_seed.py` | 체인을 잇는 빈 리비전. 이전 버전이 만든 개발용 super_admin은 `0057_suspend_dev_admin_seed.py`가 정지합니다 |
| `0107_drop_initial_seeds.py` | 이전 버전의 0001·0038이 만든 시드 프로젝트와 0002의 시드 계정을 아무 행도 가리키지 않으면 지웁니다. 쓰인 시드는 시드 값만 비웁니다. 시드 값은 md5로만 둡니다 |
| `0108_connection_allowed_buckets.py` | 저장소 연결 중 허용 버킷 목록이 없는 연결에 그 연결의 버킷과 그 연결을 고른 프로젝트들의 버킷을 채우고 감사 기록을 남깁니다. 데이터만 채우고 되돌리기는 아무것도 하지 않습니다 |

## 작성 규칙

- 파일 이름은 `NNNN_<slug>.py`이고 `revision`은 `NNNN`입니다. `alembic revision`의 기본 ID는 해시이므로 `--rev-id NNNN`을 줍니다.
- 번호는 머지 순서로 정합니다. 두 브랜치가 같은 번호를 만들면 먼저 머지된 쪽이 번호를 갖습니다. 나중 쪽은 파일 이름, `revision`, `down_revision`을 함께 옮깁니다. `down_revision`을 그대로 두면 두 파일이 같은 부모를 가리켜 head가 둘이 됩니다.
- docstring에는 무엇이 바뀌는지, 기존 행을 어떻게 이행하는지, 되돌리면 어떻게 되는지를 씁니다. 절이 여러 개면 `## 무엇이 바뀌나`·`## 이행`·`## 되돌리기` 제목을 씁니다.
- 컬럼은 NULL 허용이나 `server_default`와 함께 추가합니다. 구버전 코드는 새 컬럼에 값을 넣지 않기 때문입니다.
- 컬럼·테이블 삭제(contract)는 그 컬럼을 읽지 않는 코드가 `main`에 머지되고 배포된 뒤에 별도 파일로 합니다. 먼저 적용하면 구버전 코드가 `UndefinedColumnError`로 500을 냅니다.
- 0094 이후(RebornStudio Pro에 없는 마이그레이션)의 `downgrade()`는 지운 것을 원래 타입·제약 그대로 되살립니다. 0093까지 되돌린 스키마는 RebornStudio Pro와 호환되어야 하기 때문입니다.
- CHECK 제약은 수정할 수 없으므로 지우고 다시 만듭니다. 값 목록을 넓힐 때는 기존 행이 새 제약을 통과하는지 먼저 확인합니다.

## 설계 이유

마이그레이션은 배포 시점의 구버전 코드와 호환되어야 합니다.

## 의존성

- 부르는 것: `alembic.op`, SQLAlchemy. 모델 클래스는 import하지 않습니다. 모델이 바뀌면 과거 마이그레이션의 의미가 달라지기 때문입니다.
- 부르는 쪽: `alembic/env.py`

## 남은 일

- 대량 백필을 스키마 변경과 함께 실행하는 파일이 있습니다(`0050_backfill_transcode_failed.py`·`0059_backfill_orphan_ingest_batches.py`·`0064_analysis_job_backfill.py`). 새 백필은 ADR의 규칙대로 배치로 분리합니다.
