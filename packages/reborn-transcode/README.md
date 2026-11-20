# reborn-transcode

## 목적

DB 없이 실행하는 미디어 처리 패키지입니다. DB를 쓰지 않는 작업의 워커(`workers/worker-jobs`)가 잡 페이로드 하나로 인코딩, 씬 분할, 비식별화를 실행합니다.
서버(`apps/api-gateway`)는 probe, 검은 화면 검사, 산출 검사 판정에 이 패키지의 함수를 씁니다.
입력은 presigned GET URL, 출력은 presigned PUT URL입니다. DB, 저장소 자격증명, 앱 설정은 쓰지 않습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | 패키지 정의. 런타임 의존성은 `httpx` |
| [`src/reborn_transcode/`](src/reborn_transcode/README.md) | 모듈. 잡 러너·인코딩·probe·산출 검사·씬 분할·비식별화 |
| [`tests/`](tests/README.md) | 단위 테스트. subprocess와 HTTP를 흉내 내어 ffmpeg와 네트워크 없이 실행합니다 |

## 설계 이유

### DB에 붙지 않는다

잡 러너의 외부 접점은 세 가지입니다. 입력 presigned GET, 출력 presigned PUT, 호출자가 넘긴 하트비트 콜백입니다.
상태 전이, 펜싱 토큰(현재 시도가 아닌 보고를 버리는 토큰) 확인, 재시도 판정은 회신을 받는 서버 쪽 한 곳에서 합니다.
그래서 이 러너를 쓰는 워커는 이미지, 언어, 노드를 바꿔도 계약이 그대로입니다.
루트 `.importlinter`의 `transcode-standalone` 계약이 `api_gateway`·`worker_io`·`worker_jobs` import를 막습니다.

### 하트비트는 일이 끝날 때까지 보낸다

서버의 리스는 하트비트로만 연장됩니다(5분). 인코딩 뒤의 산출 검사와 업로드도 길면 리스를 넘기므로,
잡 러너와 비식별화 러너는 그동안에도 같은 주기로 하트비트를 보냅니다(`job_runner.heartbeating`).
씬 분할 러너는 검출이 도는 동안 페이로드의 `heartbeat_every_s`마다 하트비트를 보내고, 실행 상한(`timeout_s`)을 넘기면 ffmpeg를 끝내고 일시 오류로 보고합니다.
하트비트가 끊기면 회수가 시도를 되돌려 같은 영상이 새 토큰으로 처음부터 다시 돕니다.

### 산출물은 올리기 전에 한 번 읽는다

ffmpeg는 입력이 도중에 끊겨도 종료 코드 0으로 짧은 사본을 씁니다. 종료 코드만 보면 그 사본이 성공으로 올라가 분석과 라벨링까지 진행됩니다.
그래서 잡 러너는 업로드 전에 산출물을 probe해 길이, 프레임레이트, 코덱, 해상도, 크기를 회신(`outputs`)에 싣습니다.
산출물을 읽지 못해도 예외를 내지 않고 그 사실을 싣습니다.
검사가 60초를 넘기면 제한 시간을 두 배로 늘려 한 번 더 읽고, 그래도 넘기면 `timed_out`을 싣습니다.
부하가 높은 서버에서는 헤더 읽기도 늦어집니다. 시간 초과를 깨진 사본으로 판정하면 수십 분 걸린 인코딩을 버리게 됩니다.

원본과 비교하는 판정은 서버의 `api_gateway.services.media.transcode`가 이 패키지의 `check_output`으로 합니다.
길이 차가 `DURATION_TOLERANCE_S`(0.5초)를 넘거나 산출을 읽지 못하면 실패입니다.
검사 시간 초과(`output_check_timeout`)는 `output_check.TRANSIENT_CAUSES`에 들어 있으므로 시도 횟수를 쓰지 않는 일시 실패로 처리합니다.

### 비식별화 모델은 워커 프로세스 안에서 돈다

얼굴 비식별화 모델은 내장 비식별화 모듈(`packages/deident`, YuNet)입니다. 허용형 라이선스 모델만 쓰므로 워커 프로세스 안에서 어댑터로 부릅니다.
모듈은 비식별화 잡을 받는 워커 이미지에만 설치하므로 `deident.py`가 어댑터 함수 안에서 import합니다.

### subprocess로 ffmpeg를 직접 부른다

ffmpeg 바인딩 라이브러리를 쓰지 않고, 명령 인자 목록을 만들어 subprocess로 실행합니다.
인자를 만드는 함수(`build_h264_command`·`probe.build_command`·`scene_split.build_command`)를 분리해 두어, 테스트가 ffmpeg 없이 인자 형식을 검증합니다.
오류 메시지에 presigned URL의 서명이 남지 않도록 쿼리 문자열을 지웁니다.

모든 입력 앞에 `input_guard.guard_args`를 둡니다. Content-Type이 HLS인 파일은 ffmpeg가 재생목록으로 읽고, 목록에 적힌 주소를 워커 안에서 엽니다.
프로토콜 제한만으로는 http 주소를 막지 못해서 디먹서도 영상 컨테이너로 제한합니다.

## 의존성

- 이 패키지가 부르는 것: `httpx`(입력 GET·산출 PUT), 실행 환경의 `ffmpeg`·`ffprobe`
- 부르는 쪽: `workers/worker-jobs`(`run_job`·`scene_split.run_job`·`run_deident_job`),
  `apps/api-gateway`의 `services/media/probe.py`·`services/media/transcode.py`·`services/analysis/scene_detect.py`
- 환경변수: `FFMPEG_BIN`·`FFPROBE_BIN`(실행 파일 경로), `DEIDENT_MODEL`(비식별화). 뜻은 `docs/env-catalog.md`에 있습니다.

## 테스트

```bash
uv run --package reborn-transcode --group test pytest packages/reborn-transcode/tests/ -q
```

CI는 `api-gateway` 잡에서 이 테스트를 실행합니다. CI 러너에는 ffmpeg가 없어서 실제 ffmpeg를 부르는 테스트는 건너뜁니다.

## 남은 일

- 다른 비식별화 모델을 붙이려면 `deident.py`의 `_ADAPTERS`에 어댑터를 더합니다.
