# backup

## 목적

운영 Postgres를 논리 백업해 NCP Object Storage에 올리고, 그 백업을 복원하는 스크립트입니다.
운영 DB는 서버 안의 컨테이너라서 관리형 DB가 주는 자동 백업과 PITR이 없습니다. 라벨링 결과 전체가 서버 디스크 하나에 있으므로 이 폴더가 백업을 맡습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `backup.sh` | `pg_dump -Fc` → gzip → NCP Object Storage 업로드 → 보관 기간 정리 → 하트비트 기록 |
| `restore.sh` | 기본은 검증 복원(별도 DB `rebornstudio_restore_check`에 붓고 행 수를 운영과 비교). 운영 DB를 덮어쓰려면 `--into-production`을 명시해야 합니다. 이때는 public 스키마를 지우고 다시 만든 뒤 덤프를 붓고, 지우기와 붓기를 한 트랜잭션으로 묶습니다 |
| `../docker/pgbackup.Dockerfile` | `pg_dump`·`pg_restore`·aws-cli·redis-cli를 담은 이미지. 12시간(43200초)마다 `backup.sh`를 실행합니다 |

## 설계 이유

- 논리 백업(`pg_dump`)을 씁니다. DB에는 메타데이터만 있고 영상·클립 파일은 모두 오브젝트 저장소에 있어 덤프가 작습니다.
  논리 백업은 Postgres 버전이 달라도 복원되고, 한 테이블만 꺼낼 수도 있습니다. PITR이 필요할 만큼 커지면 NCP Cloud DB로 옮깁니다.
- 운영에서는 백업을 늘 켭니다. 주기 12시간, 보관 30일, 키 접두어 `backups/postgres`를 `compose.prod.yaml`에 고정하고 `.env`로 받지 않습니다.
  백업 버킷과 키가 비면 배포가 시작되지 않습니다. 백업 없이 운영하는 상태를 설정 단계에서 막습니다.
- 백업이 성공했을 때만 Redis 키 `reborn:beat:pg-backup`에 시각을 씁니다. 관리자 화면 연동 진단의 "DB 백업" 항목이 이 시각으로 판정합니다.
  25시간이 지나면 실패로 바뀝니다. 주기가 12시간이므로 두 번 연속 놓쳐야 실패가 됩니다.
- 1KiB 미만의 덤프는 올리지 않고 실패로 끝냅니다. 빈 백업을 성공으로 기록하면 복원할 때가 되어서야 백업이 없다는 사실을 알게 됩니다.
- 보관 기간(30일)은 파일 이름의 날짜로 자릅니다. 저장소의 LastModified는 재업로드나 복사로 바뀝니다.
  날짜 계산은 epoch 산술로 합니다. `date -d "30 days ago"`는 GNU 전용이라 이미지의 busybox `date`에서 실패합니다.
- 백업 버킷(`BACKUP_BUCKET`)은 산출물 버킷과 다른 버킷을 씁니다. 두 버킷은 라이프사이클 정책이 다르고, 산출물 버킷을 지우는 실수가 백업까지 지우면 안 됩니다.
- 백업 키는 env(`BACKUP_ACCESS_KEY`·`BACKUP_SECRET_KEY`)로 받습니다. 앱은 저장소 키를 DB의 자격증명 연결에서 읽지만, 백업 컨테이너는 DB 없이 실행되어 그 연결을 읽지 못합니다.
  키가 비어 있으면 `backup.sh`가 바로 멈춥니다. 저장소 주소는 NCP 공용 주소(`https://kr.object.ncloudstorage.com`)로 고정합니다.
- 실제 복원은 public 스키마를 지우고 다시 만든 뒤 붓습니다. `pg_restore --clean`은 덤프에 있는 객체만 지우므로, 백업 뒤에 생긴 테이블이나 컬럼이 남아 마이그레이션 장부와 실제 스키마가 어긋납니다.
  지우기와 붓기는 한 트랜잭션이라 첫 오류에서 전부 되돌아가고 원래 DB가 남습니다. `pg_restore --single-transaction`만 쓰면 스키마 삭제가 따로 커밋되어, 붓다가 실패하면 빈 DB가 남습니다.
  운영 DB에 다른 연결(앱, 워커)이 있으면 시작하지 않습니다. 복원 중에 앱이 옛 행을 쓰거나 스키마 삭제가 락을 기다리며 멈추기 때문입니다.
- 백업 대상은 Postgres뿐입니다. 영상, 클립, 납품 패키지 같은 파일은 Object Storage 버킷에 있고 이 백업에 들어가지 않습니다. 버킷의 보호(버전 관리, 복제)는 저장소 쪽에서 정합니다.
- 백업 파일 이름의 시각(`rebornstudio-20261014T030000Z.dump.gz`)은 UTC입니다. 컨테이너 로그의 시각도 UTC입니다.
- 서버 `.env`의 `CREDENTIALS_KEY`가 없으면 복원한 DB의 자격증명 연결(저장소, 분석, 품질검수 모델 키)을 복호화할 수 없습니다. `.env`는 백업에 들어가지 않으므로 서버 밖 안전한 곳에 따로 보관합니다.
- aws-cli v2.23 이후는 무결성 체크섬을 기본으로 켜는데, NCP Object Storage가 이 체크섬을 거부합니다. 오류가 `AccessDenied`로 나타나 권한 문제로 오인하기 쉽습니다.
  그래서 이미지에 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`를 설정합니다. boto3를 쓰는 앱은 정상인데 이 컨테이너만 실패하면 이 설정부터 확인합니다.

## 사용법

```bash
# 백업 목록
infra/deploy/dc.sh run --rm pg-backup restore.sh --list

# 검증 복원. 별도 DB에 붓고 운영과 행 수를 비교합니다
infra/deploy/dc.sh run --rm pg-backup restore.sh latest

```

### 복원 절차

운영 DB를 백업 시점으로 되돌리는 순서입니다. 백업 시점 뒤의 변경은 사라집니다.

```bash
# 1) 앱과 워커를 내립니다. DB에 연결이 남아 있으면 restore.sh가 시작하지 않습니다
infra/deploy/dc.sh stop web api worker worker-qc worker-gpu

# 2) 복원합니다. 확인 문구(덮어쓴다)를 입력해야 진행됩니다
infra/deploy/dc.sh run --rm pg-backup restore.sh <키> --into-production

# 3) 지금 이미지의 마이그레이션을 적용합니다. 백업이 이전 리비전이면 여기서 지금 리비전까지 올라갑니다
infra/deploy/dc.sh --profile ops run --rm migrate

# 4) 앱과 워커를 올립니다
infra/deploy/dc.sh up -d
```

- 백업이 지금 이미지보다 새 리비전이면 3에서 실패합니다. 그 백업을 만든 버전의 이미지로 배포한 뒤 복원합니다.
- 복원이 실패하면 아무것도 바뀌지 않습니다. 원인을 고친 뒤 2부터 다시 합니다.

검증 복원은 분기마다 한 번 실행합니다. 복원이 되는지는 실제로 복원해 봐야 확인할 수 있습니다.

## 의존성

- 실행 환경: `pg-backup` 서비스. 운영은 늘 뜨고, 개발은 루트 `.env`의 `BACKUP_BUCKET`에 값이 있을 때만 뜹니다(`backup` 프로필, `infra/deploy/profiles.sh`).
- 환경변수: `PGPASSWORD`, `BACKUP_BUCKET`, `AWS_ACCESS_KEY_ID`·`AWS_SECRET_ACCESS_KEY`(compose가 `BACKUP_ACCESS_KEY`·`BACKUP_SECRET_KEY`에서 넘깁니다). `BACKUP_PREFIX`·`RETENTION_DAYS`·`BACKUP_INTERVAL_SECONDS`는 운영 compose가 고정값으로 넘깁니다
- 하트비트: Redis(`REDIS_HOST`, 기본 `redis`)

## 남은 일

- 검증 복원을 주기 작업으로 돌리지 않습니다. 분기마다 사람이 실행합니다.
