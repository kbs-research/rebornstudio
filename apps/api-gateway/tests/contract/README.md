# tests/contract

도메인을 가로지르는 계약·경계 테스트입니다. 서버와 웹이 같이 읽는 판정 표, import-linter 계약 목록, OpenAPI·DB 스키마 정합, RLS 기준선, `scripts/` import, 일시 오류 판정과 워커 복사본의 일치, 화면이 옮겨 적은 서버 상한을 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_broker_visibility.py` | 두 Celery 앱의 브로커 재전달 시간이 같고 잡 URL 유효 기간보다 긴지 확인합니다. |
| `test_caption_gate.py` | 캡션 제출 게이트의 순수 판정을 검사합니다. 판정 표(`caption-gate.json`)를 그대로 돌립니다. |
| `test_connection_address.py` | 연결 수정의 시크릿 재입력 판정 표(`connection-address.json`)를 서버 구현으로 돌리고, 서버의 주소 키 표가 판정 표·연결 종류 목록과 같은지 확인합니다. |
| `test_caption_shapes.py` | 캡션 읽기 규칙의 판정 표(`caption-shapes.json`)를 서버 구현으로 돌립니다. |
| `test_labeler_rates.py` | 사람별 실적 표의 승인율·반려율 판정 표(`labeler-rates.json`)를 서버 구현(`_rate_fields`)으로 돌리고, 반려율이 행에 싣는 반려 수와 분모의 몫인지 확인합니다. |
| `test_initial_seed_cleanup.py` | 마이그레이션 0107이 손대지 않은 시드 프로젝트·시드 계정만 지우고, 행이 가리키는 시드는 시드 값만 비우는지 트랜잭션 안에서 확인합니다. |
| `test_container_users.py` | 운영 이미지 여섯의 마지막 `USER`가 정한 비 root 사용자인지, 파이썬 이미지가 uid·gid 10001의 `app`을 만들고 `/app` 디렉터리만 넘기는지, web이 자기 폴더만 쓰는지 Dockerfile을 읽어 대조합니다. |
| `test_import_contracts.py` | import-linter 계약의 모듈 목록이 `services/`의 실제 파일과 맞는지 검사합니다. |
| `test_rls_baseline.py` | RLS baseline 마이그레이션(alembic 0017)을 검증합니다. |
| `test_route_uniqueness.py` | 한 핸들러 함수가 한 (경로, 메서드)에만 등록되는지 확인합니다. 라우트를 지울 때 남은 데코레이터가 다른 함수에 붙는 경우를 잡습니다. |
| `test_scripts_import.py` | `scripts/`가 삭제된 심볼을 아직 부르고 있지 않은지 검증합니다. |
| `test_spec_compliance.py` | 기능 명세에 정의한 enum과 스키마가 실제 DB와 맞는지 보는 회귀 테스트입니다. |
| `test_transient_rule_sync.py` | 일시/영구 오류 판정의 기준 정의와 워커 복사본이 어긋나지 않는지 검사합니다. |
| `test_public_routes.py` | 로그인 확인 의존성이 없는 라우트가 모두 `PUBLIC_ROUTES` 표에 이유와 함께 있는지, 표에 인증 있는 라우트나 없는 라우트가 남지 않았는지 앱의 의존성 나무로 확인합니다. |
| `test_web_nginx_security.py` | 운영 web nginx 설정이 강제 CSP(스크립트는 같은 출처만, 저장소 출처·blob·data 글꼴만 연다, 위반 보고는 `/api/csp-report`)와 쿼리 없는 접근 로그, 연결 주소로 덮어쓰는 X-Forwarded-For를 지키는지 설정 파일을 읽어 대조합니다. CSP는 시작 스크립트(`web-csp.sh`)를 이미지 밖에서 실행해 만든 헤더 파일로 보고, `CSP_STORAGE_ORIGINS` 기본값·설정값·틀린 형식의 시작 거절, 서버 presigned 주소가 기본 출처 안인지도 봅니다. |
| `test_web_server_limits.py` | 화면이 옮겨 적은 서버 상한(메타 일괄 입력 파일의 크기와 행 수)이 서버 값과 같은지 웹 소스를 읽어 대조합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
여기 파일 대부분은 저장소의 다른 곳(`packages/reborn-contracts`·`scripts`·`pyproject.toml`)을 읽습니다. 경로가 틀리면 파일을 못 찾거나 단언이 빈 목록을 잡아 빨간불이 켜집니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
