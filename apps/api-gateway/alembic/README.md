# alembic

PostgreSQL 스키마 마이그레이션입니다. 앱은 async 드라이버(asyncpg)를 쓰고, 마이그레이션은 sync 드라이버(psycopg)로 실행합니다.
운영에서는 compose의 `migrate` 서비스(한 번 실행하고 끝나는 서비스)가 앱보다 먼저 `alembic upgrade head`를 실행합니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `env.py` | 실행 환경. 접속 주소는 `DATABASE_URL`로 받고, 없으면 멈춥니다. `+asyncpg` 주소를 `+psycopg`로 바꿉니다. 메타데이터는 `api_gateway.models.Base`입니다. 연결마다 락 대기 상한(`lock_timeout` 5초)을 겁니다 |
| `script.py.mako` | `alembic revision`이 새 파일을 만들 때 쓰는 템플릿 |
| `versions/` | 마이그레이션 파일. 번호와 작성 규칙은 `versions/README.md`에 있습니다 |

## 명령

```bash
cd apps/api-gateway
DATABASE_URL="postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio" \
  uv run --package api-gateway alembic upgrade head      # 적용
DATABASE_URL=… uv run --package api-gateway alembic current         # 장부(alembic_version) 확인
DATABASE_URL=… uv run --package api-gateway alembic downgrade -1    # 로컬에서 한 단계 되돌리기
```

`alembic.ini`에는 기본 주소가 없습니다. `DATABASE_URL`이 없으면 `env.py`가 연결하기 전에 멈춥니다.
기본 주소를 두면 `DATABASE_URL`을 빠뜨린 실행이 호스트의 다른 postgres에 조용히 적용되기 때문입니다.
compose의 `migrate` 서비스(개발·운영)와 CI는 `DATABASE_URL`을 넘깁니다.

## 설계 이유

- 모든 마이그레이션은 배포 시점의 구버전 코드와 호환되어야 합니다. 롤링 배포 중에는 구버전 레플리카가 새 스키마에서 동작하기 때문입니다. 컬럼 추가는 바로 합니다. 삭제와 이름 변경은 expand와 contract를 두 릴리스로 나눕니다.
- 마이그레이션은 락을 5초까지만 기다립니다. 배포는 이전 앱이 도는 중에 마이그레이션을 실행하는데, ALTER TABLE이 긴 트랜잭션 뒤에서 락을 기다리면 그 테이블을 읽는 요청이 모두 줄을 서서 화면 전체가 멈춥니다. 상한을 넘기면 그 마이그레이션은 실패하고 바뀐 것 없이 끝나므로 다시 실행합니다.
- 운영 롤백은 `downgrade`가 아니라 앱 버전 롤백으로 합니다. 새 스키마가 구 코드와 호환되므로 스키마는 그대로 둡니다. `downgrade()`는 로컬 개발용입니다.
- autogenerate는 부분 인덱스, CHECK 제약, RLS 정책을 빠뜨립니다. 그래서 마이그레이션은 직접 작성하고, autogenerate 결과는 참고로만 씁니다.
- 머지하기 전의 마이그레이션을 DB에 적용하면 장부(`alembic_version`)에 그 번호가 남습니다. 같은 번호의 다른 마이그레이션이 머지되면 그 DB에서는 실행되지 않습니다. 그래서 브랜치의 마이그레이션은 별도 DB에서 확인합니다.

## 의존성

- 부르는 것: `api_gateway.models`(메타데이터), psycopg
- 부르는 쪽: compose `migrate` 서비스, CI `api-gateway` 잡의 `Alembic migrate` 단계, 개발자 수동 실행

## 남은 일

- 대량 백필은 스키마 마이그레이션과 분리해 배치로 돌립니다. 기존 마이그레이션 중에는 백필을 함께 실행하는 파일이 있습니다(`versions/README.md`).
