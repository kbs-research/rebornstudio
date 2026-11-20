# reborn_transcode

## 목적

`reborn-transcode` 패키지의 모듈입니다. DB를 쓰지 않는 작업의 잡 러너 세 개(변환·재생본, 씬 분할, 비식별화)와, 러너와 서버가 함께 쓰는 FFmpeg 도구를 둡니다.
모든 모듈은 presigned URL과 잡 페이로드만 받습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `__init__.py` | 공개 함수를 다시 내보냅니다(`__all__`). 씬 분할은 모듈 경로로 import합니다 |
| `job_runner.py` | 변환·재생본 잡 러너 `run_job`. 입력을 스트리밍으로 인코딩하고, 산출을 한 번 probe해(`_inspect`) 측정값을 회신에 실은 뒤 PUT합니다. 네트워크 오류·5xx·429·만료된 presign(403)은 일시 실패로, ffmpeg 실패는 영구 실패로 1차 분류합니다 |
| `transcode.py` | h264 인코딩 명령(`build_h264_command`)과 스트리밍 인코딩(`transcode_streaming`). NVENC로 시험 인코딩이 되는 서버면 h264_nvenc를, 아니면 libx264를 쓰고(`TRANSCODE_ENCODER`로 고정 가능), NVENC 인코딩이 NVENC·CUDA 오류(세션 한도·장치 없음·GPU 메모리 부족)로 실패할 때만 libx264로 다시 인코딩합니다 |
| `probe.py` | ffprobe로 presigned URL의 메타를 읽습니다. HTTP Range로 컨테이너 헤더만 읽어 MI 요청 전 규격 검사 비용을 줄입니다 |
| `input_guard.py` | ffmpeg·ffprobe 입력 앞에 두는 제한 옵션(`guard_args`). 네트워크 입력은 http·https만, 로컬 경로는 file만 엽니다. 디먹서는 등록을 받는 확장자의 컨테이너(`MEDIA_DEMUXERS`)로 제한합니다. probe 결과가 재생목록 형식이면 거절합니다(`is_playlist`) |
| `output_check.py` | 산출 측정값을 원본과 비교해 위반 코드를 냅니다(`check_output`). 기준은 `DURATION_TOLERANCE_S`, 일시 원인 목록은 `TRANSIENT_CAUSES`. 서버만 부릅니다 |
| `blackness.py` | 검은 화면 검사. 균등 간격의 몇 지점만 디코딩해 휘도를 재고(`sample_luma`), 모든 표본이 검을 때만 위반으로 판정합니다(`is_black_video`) |
| `scene_split.py` | 씬 분할 러너. 컷과 무음 구간을 디코딩 한 번으로 찾습니다. 샷을 최대 40초까지 묶고, 끊는 위치는 무음 구간에서 고릅니다 |
| `deident.py` | 비식별화 러너 `run_deident_job`. 어댑터(`DEIDENT_MODEL`, 비우면 `essential-yunet`)를 이 프로세스에서 실행하고 결과 영상과 얼굴 좌표 JSON을 올립니다 |

## 설계 이유

- 씬 분할 검출은 원본 바이트를 읽어야 하므로 워커에서 실행합니다. 이 모듈을 `api_gateway`의 어댑터에 두면 워커가 `api_gateway`에 의존하게 됩니다.
  그래서 구현은 여기에만 두고, 서버의 `services/analysis/scene_detect.py`는 결과를 계약 타입으로 옮기기만 합니다.
- 무음 구간이 있다고 바로 끊지 않습니다. 검출된 무음 구간은 대부분 기사 안쪽에 있어서, 무음마다 끊으면 클립 수가 2~3배로 늘어납니다.
  무음 구간은 끊을지를 정하지 않고, 어디서 끊을지를 정합니다. 측정값과 기준값은 `scene_split.py` 모듈 docstring에 있습니다.
- 검은 화면 영상도 MI 분석은 성공하지만, 결과는 영상과 무관합니다. 규격 검사로는 잡히지 않으므로 분석 비용을 쓰기 전에 거릅니다.
- 비식별화를 켠 프로젝트에서 등록되지 않은 모델 이름이 지정되면 잡이 실패합니다. 그래서 마스킹하지 않은 영상이 다음 단계로 넘어가지 않습니다.
  검증용 `passthrough` 어댑터는 보고서에 `masked: false`를 적습니다. 그래서 화면이 그 결과를 비식별화된 결과로 표시하지 않습니다.

## 의존성

- 이 폴더가 부르는 것: `httpx`, `ffmpeg`·`ffprobe`(subprocess)
- 부르는 쪽: `workers/worker-jobs/worker_jobs/jobs.py`, `apps/api-gateway`의 media·analysis 서비스
- 잡 페이로드 형식: [`packages/reborn-contracts/queues.md`](../../../reborn-contracts/queues.md)

## 남은 일

- 패키지 단위 남은 일은 [`../../README.md`](../../README.md)에 있습니다.
