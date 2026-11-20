# docs

## 목적

저장소 문서의 지도입니다. 읽는 사람별로 먼저 볼 문서를 모았습니다.
서버에 설치하는 절차는 [`install.md`](install.md), 저장소 소개는 루트 [`README.md`](../README.md)에 있습니다.

## 운영자

| 알고 싶은 것 | 문서 |
|---|---|
| 서버 사양과 처리 한계 산식 | [`install.md`](install.md)의 "서버 사양", "처리 한계 산식" |
| 설치 전 준비(서버, 외부 서비스, MI 워크스페이스, 버킷 규칙) | [`install.md`](install.md)의 "설치 전 준비" |
| 설치, TLS, 보안 설정, GPU 확인 | [`install.md`](install.md) |
| 업그레이드와 되돌리기 | [`install.md`](install.md)의 "업그레이드" |
| 환경변수 키의 뜻과 채울 곳 | [`env-catalog.md`](env-catalog.md). 운영 예시는 `infra/deploy/env.example`, 개발 예시는 루트 `.env.example` |
| 배포 스크립트의 순서, 서버 상태 보기, 롤백, 비밀번호 분실, 자격증명 암호화 키 교체 | [`infra/deploy/README.md`](../infra/deploy/README.md) |
| DB 백업과 복원 | [`infra/backup/README.md`](../infra/backup/README.md) |
| 보안 취약점 신고 | [`SECURITY.md`](../SECURITY.md) |

## 개발자

| 알고 싶은 것 | 문서 |
|---|---|
| 로컬 개발 스택과 처음 띄울 때의 순서 | [`infra/compose/README.md`](../infra/compose/README.md) |
| CI와 같은 검사를 로컬에서 돌리는 법 | [`scripts/ci/README.md`](../scripts/ci/README.md) |
| 서버 테스트의 위치와 규칙 | [`apps/api-gateway/tests/README.md`](../apps/api-gateway/tests/README.md) |
| 서버 API 명세 | `api/openapi.json`. `make openapi`로 서버 앱에서 생성하고 CI가 변경 여부를 검사합니다 |
| 작업 큐와 태스크 계약 | [`packages/reborn-contracts/queues.md`](../packages/reborn-contracts/queues.md) |
| 화면이 제공하는 경로 | [`apps/web/src/app/router.tsx`](../apps/web/src/app/router.tsx) |
| 화면 부품과 디자인 토큰 | [`apps/web/src/styles/README.md`](../apps/web/src/styles/README.md) |

폴더마다 README가 있습니다. 폴더의 목적, 주요 파일, 설계 이유, 의존성을 같은 순서로 적었습니다.

## 설계 이유

- 공개 저장소에는 설계 문서, 결정 기록, API 설명 문서를 싣지 않습니다. 변경 단위의 작업 기록과 작업 메모, 버전별 변경 이력과 릴리스 본문도 싣지 않습니다. 공개 저장소는 커밋 하나로 시작합니다.
- 설계 이유는 따로 모으지 않고 코드 가까이에 둡니다. 폴더 README의 "설계 이유" 절과 모듈 docstring이 그 폴더의 판단을 설명합니다.
- `api/openapi.json`은 문서가 아니라 화면 타입 생성(`make contracts`), 테스트, CI 검사가 읽는 계약 파일이라 싣습니다.

## 의존성

- `docs/api/openapi.json`은 `scripts/dev/gen_openapi.py`가 서버 앱에서 생성합니다.

## 남은 일

- 없음.
