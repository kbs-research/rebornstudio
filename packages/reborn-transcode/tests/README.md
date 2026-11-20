# reborn-transcode/tests

## 목적

`reborn_transcode` 모듈의 단위 테스트입니다. ffmpeg·ffprobe는 subprocess를 흉내 내고, 입력 다운로드와 업로드는 대역으로 바꿉니다.
그래서 DB, 네트워크, GPU 없이 실행됩니다.

## 주요 파일

| 파일 | 확인하는 것 |
|---|---|
| `test_transcode.py` | h264 인코딩 명령 인자(preset·스레드) |
| `test_probe.py` | ffprobe 명령과 JSON 파싱(비트레이트는 컨테이너 값, 없으면 비디오 스트림 값), 시간 초과, 오류 메시지의 URL 서명 제거 |
| `test_blackness.py` | 휘도 표본 측정과 검은 화면 판정 규칙 |
| `test_input_guard.py` | 모든 ffmpeg·ffprobe 명령의 입력 앞에 프로토콜·디먹서 제한이 붙는지, 재생목록 형식을 거절하는지. 실제 ffprobe로 HTTP 재생목록을 열어 목록의 주소로 요청이 나가지 않는지도 확인합니다 |
| `test_output_check.py` | `check_output`의 경계. 기준 초과·이내, 길이 없음, 읽지 못한 산출, 검사 시간 초과 |
| `test_job_outputs.py` | 잡 러너가 업로드 전에 산출을 읽어 측정값만 싣는지. 실제 ffmpeg로 잘린 입력이 종료 코드 0으로 짧은 사본을 내는 상황을 재현합니다 |
| `test_job_progress.py` | 산출이 여럿인 잡의 진행률이 타깃을 넘어 누적되는지 |
| `test_scene_split.py` | 컷을 샷으로 바꾸고 묶는 규칙, 명령과 파싱. 실제 ffmpeg 테스트 포함 |
| `test_deident_local.py` | 비식별화 러너. 기본 모델이 내장 YuNet 어댑터인지, 결과 영상과 좌표 JSON을 올리는지, 좌표 주소가 없으면 좌표를 올리지 않는지, 등록되지 않은 모델이면 영구 실패하는지 |
| `test_deident_local_log.py` | 로컬 비식별화의 입력 다운로드 실패 로그에 presigned 주소의 서명·자격증명 쿼리가 남지 않는지 |

## 설계 이유

- CI 러너에는 ffmpeg가 없습니다. 실제 ffmpeg를 부르는 테스트는 `skipif`로 건너뛰고, 나머지는 subprocess를 흉내 내어 항상 실행합니다.
  모듈은 명령 생성 함수를 따로 두어, 인자 형식과 판정 규칙을 ffmpeg 없이 검증할 수 있게 합니다.
- 어댑터가 씬 분할 결과를 도메인 모델로 옮기는 계약은 `apps/api-gateway/tests/analysis/test_analysis_contract.py`가 확인합니다.
  이 폴더는 엔진의 판정만 봅니다.

## 의존성

- `pytest`(dependency group `test`), `httpx`
- 실행: `uv run --package reborn-transcode --group test pytest packages/reborn-transcode/tests/ -q`

## 남은 일

- 없음.
