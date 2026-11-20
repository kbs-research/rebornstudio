# deploy

## 목적

운영 서버(NCP 서버 한 대)에 배포하는 스크립트와 절차를 둡니다. 배포는 서버에서 실행하는 `deploy.sh`가 합니다.
서버에서 사람이 `deploy.sh`를 부릅니다.
레지스트리 없이 서버에서 소스로 빌드해 설치하는 절차는 [`docs/install.md`](../../docs/install.md)에 있습니다(`build-images.sh` → `DEPLOY_PULL=false deploy.sh`).
롤백할 때는 이전 커밋의 이미지 태그로 같은 스크립트를 다시 부릅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `deploy.sh` | 서버에서 실행하는 배포 절차. 이미지 당기기 → DB 백업 → 마이그레이션 → 기동 → 헬스 확인 → 이미지 정리. `DEPLOY_PULL=false`이면 당기지 않고 로컬 이미지가 모두 있는지 확인합니다. `SKIP_MIGRATE=1`이면 백업과 마이그레이션을 건너뜁니다 |
| `build-images.sh` | 서버에서 저장소 소스로 운영 이미지를 빌드해 `.env`의 `IMAGE_REGISTRY` 이름으로 태그합니다. 레지스트리 없이 설치할 때 씁니다 |
| `dc.sh` | 서버에서 compose를 손으로 부를 때의 래퍼. 프로젝트 이름·compose 파일 둘·env 파일·`IMAGE_TAG`(`.last-deploy`)·프로필을 채웁니다 |
| `profiles.sh` | `.env`의 값으로 켤 compose 프로필을 계산합니다(`compose_profiles`). `deploy.sh`·`dc.sh`·루트 `Makefile`이 source합니다 |
| `env.example` | 서버 `/opt/rebornstudio/.env`의 예시. 자원 값은 vCPU 8·메모리 32GB·디스크 150GB 서버를 예로 채웠습니다. 다른 서버는 `docs/install.md`의 산식으로 다시 정합니다 |

| 대상 | 값 |
|---|---|
| 서버 | NCP 서버 1대(GPU 서버 권장). 사양 산식은 `docs/install.md` |
| 레지스트리 | NCP Container Registry. 주소는 서버 `.env`와 GitHub Variable의 `IMAGE_REGISTRY`. 서버에서 빌드하면 레지스트리 없이 씁니다 |
| 저장소·분석 | NCP Object Storage · NCP Media Intelligence(같은 클라우드) |

## 설계 이유

### 배포 경로

```
레지스트리  이미지 빌드 → 레지스트리 push → 서버에서 deploy.sh
서버 빌드   infra/deploy/build-images.sh → DEPLOY_PULL=false deploy.sh   ← 레지스트리 없이 설치할 때
```

- 서버 빌드는 저장소를 서버에 받아 `build-images.sh`로 이미지를 만들고 `DEPLOY_PULL=false`로 `deploy.sh`를 부릅니다. 검사는 돌리지 않으므로 검사를 통과한 커밋을 받습니다.

### `deploy.sh`의 순서

- 이미지를 먼저 당깁니다. 당기지 못하면 아무것도 바뀌지 않은 채 끝납니다. 마이그레이션부터 하면 스키마만 앞서고 앱은 이전 버전으로 남습니다.
- 적용할 마이그레이션이 있으면 바로 앞에서 DB를 백업합니다(`pg-backup`의 `backup.sh`를 한 번 실행). 백업이 실패하면 아무것도 바꾸지 않고 멈춥니다.
  마이그레이션은 되돌리지 않으므로 잘못된 마이그레이션에서 돌아갈 곳은 이 백업뿐입니다. 새 설치(장부가 없는 DB)와 적용할 것이 없는 배포는 백업하지 않습니다.
- 마이그레이션은 기동보다 먼저 합니다. 마이그레이션은 언제나 이전 코드와 호환되어야 하므로 앱보다 먼저 실행해도 안전합니다.
- 마이그레이션의 문장마다 락 대기 상한(5초, `alembic/env.py`)이 걸립니다. 이전 앱의 긴 트랜잭션 뒤에서 락을 기다리는 동안 그 테이블을 읽는 요청이 모두 멈추기 때문입니다.
  상한을 넘기면 마이그레이션이 실패하고 배포가 멈춥니다. 바뀐 것은 없으므로 다시 배포합니다. 되풀이되면 워커를 잠시 내리고 배포합니다.
- DB의 리비전을 이미지가 모르면 마이그레이션을 건너뜁니다. 새 마이그레이션이 들어간 배포를 직전 태그로 되돌리는 롤백이 이 경우입니다. 직전 이미지에는 새 리비전 파일이 없어 `alembic upgrade head`가 실패하기 때문입니다.
  판단이 맞지 않으면 `SKIP_MIGRATE=1`로 백업과 마이그레이션을 건너뜁니다.
- 이미지 정리는 마지막에 합니다. 헬스 확인이 실패하면 직전 이미지가 로컬에 남아 있어야 바로 롤백할 수 있습니다.
  성공한 뒤에는 지금 태그와 직전 태그를 뺀 `rebornstudio-*` 이미지와 dangling 이미지를 지우고, 일주일 넘게 쓰지 않은 빌드 캐시를 지웁니다. `docker image prune -a`는 직전 태그까지 지우므로 쓰지 않습니다.
- 헬스는 컨테이너 안에서 확인합니다(`HEALTH_TIMEOUT`, 기본 180초). web은 `/healthz`, api는 `/readyz`(DB·Redis 연결)를 요청하고, 모든 서비스가 돌고 있으며 헬스체크가 있는 서비스는 healthy인지 봅니다. 재시작을 되풀이하는 워커는 web·api가 멀쩡해도 작업을 처리하지 못하기 때문입니다.
  호스트에서 80 포트를 요청하면 앞단 로드밸런서와 방화벽까지 함께 검사하게 됩니다.
  실패하면 실패한 프로브, 준비되지 않은 서비스, 설정 검증 실패 문구, 컨테이너 상태를 출력합니다. api 로그 본문은 출력하지 않습니다. 첫 기동의 셋업 토큰이 그 로그에 있기 때문입니다.
- `.last-deploy`는 헬스 확인이 성공한 뒤에만 갱신됩니다. 그래서 배포가 실패한 뒤에도 이 값은 마지막으로 정상이던 태그입니다.
- 서버 `.env`가 없으면 첫 단계 전에 멈춥니다. `.env` 없이 마이그레이션이 실행되면 스키마만 앞서 나갑니다.

### 같은 태그의 이미지 여섯 개

`deploy.sh`의 pull은 compose에 적힌 이미지를 모두 당깁니다. 한 태그(`sha-<커밋 전체 sha>`)로 아래 이미지가 레지스트리에 있어야 1단계를 넘습니다.
프로필로만 뜨는 서비스의 이미지도 같이 올립니다. 그 프로필을 켜는 날 이미지가 없으면 어느 이미지가 빠졌는지 찾기 어렵습니다.

| 이미지 | Dockerfile | 쓰는 서비스 |
|---|---|---|
| `rebornstudio-api` | `infra/docker/api.Dockerfile` | api · migrate |
| `rebornstudio-web` | `infra/docker/web.Dockerfile` | web |
| `rebornstudio-worker-io` | `infra/docker/worker-io.Dockerfile` | worker · worker-qc |
| `rebornstudio-worker-jobs` | `infra/docker/worker-jobs.Dockerfile` | worker-gpu(GPU가 없는 서버) |
| `rebornstudio-worker-gpu` | `infra/docker/worker-gpu.Dockerfile` | worker-gpu(`GPU_ENABLED=true`, `compose.gpu.yaml`) |
| `rebornstudio-pgbackup` | `infra/docker/pgbackup.Dockerfile` | pg-backup |

### `.env`가 정하는 형상

| `.env` | 바뀌는 것 |
|---|---|
| `GPU_ENABLED=true` | `compose.gpu.yaml`을 얹어 `worker-gpu`가 GPU 이미지와 GPU 한 장을 씁니다 |
| `BACKUP_BUCKET`·`BACKUP_ACCESS_KEY`·`BACKUP_SECRET_KEY` | 운영 필수입니다. 하나라도 비면 `deploy.sh`가 pull 전에 멈춥니다 |

규칙은 `profiles.sh` 한 곳에 있습니다. 배포와 `dc.sh ps`가 같은 규칙을 써야 `ps`가 실제로 배포된 서비스 목록을 보여 줍니다.
`ops`(migrate)는 `deploy.sh`가 `run`으로만 부릅니다.
`pg-backup`과 `worker-gpu`는 프로필 없이 늘 뜹니다.

## 서버 상태 보기

`docker compose ps`를 그냥 부르면 `IMAGE_TAG`가 없어 보간 오류로 끝납니다. `IMAGE_TAG`는 `.env`가 아니라 배포 스크립트가 넘기는 값입니다. 그래서 `dc.sh`를 씁니다.

```bash
sudo -u deploy /opt/rebornstudio/infra/deploy/dc.sh ps
sudo -u deploy /opt/rebornstudio/infra/deploy/dc.sh logs -f api
sudo -u deploy /opt/rebornstudio/infra/deploy/dc.sh restart worker-gpu
```

## 최고관리자 비밀번호 분실

재설정 링크는 최고관리자가 사용자 관리 화면에서 발급합니다. 최고관리자 본인이 비밀번호를 잃었고 다른 최고관리자도 없으면 서버에서 링크를 발급합니다.

```bash
sudo -u deploy /opt/rebornstudio/infra/deploy/dc.sh exec api uv run --package api-gateway \
    python -m api_gateway.cli.recover_admin --email <관리자 이메일> --base-url https://<서비스 주소>
```

- 최고관리자가 한 명이면 `--email`을 생략할 수 있습니다. 여럿이면 명령이 목록을 보이고 멈춥니다.
- 링크는 30분 동안 한 번만 쓸 수 있고, 이 명령의 출력에만 나옵니다. 잃어버리면 다시 실행합니다. 새로 발급하면 앞서 발급한 링크는 무효가 됩니다.
- 발급은 감사 기록(`user.password_reset_issued`, `via: server_command`)에 남습니다.
- 일반 사용자에게는 발급하지 않습니다. 일반 사용자의 링크는 최고관리자가 화면에서 발급합니다.

## 자격증명 암호화 키 교체

`CREDENTIALS_KEY`가 유출됐거나 정해 둔 주기가 되면 키를 바꿉니다. 이 값만 바꾸면 DB에 저장된 연결 시크릿(저장소, MI, 품질검수 모델 키)을 읽지 못하므로, 새 키와 이전 키를 함께 둔 채 저장된 시크릿을 새 키로 다시 암호화합니다.
재암호화는 api 이미지에 든 명령(`api_gateway.cli.rotate_credentials_key`)이 합니다. 기본은 건수만 세는 드라이런이고, `--apply`는 바꿀 행과 감사 기록을 한 트랜잭션으로 커밋하므로 중간에 실패하면 아무것도 바뀌지 않습니다. 다시 실행해도 새 키 행은 건너뜁니다. 출력은 건수 JSON 한 줄이고 키와 시크릿은 출력하지 않습니다.

```bash
cd /opt/rebornstudio
openssl rand -hex 32                     # 새 키. 쉼표가 없는 값을 씁니다
vi .env                                  # CREDENTIALS_KEY_PREVIOUS=<지금 키>, CREDENTIALS_KEY=<새 키>
infra/deploy/dc.sh up -d --force-recreate api worker worker-qc
infra/deploy/dc.sh exec -T api uv run --package api-gateway python -m api_gateway.cli.rotate_credentials_key
infra/deploy/dc.sh exec -T api uv run --package api-gateway python -m api_gateway.cli.rotate_credentials_key --apply
infra/deploy/dc.sh exec -T api uv run --package api-gateway python -m api_gateway.cli.rotate_credentials_key
```

- 첫 드라이런의 `unreadable`이 0인지 봅니다. 0이 아니면 `CREDENTIALS_KEY_PREVIOUS`에 빠진 키가 있습니다. 적용해도 그 행은 바뀌지 않고 명령이 종료 코드 1로 끝납니다.
- 적용 결과의 `rotated`가 이전 키 행 수이고, 실행 중 화면에서 시크릿을 바꾼 행은 `changed_during_run`으로 따로 셉니다. 그 행은 화면이 이미 새 키로 저장했습니다.
- 마지막 드라이런의 `previous`가 0이면 `.env`에서 `CREDENTIALS_KEY_PREVIOUS`를 비우고 같은 `up -d --force-recreate`를 한 번 더 합니다.
- 처리 기록(`domain_events`)에 `system.credentials_rekeyed`가 건수와 함께 남습니다. 자격증명 연결 화면에서 각 연결의 점검 상태가 정상인지 확인합니다.
- 서버 밖에 보관하는 `.env` 사본도 새 키로 바꿉니다. 교체 전에 만든 DB 백업에는 이전 키 암호문이 들어 있으므로 이전 키는 그 백업의 보관 기간(30일) 동안 따로 보관합니다. 그 백업을 복원하면 이전 키를 `CREDENTIALS_KEY_PREVIOUS`에 넣고 같은 명령을 다시 돌립니다.
- 되돌리려면 두 값을 맞바꾸고(`CREDENTIALS_KEY=<이전 키>`, `CREDENTIALS_KEY_PREVIOUS=<새 키>`) 같은 절차를 따릅니다. worker-gpu는 DB와 이 키를 쓰지 않으므로 다시 만들지 않습니다.

## 롤백

서버에서 직전 정상 태그로 다시 배포합니다. 헬스 확인이 실패한 배포는 `.last-deploy`를 바꾸지 않으므로 그 값이 직전 정상 태그입니다.

```bash
IMAGE_TAG=$(cat /opt/rebornstudio/.last-deploy) /opt/rebornstudio/infra/deploy/deploy.sh
```

마이그레이션은 되돌리지 않습니다. expand-contract 규칙에 따라 새 스키마는 이전 코드와 호환되므로 이미지만 되돌립니다.
DB가 직전 이미지보다 앞선 리비전에 있으면 `deploy.sh`가 그 상태를 알아보고 마이그레이션을 건너뜁니다.
contract(컬럼 삭제) 마이그레이션이 들어간 배포에는 이 전제가 성립하지 않습니다. 그때는 롤백 대신 수정 배포를 하거나, 배포 직전 백업으로 복원합니다(`infra/backup/README.md`).

## 서버 준비 (처음 한 번)

서버에서 실행할 명령은 아래와 같습니다.

```bash
# 0) docker를 공식 apt 저장소로 설치합니다(GPG 서명 검증, 편의 스크립트는 쓰지 않습니다)
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker compose version   # v2여야 합니다. compose 파일이 profiles 등 v2 문법을 씁니다

# 1) 배포 계정과 디렉터리(기존 계정을 쓰면 건너뜁니다)
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -o deploy -g deploy /opt/rebornstudio

# 2) 배포 계정으로 접속할 공개키 등록
sudo -u deploy install -d -m 700 /home/deploy/.ssh
sudo -u deploy tee /home/deploy/.ssh/authorized_keys <<< '<공개키 한 줄>'
sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys

# 3) 새 계정으로 로그인되는지 먼저 확인합니다
ssh -i <개인키> deploy@<서버-IP> 'docker ps >/dev/null && echo OK'

# 4) SSH 설정을 강화합니다(3이 성공한 뒤에만)
sudo tee /etc/ssh/sshd_config.d/99-harden.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
sudo apt-get install -y fail2ban

# 5) 레지스트리 로그인(NCP 서브계정 키)
sudo -u deploy docker login <레지스트리 이름>.kr.ncr.ntruss.com -u <ACCESS_KEY>

# 6) 서버 .env. 배포 스크립트는 이 파일을 만들거나 고치지 않습니다
sudo -u deploy cp /opt/rebornstudio/infra/deploy/env.example /opt/rebornstudio/.env
sudo -u deploy vi /opt/rebornstudio/.env      # 빈 값(레지스트리·시크릿·백업 버킷)을 채웁니다
sudo -u deploy chmod 600 /opt/rebornstudio/.env
```

- 4를 3보다 먼저 하지 않습니다. NCP 서버는 보통 인증키로 root 로그인을 하는데, `PermitRootLogin no`가 그 경로를 끊습니다.
- docker 그룹은 호스트 파일시스템을 마운트할 수 있어 사실상 root 권한입니다. 배포 계정을 따로 두면 권한은 줄지 않지만, 그 계정에 등록한 키를 사람 키와 따로 회수할 수 있습니다.
- 최신 LTS의 코드명이 Docker 저장소에 아직 없으면 `apt-get update`가 404를 냅니다. 그때는 직전 LTS 코드명을 넣습니다.
- 백업 버킷은 산출물 버킷과 다른 버킷을 씁니다. 버킷과 그 버킷 전용 키를 채우기 전에는 배포가 시작되지 않습니다. 백업은 12시간마다 돌고 30일치를 보관하며, 관리자 화면 연동 진단의 "DB 백업" 항목이 마지막 성공 시각을 보입니다.

## 의존성

- 이 폴더가 부르는 것: `infra/compose/compose.yaml`·`compose.prod.yaml`, 서버의 `/opt/rebornstudio/.env`·`.last-deploy`, docker compose v2
- 부르는 쪽: 서버의 운영자, 루트 `Makefile`(`profiles.sh`)

## 남은 일

- 지표 수집과 알림이 없습니다.
- TLS 종료가 없습니다. web은 80 포트만 엽니다. 앞단 구성과 위험은 [`docs/install.md`](../../docs/install.md)의 "TLS" 절에 있습니다.
