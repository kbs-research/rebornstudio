# tests/analysis

분석 어댑터 도메인(`services/analysis`) 테스트입니다. 어댑터 계약, 분석 스택 프리셋, 잡 상태 단위, 샷 검출, 클립 길이·변환 옵션을 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_analysis_contract.py` | 분석 어댑터 계약 테스트입니다. 모든 어댑터가 같은 테스트를 통과해야 합니다. MI 어댑터의 능력 선언과 없는 어댑터 키도 봅니다. |
| `test_analysis_job.py` | `analysis_job`의 어댑터 중립 상태 단위를 검사합니다. |
| `test_analysis_presets.py` | 분석 스택 프리셋을 검사합니다. 카탈로그는 코드에 정의합니다. |
| `test_scene_detect.py` | 샷 검출 어댑터가 엔진 결과를 옮기기만 하는지 검증합니다. |
| `test_scene_options.py` | 클립 길이 설정의 모델 규칙, 미뤄 둔 요청의 기록 값 우선, 잡 기록, 두 스택이 같은 값을 읽는지 검증합니다. 클립 길이는 고정 기본값이라 다른 값은 DB에 직접 씁니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_analysis_contract.py`는 어댑터마다 같은 묶음을 돌리는 도메인 안의 계약이라 `contract/`가 아니라 여기 둡니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
