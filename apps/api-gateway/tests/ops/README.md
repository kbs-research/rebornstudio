# tests/ops

플랫폼 운영 도메인(`services/ops`) 테스트입니다. 연동 점검, 운영 가드, 운영 환경 문서 닫기를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_csp_report.py` | CSP 위반 보고 수집(`POST /csp-report`)이 두 보고 형식을 인증 없이 받아 204로 답하는지, 로그에 막힌 주소의 출처와 화면 경로만 남기고 쿼리·서명·자격증명 id가 남지 않는지, 같은 위반을 묶고 줄 수를 자르는지, 다른 형식은 400, 64KiB를 넘는 본문은 413, 주소별 한도는 429인지 검사합니다. |
| `test_integration_watch.py` | 연동 점검이 아무도 화면을 보지 않을 때도 돌고, 바뀐 상태만 전이로 기록하는지 검사합니다. |
| `test_ncp_oss_upload.py` | ncp_oss_upload의 SigV4 서명과 캐노니컬 키를 단위 테스트합니다. |
| `test_ops_guards.py` | 운영·보안 가드를 검증합니다. 라벨러에게 완료 목록·상세를 닫는지, 일시정지가 가져오기만 막는지, 세션 갱신이 기본 조회에서 빠지는지, 감사 보존 기본값이 3년인지 봅니다. |
| `test_audit_retention_batches.py` | 감사 보존 정리가 한 회차에 묶음 크기보다 많은 만료 행도 모두 지우고 묶음마다 삭제 기록을 남기는지 봅니다. |
| `test_prod_docs_closed.py` | 운영 환경에서 대화형 API 문서를 닫는지 검증합니다. |
| `test_dev_token_output.py` | dev 토큰 발급 스크립트의 기본 출력이 refresh 쿠키를 심는 콘솔 한 줄이고 그 토큰으로 refresh가 되는지, `--access` 출력으로 API를 부를 수 있는지 봅니다. |
| `test_forwarded_trust.py` | 운영 compose가 api의 신뢰 프록시를 web·api 전용 edge 서브넷으로 좁히는지, edge 밖 컨테이너가 꾸민 `X-Forwarded-For`·`X-Forwarded-Proto`가 감사 출처·IP 한도 키·쿠키 `Secure`가 되지 않는지, web nginx가 믿는 앞단(`WEB_TRUSTED_PROXIES`)의 헤더만 넘기는지 봅니다. |
| `test_dev_token_guard.py` | dev 토큰 발급 스크립트가 `ENVIRONMENT` 없는 셸(기본값 prod)에서 멈추는지, `ENVIRONMENT=local`이어도 로컬이 아닌 DB 호스트에서 DB에 붙기 전에 멈추는지, compose 서비스 이름은 `ENVIRONMENT`를 명시했을 때만 받는지 봅니다. |
| `test_seed_admin_password.py` | 개발 super_admin 시드가 이미 있는 계정의 비밀번호를 명시했을 때만 바꾸고, 새 계정에는 무작위 비밀번호를 주는지 봅니다. |
| `test_seed_local_guard.py` | dev 시드 셋이 로컬이 아닌 DB와 개발 밖 `ENVIRONMENT`에서 DB에 붙기 전에 멈추는지 봅니다. |
| `test_seed_volume_analysis_rows.py` | 운영 밀도 시드의 분석 행이 화면 판정에서 분석 중으로 분류되고 분석 결과 회수 작업의 처리 대상에서 빠지는지 봅니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_ncp_oss_upload.py`는 `scripts/ops/ncp_oss_upload.py`의 단위 테스트입니다. `scripts/`는 패키지가 아니라서 그 폴더를 `sys.path`에 넣고 import합니다(`parents[4]`).
`test_seed_volume_analysis_rows.py`도 같은 방식으로 `scripts/dev/seed_dev_volume.py`를 import합니다. 테스트 환경은 로컬 DB라서 import 시점의 로컬 확인을 통과합니다.
`test_seed_local_guard.py`는 `scripts/dev/`의 시드를 별도 프로세스로 실행합니다. 확인은 import 시점에 돌고, 막지 못하면 그 프로세스가 DB 연결을 시도하므로 연결되지 않는 주소만 씁니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
