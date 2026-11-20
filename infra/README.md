# infra

## 목적

서비스를 띄우고, 배포하고, 지키는 데 필요한 정의를 둡니다. 컨테이너 이미지, docker compose 형상, 배포 스크립트, Postgres 백업이 여기 있습니다.
개발과 운영 모두 단일 호스트 docker compose로 실행합니다.

## 주요 파일

| 폴더 | 역할 |
|---|---|
| [`docker/`](docker/README.md) | Dockerfile 여섯 개(api·worker-io·worker-jobs·worker-gpu·web·pgbackup)와 web nginx 설정 |
| [`compose/`](compose/README.md) | 공통 `compose.yaml`과 개발·운영 덮어쓰기 파일(`compose.dev.yaml`·`compose.prod.yaml`) |
| [`deploy/`](deploy/README.md) | 서버에서 실행하는 배포 스크립트, compose 래퍼, 프로필 규칙, 운영 `.env` 예시, 서버 준비 절차 |
| [`backup/`](backup/README.md) | Postgres 논리 백업·복원 스크립트(`pg-backup` 서비스) |

## 설계 이유

| 환경 | 형상 | 오브젝트 저장소 |
|---|---|---|
| 개발 | `compose/compose.yaml` + `compose.dev.yaml`(`make dev-up`) | NCP Object Storage |
| 운영 | NCP 서버 한 대, `compose/compose.yaml` + `compose.prod.yaml` | NCP Object Storage |

- 두 환경의 서비스 목록과 워커별 소비 큐는 `compose.yaml` 한 곳에 있습니다. 그래서 개발에서 검증한 큐 분리가 운영에 그대로 올라갑니다.
- 컴퓨트와 DB를 NCP에 둡니다. 저장소(NCP Object Storage)와 분석(NCP Media Intelligence)이 NCP에 있고, 트랜스코딩은 원본을 내려받고 산출을 올립니다.
  컴퓨트나 DB가 다른 클라우드에 있으면 그 전송과 DB 왕복이 모두 클라우드 사이의 과금 트래픽이 됩니다.
- 개발도 로컬 저장소 서비스 없이 NCP Object Storage를 씁니다. 로컬 저장소를 두면 컨테이너 안과 호스트에서 저장소 주소가 달라집니다.
  그러면 presigned URL을 만드는 경로가 환경마다 달라집니다. 그래서 로컬 개발에도 네트워크와 NCP 자격증명 연결이 필요합니다.
  테스트는 인프로세스 moto를 써서 네트워크 없이 실행됩니다.
- 클러스터 배포 형상은 두지 않습니다.

## 의존성

- 이미지 빌드: 저장소 루트를 context로 씁니다(`apps/`·`packages/`·`workers/`의 소스와 `uv.lock`·`pnpm-lock.yaml`).
- 외부: NCP Object Storage, NCP Media Intelligence, NCP Container Registry. 저장소와 MI 자격증명은 env가 아니라 앱 DB의 자격증명 연결에 있습니다.
- 부르는 쪽: 루트 `Makefile`(`dev-up`·`dev-migrate` 등), `.github/workflows/ci.yml`(이미지 빌드), `infra/deploy/build-images.sh`

## 남은 일

- 지표 수집과 알림이 없습니다. 서버 상태는 `dc.sh ps`·`logs`와 관리자 화면의 연동 진단으로 확인합니다.
- TLS 종료가 없습니다. web은 80 포트만 엽니다.
