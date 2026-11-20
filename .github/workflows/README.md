# .github/workflows/

GitHub Actions 워크플로를 설명합니다.

`.github/` 바로 아래에는 README를 두지 않습니다. GitHub는 저장소 첫 화면에 `.github/README.md`를 루트 `README.md`보다 먼저 보여 줍니다.
그래서 `.github/`에 README가 있으면 저장소 소개 대신 이 폴더 설명이 첫 화면에 뜹니다.

## workflows/ci.yml의 잡 3개

| 잡 | 하는 일 | 막는 것 |
|---|---|---|
| `lint` | `ruff check .`(테스트를 포함한 전 범위)와 `lint-imports` | 스타일 회귀, 도메인 경계 위반(허용 목록 밖 교차 import) |
| `images` | api, worker-io, worker-jobs, worker-gpu, web, pgbackup 이미지 빌드(`linux/amd64`). main push이면 저장소 변수 `IMAGE_REGISTRY`가 가리키는 NCP Container Registry에 `rebornstudio-<이름>:sha-<커밋 sha>` 태그로 push합니다. 로그인은 시크릿 `NCP_ACCESS_KEY`·`NCP_SECRET_KEY`로 하고, `latest` 태그는 만들지 않습니다. PR에서는 Dockerfile이나 의존성 파일이 바뀐 경우에만 빌드합니다 | 빌드를 확인하지 않은 머지. 배포할 이미지를 PR에서 미리 빌드해 봅니다 |
| `api-gateway` | alembic upgrade → super_admin seed → pytest(reborn-transcode) → pytest(worker-jobs) → pytest(api-gateway, `-n 4`) → pytest(worker-io) → OpenAPI drift → TS contracts drift | 파이썬 워크스페이스 멤버 넷의 회귀, 스키마를 고치고 `make openapi`·`make contracts`를 돌리지 않은 커밋 |

## 실행 환경

- `ubuntu-latest`, Python 3.11과 uv(캐시)를 씁니다.
- 서비스는 postgres 16과 redis 7입니다. postgres는 로컬 compose와 같은 `5433:5432` 매핑을 써서 `DATABASE_URL`이 로컬과 같습니다.
- 오브젝트 스토리지 서비스는 없습니다. 테스트는 인프로세스 moto를 씁니다.

## 트리거

CI는 수동 실행(Actions 탭의 Run workflow)으로만 돕니다. push와 PR로는 자동 실행하지 않습니다.

- CI를 수동으로 돌리면 바뀐 파일 목록을 알 수 없어 모든 잡이 돕니다. 이미지는 빌드만 하고 레지스트리에 올리지 않습니다.
- 자동 실행을 켤 때는 `ci.yml`에 `push: [main]`과 `pull_request:`를 다시 더합니다.
  CI는 PR에서 돌아야 브랜치에 검증하지 않은 커밋이 쌓이지 않습니다.

## 로컬에서 CI와 같은 것을 돌리려면

```bash
uvx ruff check .
(cd apps/api-gateway && uv run --group test lint-imports)
make openapi && make contracts && git diff --exit-code
(cd apps/api-gateway && uv run --package api-gateway --group test pytest tests/)
(cd packages/reborn-transcode && uv run --package reborn-transcode --group test pytest tests/)
(cd workers/worker-jobs && uv run --package worker-jobs --group test pytest tests/)
(cd workers/worker-io && uv run --package worker-io --group test pytest tests/)
```

`scripts/ci/verify.sh --all`(`make verify-all`)이 위 명령을 모두 전용 DB에서 CI와 같은 순서로 실행합니다.
`api-gateway` 잡은 `lint` 잡의 `backend` 필터가 참일 때만 돕니다. 파이썬 워크스페이스 멤버를 더하면 그 필터에도 경로를 더합니다.

로컬 검사는 작업 트리를 보고 CI는 커밋을 봅니다. 파일을 고쳐 놓고 `git add`를 빠뜨리면 로컬에서만 통과합니다.
