# 설치와 업그레이드

RebornStudio를 서버 한 대에 설치하고 새 버전으로 올리는 절차입니다.
서버에서 저장소 소스로 이미지를 빌드해 실행하므로 이미지 레지스트리가 없어도 됩니다.
레지스트리를 거쳐 배포하는 경로와 배포 스크립트의 동작은 [`infra/deploy/README.md`](../infra/deploy/README.md)에 있습니다.

## 구성

한 서버에서 컨테이너 여덟 개가 돕니다.

| 서비스 | 하는 일 |
|---|---|
| `web` | 화면(nginx)과 `/api` 프록시. 외부에 여는 포트는 80 하나입니다 |
| `api` | 서버 API |
| `postgres`, `redis` | 메타데이터 DB, 작업 큐와 세션 |
| `worker` | 짧은 작업(분석 결과 회수, 썸네일, probe, 회수·내보내기)과 주기 작업 |
| `worker-qc` | 캡션 품질검수(외부 모델 호출) |
| `worker-gpu` | 긴 작업. 재생본·분석본 변환, 장면 분할, 비식별화 |
| `pg-backup` | DB 백업. 12시간마다 Object Storage에 올리고 30일치를 보관합니다 |

영상 파일은 서버 디스크가 아니라 NCP Object Storage에 있고, 장면 분석은 NAVER Media Intelligence(MI)가 합니다.
캡션 품질검수 모델은 외부 API(OpenAI 호환, Anthropic, Bedrock 등)에 연결합니다.

GPU는 비식별화의 얼굴 검출(ONNX Runtime CUDA)과 영상 인코딩(NVENC)에 씁니다. 재생본·분석본 변환과 비식별화 결과 영상은 NVENC로 인코딩되는 서버면 NVENC로, 아니면 libx264(CPU)로 인코딩합니다.
GPU가 없는 서버에서도 같은 구성으로 돌고, 그때는 검출과 인코딩이 모두 CPU로 돕니다(느림).

## 서버 사양

수치는 처리할 양과 기간에 따라 정합니다. 아래 산식에 값을 넣어 고릅니다.

### CPU와 메모리

| 항목 | 산식 | 예 |
|---|---|---|
| vCPU | 긴 작업 워커 몫(`GPU_WORKER_CPUS`) + 나머지 서비스 몫(4) | 4 + 4 = 8 |
| 변환 1건의 스레드(`TRANSCODE_THREADS`) | `GPU_WORKER_CPUS ÷ GPU_WORKER_CONCURRENCY` | 4 ÷ 2 = 2 |
| 메모리 | postgres 공유 버퍼(`PG_SHARED_BUFFERS`) + Redis 상한(`REDIS_MAXMEMORY`) + 서비스·워커 몫 | 2GB + 2GB + 여유, 32GB 서버 |

긴 작업 워커에는 CPU 상한을 둡니다. 변환이 모든 코어를 쓰면 postgres 응답이 늦어지고 화면 전체가 느려집니다.

### GPU

- NVENC가 있는 NVIDIA GPU를 씁니다(T4, L4, A10, L40S 등). A100, H100처럼 NVENC가 없는 GPU에서는 검출만 GPU로 돌고 인코딩은 libx264로 넘어갑니다.
- 드라이버는 CUDA 12.8을 지원하는 버전(570 이상)이어야 합니다. GPU 이미지가 CUDA 12.8·cuDNN 9 런타임 위에 있습니다.
- GPU를 쓰는 잡은 긴 작업 워커의 동시 실행 수(`GPU_WORKER_CONCURRENCY`, 기본 2)만큼입니다. 동시 NVENC 세션도 그 수를 넘지 않습니다. 검출 모델(YuNet)은 1MB 미만이라 작은 GPU로도 충분합니다.
- 인코더를 고정하려면 `.env`에 `TRANSCODE_ENCODER`(변환)와 `ESSENTIAL_DEIDENT_ENCODER`(비식별화)를 `nvenc`나 `libx264`로 넣습니다. 비우면 서버에서 NVENC로 인코딩되는지 보고 정합니다.

### 디스크

| 항목 | 산식 | 예 |
|---|---|---|
| 이미지 | 세 세트(지금, 직전, 새로 받는 세트) × 한 세트 크기. `deploy.sh`가 배포에 성공하면 지금 태그와 직전 태그만 남깁니다 | 3 × 약 8.6GB(GPU 이미지 약 7GB 포함) ≈ 26GB |
| 빌드 캐시 | 서버에서 빌드할 때 생깁니다. `deploy.sh`가 일주일 넘게 쓰지 않은 캐시를 지웁니다 | `docker system df`로 확인 |
| 작업 임시 파일 | 동시 실행 수 × (원본 1편 + 산출물) | 1시간 720p 원본 기준 변환 산출물 약 1GB, 2건 동시 |
| 컨테이너 로그 | 50MB × 5개 × 서비스 수 | 약 2GB |
| postgres | 메타데이터만 담습니다 | 수백 MB 이하 |

100GB 이상을 권장합니다. 영상 파일은 Object Storage에 있으므로 서버 디스크는 처리량과 관계없이 거의 일정합니다.

## 처리 한계 산식

서버가 하루에 처리하는 원본 길이는 변환과 비식별화 속도로 정해집니다. 분석(MI)은 외부에서 돌고, 라벨링·검수는 사람이 하므로 이 산식에 들어가지 않습니다.

| 기호 | 뜻 | 기본값 |
|---|---|---|
| `t_v` | 원본 1시간을 변환(재생본·분석본·장면 분할)하는 데 걸리는 시간 | 서버에서 잽니다 |
| `t_d` | 원본 1시간을 비식별화하는 데 걸리는 시간 | 서버에서 잽니다 |
| `c` | 긴 작업 워커의 동시 실행 수(`GPU_WORKER_CONCURRENCY`) | 2 |
| `e` | 변환 투입 상한(`TRANSCODE_MAX_INFLIGHT`) | 2 |
| `d` | 비식별화 투입 상한(`DEIDENT_MAX_INFLIGHT`) | 1 |

```
비식별화를 켠 경우   하루 처리량 H = min(24·d / t_d, 24·c / (t_v + t_d))   [원본 시간/일]
비식별화를 끈 경우   하루 처리량 H = 24·min(c, e) / t_v
서버 처리 일수 = 전체 원본 시간 ÷ H
```

예를 들어 `t_v = 0.5`, `t_d = 0.4`이면(측정값이 아니라 산식을 보이려고 넣은 값) H = min(24 / 0.4, 48 / 0.9) = min(60, 53) ≈ 53시간/일이고, 원본 1,000시간은 약 19일이 걸립니다.

`t_v`와 `t_d`는 설치한 서버에서 1시간 길이 표본 한 편을 처리해 잽니다. 잡이 끝날 때마다 워커 로그에 걸린 시간이 남습니다.

```bash
infra/deploy/dc.sh logs worker-gpu | grep -E "(transcode_one|scene_split_one|deident_one).*succeeded in"
```

`t_v`는 `transcode_one`과 `scene_split_one`의 시간을 더한 값이고, `t_d`는 `deident_one`의 시간입니다. 둘 다 원본 길이(시간)로 나눕니다.

## 설치 전 준비

### 서버

Ubuntu 22.04 또는 24.04 서버를 기준으로 씁니다.

```bash
# 1) NVIDIA 드라이버(GPU 서버만). 설치 뒤 재부팅합니다
sudo apt-get update
sudo ubuntu-drivers install
sudo reboot
nvidia-smi                      # GPU 이름과 드라이버 버전(570 이상)이 나오면 됩니다

# 2) Docker(공식 apt 저장소)
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker compose version          # v2여야 합니다

# 3) NVIDIA Container Toolkit(GPU 서버만)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi   # 컨테이너 안에서 GPU가 보이면 됩니다

# 4) 실행 계정과 디렉터리
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -o deploy -g deploy /opt/rebornstudio
```

### 외부 서비스

| 준비할 것 | 쓰는 곳 |
|---|---|
| NCP Object Storage 버킷 둘(산출물용, 백업용)과 접근 키 | 산출물 버킷은 화면의 «자격증명 연결»에 등록합니다. 백업 버킷과 그 버킷 전용 키는 서버 `.env`에 넣습니다 |
| NAVER Media Intelligence 워크스페이스, 분석 프로젝트, 키 | 워크스페이스와 분석 프로젝트는 NCP 콘솔에서 미리 만듭니다(아래 절). 키는 화면의 «자격증명 연결»에 등록하고, 프로젝트마다 «분석 연동»에서 워크스페이스와 분석 프로젝트를 고릅니다 |
| 캡션 품질검수 모델 API 키 | 화면의 «자격증명 연결»에 등록하고 프로젝트 설정에서 고릅니다. 품질검수를 쓰지 않으면 필요 없습니다 |

산출물 버킷과 백업 버킷은 따로 둡니다. 산출물 버킷을 지우는 실수가 백업까지 지우지 않게 하기 위해서입니다.
브라우저가 영상을 산출물 버킷에 직접 올리므로 산출물 버킷에는 화면 주소를 출처로, `PUT`을 방법으로 허용하는 CORS 규칙이 필요합니다. 출처는 사용자가 실제로 여는 주소(TLS를 앞에 두면 `https://` 주소)입니다. 프로젝트의 저장소 점검이 이 규칙을 확인합니다.

출처에 모든 주소(`*`)를 넣지 않습니다. `*`이면 업로드는 되지만 다른 사이트의 화면도 유출된 업로드 주소로 버킷에 올릴 수 있어, 저장소 점검의 «브라우저 업로드 허용» 항목이 경고로 바뀝니다. 경고는 저장과 영상 등록을 막지 않습니다. 아래처럼 서비스의 웹 주소 하나만 허용합니다.

```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://studio.example.com"],
      "AllowedMethods": ["GET", "HEAD", "PUT"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3000
    }
  ]
}
```

`AllowedOrigins`를 실제 주소로 바꾼 뒤 이 파일을 `cors.json`으로 저장하고, S3 호환 CLI로 적용합니다. 적용한 뒤 프로젝트 설정 «분석 연동»의 저장소 대상 카드에서 `점검`을 눌러 «브라우저 업로드 허용»이 정상인지 봅니다.

```bash
aws --endpoint-url https://kr.object.ncloudstorage.com s3api put-bucket-cors \
  --bucket <산출물 버킷> --cors-configuration file://cors.json
```

### MI 워크스페이스와 분석 프로젝트

앱은 MI 계정에 워크스페이스나 분석 프로젝트를 만들지 않습니다. 운영자가 NCP 콘솔에서 미리 만들고, 앱에서는 만든 것을 고르기만 합니다.

1. NCP 콘솔의 Media Intelligence에서 워크스페이스를 만듭니다. 연동할 Object Storage 버킷으로 그 워크스페이스를 쓸 프로젝트의 산출물 버킷을 고릅니다. 계정마다 만들 수 있는 워크스페이스 수에 한도가 있으므로, 같은 버킷을 쓰는 프로젝트는 워크스페이스 하나를 같이 씁니다.
2. 그 워크스페이스 안에 분석 프로젝트를 만듭니다. 이름은 워크스페이스 안에서 겹치지 않아야 합니다. 앱의 프로젝트마다 분석 프로젝트를 하나씩 두면 분석 결과와 비용을 프로젝트별로 나눠 볼 수 있습니다.
3. «자격증명 연결»에 등록할 MI 키가 그 워크스페이스와 분석 프로젝트를 읽고 에셋을 등록할 수 있는지 확인합니다. 서브 계정 키를 쓰면 그 계정에 Media Intelligence 권한을 줍니다.
4. 앱의 프로젝트 설정 «분석 연동»에서 저장소 대상을 먼저 저장하고, «분석 대상»에서 연결, 워크스페이스, 분석 프로젝트를 고른 뒤 점검하고 저장합니다. 워크스페이스의 연동 버킷이 프로젝트 저장소와 다르면 등록과 업로드는 되고 분석 요청만 거절되므로, 저장 점검이 저장을 막습니다.
5. 같은 화면의 «연동 상태»에서 분석 워크스페이스, 연결된 저장소, 분석 프로젝트 세 항목이 정상인지 봅니다.

분석 대상은 첫 영상을 등록한 뒤에는 바꿀 수 없습니다. 영상을 등록하기 전에 위 순서를 끝냅니다.

### 버킷 수명 주기 규칙

산출물 버킷의 파일 정리는 앱이 직접 합니다. 버킷의 수명 주기 규칙은 아래 기준으로 둡니다.

- 앱이 쓰는 경로(`intake/`, `work/`, `transcoded/`, `clip-thumbs/`, `video-posters/`, `projects/`, `archive/`, `_diagnostics/`)에 만료 규칙을 두지 않습니다. 업로드한 원본은 `intake/`에 있다가 분석과 체크섬이 끝난 뒤 앱이 지우거나 보관 경로(`archive/`)로 옮깁니다. 분석을 요청하지 않은 영상의 원본은 그때까지 남아야 하므로, 만료 규칙이 먼저 지우면 아직 처리하지 않은 원본이 사라집니다.
- 변환·재생본·비식별화 워커가 결과를 보고하지 못하고 끝나면 그 시도의 파일이 `work/` 아래에 남습니다. 앱이 하루 한 번 이 파일을 지웁니다. 진행 중인 시도의 파일은 남기고, 잡 URL 유효 기간(기본 22시간)보다 오래된 파일만 지웁니다.
- 완료되지 않은 멀티파트 업로드를 정리하는 규칙(AbortIncompleteMultipartUpload)은 7일로 둡니다. 업로드 중에 브라우저를 닫으면 올라간 파트가 남고, 완료되지 않은 파트는 목록에 보이지 않지만 저장 용량으로 과금됩니다. 앱은 영상을 지울 때만 그 업로드를 중단합니다. 7일이 지난 업로드는 이어받을 수 없고 처음부터 다시 올립니다.
- 저장소가 그 규칙을 지원하지 않으면 남은 업로드를 주기적으로 확인하고 정리합니다(`aws s3api list-multipart-uploads`, `aws s3api abort-multipart-upload`).
- 백업 버킷은 `backup.sh`가 30일이 지난 백업을 지웁니다. 만료 규칙을 더 두려면 30일보다 길게 둡니다.

### 다른 설치본과 같은 버킷 쓰기

산출물 버킷을 다른 설치본(RebornStudio Pro 등)과 같이 써도 앱의 정리 작업은 그 설치본의 파일을 지우지 않습니다.

- `work/`를 하루 한 번 정리하는 작업은 이 서버 DB에 있는 영상(지운 영상 포함)의 파일만 지웁니다. 키의 영상 ID가 이 DB에 없으면 그 파일은 다른 설치본의 것으로 보고 남깁니다.
- 원본 정리, 분석본 정리, 변환·비식별화 완료 처리는 버킷 목록을 읽지 않고 이 DB의 영상 행에서 만든 키만 지웁니다. 버킷에서 등록한 파일은 지우지 않습니다.
- 버킷에서 영상을 등록할 때 앱 경로(`intake/`, `work/` 등) 아래 파일은 등록하지 않으므로, 다른 설치본의 산출물을 원본으로 가져오지 않습니다.

아래 두 경우는 같은 버킷을 쓸 때 직접 조율합니다.

- 버킷 수명 주기 규칙은 버킷 전체에 걸립니다. 위 기준으로 둔 규칙(완료되지 않은 멀티파트 업로드 7일 등)이 다른 설치본에도 적용되고, 다른 설치본이 둔 규칙도 이 서버의 파일에 적용됩니다. 두 설치본의 기준을 맞춰 규칙 하나로 둡니다.
- 한 DB를 다른 설치본으로 옮긴 경우 두 DB의 영상 ID가 같습니다. 두 서버가 같은 파일을 각자 자기 것으로 보고 정리하므로, 옮긴 뒤에는 옛 서버의 api와 워커를 다시 띄우지 않습니다.

## 설치

```bash
sudo -iu deploy
git clone <저장소 주소> /opt/rebornstudio
cd /opt/rebornstudio
git checkout <설치할 태그나 커밋>

cp infra/deploy/env.example .env
chmod 600 .env
vi .env
```

`.env`에서 채울 값은 아래와 같습니다. 키마다 뜻은 [`env-catalog.md`](env-catalog.md)에 있습니다.

| 키 | 값 |
|---|---|
| `IMAGE_REGISTRY` | 서버에서 빌드하면 `local` |
| `GPU_ENABLED` | GPU 서버면 `true` |
| `GPU_WORKER_CPUS`, `TRANSCODE_THREADS`, `PG_SHARED_BUFFERS`, `REDIS_MAXMEMORY` | 서버 사양 절의 산식으로 정한 값 |
| `POSTGRES_PASSWORD`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `CREDENTIALS_KEY` | 각각 `openssl rand -hex 32`로 만든 값. `CREDENTIALS_KEY`는 값만 바꾸면 저장된 연결 키를 읽지 못하므로, 바꿀 때는 `infra/deploy/README.md`의 «자격증명 암호화 키 교체» 절차를 따릅니다 |
| `BACKUP_BUCKET`, `BACKUP_ACCESS_KEY`, `BACKUP_SECRET_KEY` | 백업 버킷과 그 버킷 전용 키. 비어 있으면 배포가 시작되지 않습니다 |
| `ENVIRONMENT` | `prod` 그대로 둡니다 |

- `.env`는 서버 밖 안전한 곳(비밀값 보관소 등)에 따로 보관합니다. DB 백업에는 `.env`가 들어가지 않고, `CREDENTIALS_KEY`가 없으면 복원한 DB의 자격증명 연결(저장소, 분석, 품질검수 모델 키)을 복호화할 수 없습니다. 서버를 잃으면 백업이 있어도 연결을 모두 다시 등록해야 합니다.
- 운영 서버의 `ENVIRONMENT`를 `prod` 말고 다른 값으로 두지 않습니다. `local`과 `test`는 시작할 때의 비밀값 검사와 일부 진단을 끄고, `dev`는 API 문서 화면(`/docs`)을 열고 로그인 토큰을 직접 발급하는 개발 도구를 허용합니다.

```bash
infra/deploy/build-images.sh                       # 지금 커밋으로 이미지를 빌드합니다
IMAGE_TAG=sha-$(git rev-parse HEAD) DEPLOY_PULL=false infra/deploy/deploy.sh
```

`deploy.sh`는 마이그레이션, 기동, 헬스 확인을 차례로 합니다. 처음 설치하면 끝에 최초 설정 안내가 나옵니다.

1. 셋업 토큰을 api 로그에서 확인합니다: `infra/deploy/dc.sh logs api | grep '셋업 토큰'`
2. 브라우저로 서버 주소에 접속해 최초 설정 화면에 셋업 토큰과 최고관리자 정보를 입력해 최고관리자를 만듭니다.
3. 만든 계정으로 로그인합니다.
4. 관리자 화면의 «자격증명 연결»에 산출물 버킷, MI, 품질검수 모델을 등록합니다. 저장하면 같은 요청에서 실제로 접속해 점검하고, 정상인 연결은 프로젝트 설정에서 바로 고를 수 있습니다. 점검이 실패하면 연결은 저장된 채 실패 사유가 표시되므로 값을 고쳐 다시 저장합니다.
5. 플랫폼 개요에서 연동 상태를 확인하고 모든 항목이 정상인지 봅니다. 개요는 서버가 15분마다 도는 자동 점검의 결과를 보여 주므로, 기동 직후에는 첫 자동 점검이 끝날 때까지 기다립니다. «DB 백업»은 기동 후 첫 백업이 끝나면 정상으로 바뀝니다.

최초 설정 화면에는 연동 점검이 없습니다. 로그인 전 화면에 버킷 이름 같은 내부 정보를 보이지 않기 위해서입니다. 연동 상태는 최고관리자로 로그인한 뒤 플랫폼 개요에서 확인합니다.

## TLS

기본 구성은 TLS를 끝내는 구성 요소가 없고, web이 80 포트에서 평문 http만 엽니다. 운영하기 전에 앞단에 TLS를 둡니다.

TLS 없이 운영하면 아래 위험이 있습니다.

- 로그인 비밀번호, 세션 토큰(로그인 쿠키 포함), 관리자가 등록하는 저장소·분석·모델 접근 키가 네트워크에서 평문으로 오갑니다. 같은 네트워크의 누구나 읽을 수 있습니다.
- 가로챈 세션 토큰으로 그 사용자 권한(최고관리자 포함)을 그대로 쓸 수 있습니다.
- 중간에서 화면 파일과 응답을 바꿀 수 있습니다. 바뀐 화면은 사용자가 입력하는 값을 다른 곳으로 보낼 수 있습니다.

앞단은 둘 중 하나로 둡니다.

| 방식 | 구성 |
|---|---|
| 로드밸런서 | 클라우드 로드밸런서가 인증서로 443을 받고 서버의 80으로 넘깁니다. 서버의 80은 방화벽(ACG)에서 로드밸런서 대역에만 엽니다 |
| 호스트 프록시 | 같은 서버의 nginx나 Caddy가 443을 받고 `127.0.0.1:80`으로 넘깁니다. 서버의 80은 방화벽에서 외부에 닫습니다 |

- 앞단은 `X-Forwarded-Proto: https`와 `X-Forwarded-For`를 넘기고, `Host`는 사용자가 연 주소 그대로 넘깁니다.
- 서버 `.env`의 `WEB_TRUSTED_PROXIES`에 앞단 주소를 적습니다. 로드밸런서면 그 사설 대역(예: `10.0.1.0/24`), 같은 서버의 호스트 프록시면 `host`입니다. web은 이 주소에서 온 연결의 `X-Forwarded-Proto`와 `X-Forwarded-For`만 믿고, 그 밖의 연결이 보낸 두 헤더는 버립니다. 비우면 아무도 믿지 않습니다. 앞단 없이 80을 바로 연 서버에서 사용자가 `X-Forwarded-Proto: https`나 다른 주소를 꾸며 보내도 api에 닿지 않게 하려는 규칙입니다.
- api는 web에서 온 `X-Forwarded-Proto`를 요청 scheme으로 씁니다(uvicorn `--proxy-headers`). api가 믿는 대역 `FORWARDED_ALLOW_IPS`는 비워 두면 web과 api만 붙는 edge 네트워크의 서브넷이라, 같은 서버의 워커 컨테이너가 꾸민 헤더도 믿지 않습니다. scheme이 https일 때만 로그인 쿠키(`reborn_refresh`)에 `Secure`를 붙입니다. 평문 http로 여는 기본 구성에서도 새로고침 뒤 로그인이 유지되게 하려는 규칙입니다. 앞단이 `X-Forwarded-Proto`를 넘기지 않거나 `WEB_TRUSTED_PROXIES`에 앞단이 없으면 https로 접속해도 쿠키에 `Secure`가 붙지 않습니다.
- 토큰 갱신(refresh)과 로그아웃은 요청의 `Origin`이 서버 자신의 출처(요청 scheme과 `Host`)나 `CORS_ORIGINS`에 있어야 받습니다. 앞단이 `Host`를 바꾸거나 `X-Forwarded-Proto`를 빠뜨리면 같은 주소에서 연 화면도 403 `origin_not_allowed`를 받아, 새로고침할 때마다 로그인 화면으로 돌아갑니다. 앞단 설정을 고칠 수 없으면 `.env`의 `CORS_ORIGINS`에 사용자가 여는 주소(`https://…`)를 적습니다.
- web은 api에 넘기는 `X-Forwarded-For`를 사용자 주소 하나로 덮어씁니다. `WEB_TRUSTED_PROXIES`에 앞단이 있으면 앞단이 넘긴 사용자 주소이고, 없으면 web에 연결한 주소입니다. 앞단을 두고 이 값을 비우면 api가 모든 요청을 앞단 주소 하나에서 온 것으로 보아, 로그인 한도가 모든 사용자에게 하나로 걸리고 감사 기록의 접속 주소가 앞단 주소가 됩니다.
- 산출물 버킷 CORS의 출처와 `.env`의 `CORS_ORIGINS`를 `https://` 주소로 둡니다.
- 플랫폼 개요 연동 진단의 TLS 항목이 정상인지 봅니다. 이 항목은 api가 믿은 요청 scheme으로 판정하므로, 앞단이 있는데 주의로 보이면 `WEB_TRUSTED_PROXIES`를 확인합니다.

## 보안 설정

설치 뒤 바꾸지 않아도 적용되는 것과, 운영자가 값을 정하는 것을 함께 적습니다.

- web은 강제 CSP를 보냅니다. 스크립트는 같은 출처의 번들만 받고, 영상·썸네일·업로드는 저장소 출처로만 오갑니다. 저장소 출처는 서버 `.env`의 `CSP_STORAGE_ORIGINS`이고 비우면 NCP 객체 저장소(`https://kr.object.ncloudstorage.com`)입니다. 저장소 연결의 엔드포인트가 다르면 그 출처를 더하고, http 엔드포인트는 브라우저가 막으므로 https로 등록합니다. 근거는 `infra/docker/README.md`에 있습니다.
- 브라우저가 막은 자원은 `/api/csp-report`로 보고되어 api 로그에 `csp_violation blocked=<출처>` 줄로 남습니다. 썸네일이나 재생이 화면에서만 깨지면 이 줄부터 봅니다.
- HSTS는 보내지 않습니다. TLS 없이 80으로 여는 구성에서는 효과가 없습니다. 앞단에 TLS를 두면 앞단에서 HSTS를 켭니다.
- 로그인 세션의 refresh 토큰은 HttpOnly 쿠키(`reborn_refresh`, SameSite=Strict, `Path=/api/auth`)로만 오가고, access 토큰은 화면 메모리에만 있습니다. 화면 스크립트가 두 토큰을 브라우저 저장소에서 읽을 수 없으므로 XSS가 생겨도 토큰이 바로 나가지 않습니다. 쿠키는 평문 http에서도 동작하지만 그때는 다른 요청처럼 평문으로 오가므로, 실제 운영에는 앞단에 TLS를 둡니다(위 TLS 절).
- 로그인 세션은 로그인 시각부터 `JWT_SESSION_MAX_DAYS`(기본 14일)가 지나면 끝납니다. 토큰을 갱신해도 늘어나지 않습니다. 로그아웃은 같은 로그인에서 이어진 토큰을 모두 끊습니다.
- web 접근 로그는 쿼리 문자열과 Referer의 쿼리를 남기지 않습니다. 비밀번호 재설정 링크는 토큰을 주소 조각(`#token=`)에 실어 서버로 보내지 않습니다.
- 인증 경로의 한도는 한 사무실이 IP 하나를 같이 쓰는 조건을 기준으로 둡니다. 로그인·재설정 링크 확인·재설정 확정은 실패만 IP마다 분당 `RATE_LIMIT_AUTH_PER_MIN`(기본 10)건까지 경로별로 세고, 성공한 로그인은 세지 않습니다. 한도를 넘은 IP는 1분 창이 끝날 때까지 맞는 비밀번호도 거절합니다. 비밀번호 변경 실패는 사용자마다 셉니다. 토큰 갱신은 무효 토큰의 실패만 IP마다 분당 `RATE_LIMIT_REFRESH_PER_MIN`(기본 300)건, 로그인 세션 하나의 갱신은 분당 `RATE_LIMIT_REFRESH_SESSION_PER_MIN`(기본 60)건까지 받습니다. 모든 거절(429)에는 다시 시도할 때까지의 초(`Retry-After`)가 실립니다. 계정 단위로는 (이메일, IP) 연속 5회, 이메일 50회 실패에 15분 잠급니다.
- 자격증명 연결의 주소는 http(s)만 받고 루프백·링크 로컬(클라우드 메타데이터)·`localhost`·점 없는 이름(compose 서비스 이름)을 거절합니다. 사설 대역은 받습니다. 사내 모델 서버를 점 없는 이름으로 불러야 하면 `.env`의 `CONNECTION_HOST_ALLOWLIST`에 그 이름을 적습니다.
- 자격증명 연결의 엔드포인트·리전·API 주소를 바꾸려면 키를 함께 다시 입력합니다. 키 없이 주소만 바꾸면 저장된 키가 새 주소로 나가므로 서버가 저장을 거절합니다.
- 프로젝트 관리자는 저장소 연결이 허용한 저장소만 프로젝트 저장소로 고르고, 그 저장소 안의 경로를 입력해 영상을 등록합니다. 허용 범위는 연결의 저장소와 «허용 저장소» 목록의 합이고 최고관리자가 자격증명 연결 편집에서 넓힙니다. 프로젝트가 쓰는 저장소는 목록에서 뺄 수 없습니다. 업그레이드하면 기존 프로젝트가 쓰던 저장소가 목록에 자동으로 채워집니다.
- Bedrock 연결은 액세스 키와 비밀 키를 모두 넣어야 저장됩니다. 키 없이 저장하려 하면 422 `bedrock_keys_required`로 거절합니다. 두 키가 있어야 점검과 품질검수 호출을 합니다. 모델 ID는 직접 입력하고, 점검이 그 리전의 모델 목록에 있는지 확인합니다. 서버 인스턴스의 역할 자격증명으로 넘어가지 않습니다.
- 앱 컨테이너는 root로 돌지 않습니다. api·worker·worker-qc·worker-gpu는 `app`(uid·gid 10001), web nginx는 `nginx`(uid·gid 101), pg-backup은 `postgres`(uid·gid 70)입니다. 앱 소스는 root 소유라 실행 사용자가 고쳐 쓰지 못합니다. postgres와 redis는 공식 이미지가 시작할 때 각자의 사용자로 권한을 내립니다.
- `ENVIRONMENT`가 비어 있으면 `prod`로 봅니다. 운영 서버는 값을 빠뜨려도 시작할 때의 비밀값 검사를 받습니다.
- api는 web과 api만 붙는 edge 네트워크에서 온 `X-Forwarded-*`만 믿고, web은 `WEB_TRUSTED_PROXIES`에 적은 앞단의 `X-Forwarded-*`만 믿습니다(위 TLS 절).
- `MAIU_ENABLED=false`(가짜 MI)로는 운영 서버가 뜨지 않습니다. 시연 서버처럼 가짜 결과가 의도일 때만 `ALLOW_FAKE_MAIU=true`를 함께 줍니다.

## GPU와 NVENC 확인

```bash
# ONNX Runtime이 CUDA를 찾는지
infra/deploy/dc.sh exec worker-gpu /app/.venv/bin/python -c \
  "import onnxruntime as o; print(o.get_available_providers())"     # CUDAExecutionProvider가 있어야 합니다

# NVENC로 실제 인코딩되는지
infra/deploy/dc.sh exec worker-gpu ffmpeg -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1280x720:rate=30 -t 3 -c:v h264_nvenc -f null - && echo NVENC OK
```

첫 명령에 `CUDAExecutionProvider`가 없으면 드라이버 버전이나 Container Toolkit 설정을 확인합니다. 둘째 명령이 실패하면 GPU에 NVENC가 없거나 드라이버의 영상 라이브러리가 컨테이너에 들어오지 않은 것입니다.
NVENC로 인코딩되지 않으면 변환과 비식별화가 libx264로 인코딩합니다. 처리는 되지만 느려지고 CPU를 더 씁니다.
NVENC는 libx264와 같은 품질 값을 써도 파일이 조금 커지는 경향이 있습니다.

## 업그레이드

```bash
sudo -iu deploy
cd /opt/rebornstudio
git fetch
git checkout <새 태그나 커밋>
infra/deploy/build-images.sh
IMAGE_TAG=sha-$(git rev-parse HEAD) DEPLOY_PULL=false infra/deploy/deploy.sh
```

- `.env`는 업그레이드가 바꾸지 않습니다. 새 버전에서 키가 늘었으면 `infra/deploy/env.example`과 비교해 채웁니다. 필수 키가 비어 있으면 배포가 시작 단계에서 멈추고 빠진 키를 알립니다.
- 마이그레이션은 기동 전에 자동으로 돕니다. 적용할 마이그레이션이 있으면 `deploy.sh`가 바로 앞에서 DB를 백업하고, 백업이 실패하면 아무것도 바꾸지 않고 멈춥니다. 업그레이드 전에 «DB 백업» 진단이 정상인지 확인합니다.
- 마이그레이션은 락을 5초까지만 기다립니다. 이전 앱이 쓰는 테이블의 락을 얻지 못하면 마이그레이션이 바뀐 것 없이 실패하고 배포가 멈춥니다. 다시 배포하고, 되풀이되면 워커를 잠시 내린 뒤 배포합니다.
- 헬스 확인은 web, api(DB·Redis 연결), 모든 서비스의 컨테이너 상태를 봅니다. 실패하면 직전 버전으로 되돌립니다. 직전 이미지는 서버에 남아 있습니다.

### 되돌리기

```bash
git checkout <직전 태그나 커밋>
IMAGE_TAG=$(cat .last-deploy) DEPLOY_PULL=false infra/deploy/deploy.sh
```

`.last-deploy`는 헬스 확인까지 성공한 마지막 태그입니다. 새 마이그레이션이 들어간 버전에서 되돌리면 DB가 직전 이미지보다 앞선 리비전에 있으므로 `deploy.sh`가 그 상태를 알아보고 마이그레이션을 건너뜁니다. 판단이 맞지 않으면 `SKIP_MIGRATE=1`을 붙입니다.
컬럼을 지우는 마이그레이션이 들어간 버전은 이전 코드와 호환되지 않으므로 되돌리지 않고 수정 버전을 배포합니다.

## 운영 중 할 일

| 할 일 | 방법 |
|---|---|
| 상태 보기 | `infra/deploy/dc.sh ps`, `infra/deploy/dc.sh logs -f api`, 플랫폼 개요의 연동 진단 |
| 백업 복원 | [`infra/backup/README.md`](../infra/backup/README.md)의 «복원 절차». 검증 복원은 분기마다 한 번 합니다 |
| 최고관리자 비밀번호 분실 | [`infra/deploy/README.md`](../infra/deploy/README.md)의 «최고관리자 비밀번호 분실» |
| 자격증명 암호화 키 교체 | [`infra/deploy/README.md`](../infra/deploy/README.md)의 «자격증명 암호화 키 교체» |

백업과 로그에서 알아 둘 것은 아래와 같습니다.

- 백업 대상은 Postgres뿐입니다. 영상, 클립, 납품 패키지는 Object Storage 버킷에 있고 백업에 들어가지 않습니다. 버킷의 보호(버전 관리, 복제)는 저장소 쪽 설정으로 정합니다.
- 서버 `.env`도 백업에 들어가지 않습니다(위 «설치»).
- 백업 파일 이름과 컨테이너 로그의 시각은 UTC입니다. 한국 시각은 9시간을 더합니다.

## 제3자 소프트웨어

저장소의 소스는 Apache-2.0입니다. 저장소에 함께 들어 있는 제3자 구성 요소는 [`NOTICE`](../NOTICE)에 있습니다.
이미지를 빌드하면 아래 소프트웨어가 이미지 안에 설치됩니다. 이미지를 다른 곳에 배포하면 각 라이선스의 조건을 따릅니다.

| 소프트웨어 | 들어가는 이미지 | 라이선스 |
|---|---|---|
| FFmpeg(배포판 패키지) | worker-io, worker-jobs, worker-gpu | GPL-2.0 이상. 배포판 빌드가 libx264 등 GPL 구성 요소를 켜고 있습니다. 소스는 각 배포판(Debian, Ubuntu)의 소스 패키지로 받을 수 있습니다 |
| NVIDIA CUDA·cuDNN 런타임 | worker-gpu | NVIDIA 소프트웨어 라이선스(CUDA EULA, cuDNN SLA) |
| ONNX Runtime GPU | worker-gpu | MIT |
| PostgreSQL | postgres(공식 이미지 `postgres:16-alpine`) | PostgreSQL License |
| Redis | redis(공식 이미지 `redis:7-alpine`, 7.4 계열) | Redis 7.4부터 RSALv2와 SSPLv1 중 선택 |

앱은 FFmpeg를 라이브러리로 링크하지 않고 별도 프로세스로 실행합니다.
설치한 이미지의 FFmpeg 라이선스는 `infra/deploy/dc.sh exec worker-gpu ffmpeg -hide_banner -L`로 확인합니다.
