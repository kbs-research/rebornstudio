# tests

api-gateway의 pytest입니다. 실제 PostgreSQL과 Redis, 프로세스 안에서 띄운 moto(S3 호환 서버)를 씁니다. 외부 NCP·MI는 부르지 않습니다(`live` 마커 제외).
FastAPI 앱은 `httpx.AsyncClient`와 `ASGITransport`로 서버 없이 구동합니다. 단위 테스트와 통합 테스트를 나누지 않고, 실제 DB에서 라우터부터 서비스까지 확인합니다.
케이스 수는 적지 않습니다. `uv run --group test pytest tests/ --collect-only -q | tail -1`로 셉니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `conftest.py` | 공용 픽스처(`client`·`admin_token`·`fake_ffmpeg`)와 `uniq()`, `live` 마커 건너뛰기, 연결 점검이 외부 서비스에 접속하지 않게 하는 대역(`real_probe` 표지로 끔), 세션 종료 시 테스트 잔여 행 정리(`pytest_sessionfinish`). `REBORN_ENV_FILE`을 비워 로컬 `.env`를 읽지 않습니다 |
| `_xdist_split.py` | 병렬 실행에서 워커마다 전용 DB(템플릿 복사)와 Redis 인덱스를 쓰게 합니다. 워커가 15개 이상이면 실행을 막습니다 |
| `_moto_bootstrap.py` | 프로세스 안에 moto 서버를 띄우고 주소와 버킷을 정합니다 |
| `_storage.py` | moto를 가리키는 NCP 저장소 연결을 DB에 등록하고, 테스트 프로젝트에 저장소 대상으로 기록합니다(`pin_project`·`connection_for`). 객체 직접 업로드(`put_bytes`·`store`)도 맡습니다 |
| `_analysis.py` | 분석 대상 저장 테스트가 고를 MI 연결(`mi_connection`). 연결 저장 때 도는 점검의 워크스페이스 조회만 가짜로 바꿉니다 |
| `_helpers.py` | 테스트마다 따로 만드는 계정·프로젝트·멤버·영상 업로드·클립 작업 시드. 공개 가입이 없으므로 계정은 `make_account`(이메일·비밀번호까지 돌려줌)와 `make_user`가 관리자 생성 경로로 만듭니다. `caption_payload`는 제출 최소 길이를 넘는 캡션을 만듭니다. 처리 옵션은 API로 바꿀 수 없는 고정 기본값이라, 옵션에 따라 달라지는 동작은 `set_project_options`로 프로젝트 행에 값을 직접 써서 테스트합니다 |

하위 폴더 이름은 테스트가 주로 부르는 서비스의 도메인 이름을 씁니다. `api_gateway/services/`의 패키지 이름과 같습니다.

| 폴더 | 담는 것 |
|---|---|
| [`auth/`](auth/README.md) | 로그인·세션·관리자 계정 생성·계정 상태·개인정보 동의·최초 설정·인증 경로 보호 |
| [`org/`](org/README.md) | 기본 조직 한 행·프로젝트·멤버·역할 |
| [`media/`](media/README.md) | 영상·클립·저장소·변환·비식별화·준비 단계·재생본 |
| [`analysis/`](analysis/README.md) | 분석 어댑터 계약·프리셋·잡 상태·샷 검출·옵션 |
| [`maiu/`](maiu/README.md) | MI 연동. 분석 결과 회수 작업·분석 요청·비용·클라이언트 |
| [`work/`](work/README.md) | 라벨링·검수·비식별화 신고 처리·반려 누적·보유 목록·제출 게이트 |
| [`workforce/`](workforce/README.md) | 본인 판정 목록·초대 가드·다음 작업 배정·보유 해제 |
| [`analytics/`](analytics/README.md) | 대시보드·통계·운영 게이지 |
| [`caption_qc/`](caption_qc/README.md) | 캡션 품질검수 |
| [`exports/`](exports/README.md) | 납품 패키지·완료 목록·납품 규격 대조 |
| [`ops/`](ops/README.md) | 감사 로그·연동 진단·운영 가드 |
| [`stacks/`](stacks/README.md) | 자격증명 연결·프로젝트 스택·저장소 어댑터 계약 |
| [`kernel/`](kernel/README.md) | `services/` 평면 모듈의 규칙 |
| [`contract/`](contract/README.md) | 도메인을 가로지르는 계약. 두 런타임 판정 표, import 계약, 스키마 |
| [`e2e/`](e2e/README.md) | 앱 바깥에서 도는 종단·스모크 |

### 둘 이상에 걸친 파일의 위치

| 파일 | 폴더 | 이유 |
|---|---|---|
| `test_auth_events.py` | auth | 본인 이력 조회는 `workforce.login_history`에 있지만, 테스트 대상은 로그인·실패·로그아웃 기록입니다 |
| `test_fixed_options.py` | org | 분석 요청의 422도 보지만, 테스트 대상은 프로젝트 생성·수정 요청이 받는 필드입니다 |
| `test_labeler_approval_owner.py`·`test_pipeline_summary.py` | analytics | `analytics.dashboard`가 세는 값을 봅니다 |
| `test_prep_stage.py` | media | 부르는 함수가 `media.clip`·`media.video`에 있습니다 |
| `test_storage_register.py`·`test_transcode_gate.py` | media | 분석 요청을 거치지만, 테스트 대상은 등록·변환 게이트입니다 |
| `test_analysis_purge.py` | media | 분석본 정리는 `media.retention`이 합니다 |
| `test_scene_options.py` | analysis | 클립 길이 설정은 `analysis.scene_options`가 정합니다 |
| `test_ops_guards.py`·`test_prod_docs_closed.py` | ops | 감사 보존과 운영 설정 가드를 봅니다 |
| `test_ncp_oss_upload.py` | ops | `scripts/ops/ncp_oss_upload.py`의 단위 테스트입니다 |
| `test_meta_reject_codes.py` | kernel | 평면 모듈 `video_meta`·`reject_reasons`의 규칙을 여러 도메인에서 봅니다 |
| `test_caption_gate.py`·`test_caption_shapes.py` | contract | 서버와 웹이 같은 판정 표(`packages/reborn-contracts/caption-gate.json`·`caption-shapes.json`)를 읽습니다 |
| `test_transient_rule_sync.py` | contract | 서버의 일시 오류 판정과 워커 복사본을 대조합니다 |
| `test_web_server_limits.py` | contract | 상한이 org·workforce·감사 라우트 세 곳에 있고, 웹 상수와 대조합니다 |
| `test_rls_baseline.py`·`test_spec_compliance.py` | contract | 스키마 전체를 봅니다 |

## 설계 이유

- 하위 폴더에 `__init__.py`를 두지 않습니다. pytest 기본 import 방식(`prepend`)은 이런 폴더의 테스트를 파일 이름으로 import합니다. 그래서 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 같은 이름이 두 폴더에 있으면 수집 단계에서 `import file mismatch`로 멈춥니다. `__init__.py`를 넣으면 `auth`·`media` 같은 폴더 이름이 최상위 패키지가 되어 다른 이름과 충돌합니다.
- `from tests._helpers import …`가 동작하는 이유는 `api-gateway` 편집 설치가 `apps/api-gateway`를 `sys.path`에 올리기 때문입니다. 그래서 `tests`가 네임스페이스 패키지로 보입니다. 다른 테스트 모듈을 import할 때는 폴더까지 적습니다(`from tests.work.test_work import …`).
- 경로를 계산하는 테스트는 폴더 깊이에 따라 결과가 달라집니다. 하위 폴더 파일에서 저장소 루트는 `Path(__file__).resolve().parents[4]`이고, `apps/api-gateway`는 `parents[2]`입니다. 파일을 다른 깊이로 옮기면 이 수도 고칩니다.
- 테스트 사이의 격리는 트랜잭션이 아니라 고유 email·slug(`uniq()`)로 합니다. 그래서 두 실행이 같은 DB를 쓰면 세션 종료 시 정리하는 DELETE가 다른 실행의 행을 지워 FK 위반이 납니다. 병렬 실행은 워커마다 전용 DB(`<기본DB>_gw0` …)를 템플릿에서 새로 만들어 이 문제를 피합니다.
- 앱은 저장소·분석 자격증명을 등록한 연결에서만 읽습니다. 그래서 테스트도 환경 변수 대신 moto 연결과, 가짜 점검을 쓰는 MI 연결을 DB에 등록합니다.

## 의존성

- 사전 조건: PostgreSQL과 Redis가 필요합니다. 테스트 DB에 `alembic upgrade head`를 적용합니다. 병렬 워커 DB는 이 DB를 템플릿으로 복사하므로 마이그레이션은 한 번이면 됩니다.
- 기본 DB는 `postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio_test`, 기본 Redis는 `redis://localhost:6380/0`입니다(`conftest.py`). 다른 DB·Redis는 `DATABASE_URL`·`REDIS_URL`로 지정합니다.
- 병렬 워커는 Redis 인덱스 1~15를 하나씩 씁니다. 같은 Redis에서 전체 실행 둘을 동시에 돌리면 인덱스가 겹치므로, 동시에 돌릴 때는 실행마다 다른 DB와 Redis를 줍니다.
- 워크스페이스 전체 설치가 필요합니다(`uv sync --all-packages --group test`). 일부 테스트가 `worker_io`를 import하기 때문입니다.

## 실행

```bash
cd apps/api-gateway
uv run --group test pytest tests/ -q -n 4                 # 전체(CI와 같은 병렬)
DATABASE_URL=postgresql+asyncpg://reborn:reborn@localhost:5433/my_test \
  REDIS_URL=redis://localhost:6399/0 uv run --group test pytest tests/ -q -n 4   # 다른 DB·Redis로
uv run --group test pytest tests/work/ -q                 # 도메인 하나
uv run --group test pytest tests/e2e/test_smoke.py -v     # 파일 하나
MAIU_LIVE_TEST=1 uv run --group test pytest tests/maiu/test_naver_maiu_live.py   # 실제 NCP 호출
```

`live` 마커가 붙은 테스트는 `MAIU_LIVE_TEST=1`일 때만 실행됩니다. 그 밖에는 건너뜁니다.

## 남은 일

- 시간과 동시성에 의존하는 테스트 몇 개가 부하가 겹친 실행에서 불안정합니다.
