# services/analysis

분석 스택 패키지입니다. 이 패키지에는 네 가지가 있습니다. 분석기(MI·샷 검출·더미)의 결과를 도메인 계약으로 옮기는 어댑터, 어느 어댑터가 무엇을 할 수 있는지 적은 등록부, 프로젝트가 고르는 스택 카탈로그, 분석 상태 어휘입니다.
이 패키지는 판정을 하지 않습니다. 판정은 결과를 쓰는 도메인(`work`·`media`)에 있습니다.

계약 타입(`Capability`·`Segment`·`AnalysisResult`·`AdapterInfo` 등)은 [`reborn_contracts.analysis`](../../../../../packages/reborn-contracts/python/src/reborn_contracts/analysis.py)에 있습니다. 화면도 같은 능력 이름을 읽어야 하기 때문입니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `registry.py` | 어댑터 등록부. 어댑터별 능력 선언은 이 파일에만 둡니다(`MAIU`·`SCENEDETECT`, `MAIU_KEY`) |
| `presets.py` | 분석 스택 카탈로그(`ncp-mi`·`ncp-shots`). 저장소와 분석기의 조합, 사용 가능 여부와 이유, 준비 상태(`readiness`) |
| `job_state.py` | `analysis_job` 상태 분류(`ACTIVE`·`SETTLED`·`PRODUCED`)와 단계 전이(`advance`·`set_status`) |
| `status_map.py` | 어댑터 단계(MI의 `registering`·`summarizing` 등)를 중립 상태로 옮기는 표(`neutral_status`) |
| `scene_options.py` | 클립 길이 설정(MI의 `sceneRange`·최소·최대 초, 샷 검출의 병합 상한). 고정 기본값(`MEDIUM`)을 생성 때 프로젝트 설정에 적고 잡에도 기록합니다. 프로젝트 수정과 분석 요청으로는 바꿀 수 없습니다 |
| `scene_detect.py` | 샷 검출 어댑터. 검출과 병합은 `reborn_transcode.scene_split`이 하고, 이 모듈은 결과를 계약으로 옮깁니다 |
| `dummy.py` | 더미 어댑터. 결정적 결과(`video_id` 해시 시드)와 빈 결과(`EMPTY`) 프리셋. 계약 테스트의 기준 구현으로 씁니다 |

## 설계 이유

- 어댑터는 형식 변환만 합니다. 어댑터가 판정까지 하면 스택을 바꿀 때 판정 규칙도 바뀝니다. 그러면 같은 데이터가 스택에 따라 다르게 판정됩니다.
- 어댑터가 제공하지 못하는 값은 비웁니다. 0이나 빈 문자열로 채우면 화면이 결과가 있다고 표시합니다. 선언하지 않은 능력의 빈 값은 정상이고, 선언한 능력의 빈 값은 결함입니다. 계약 테스트가 둘을 구분합니다.
- 분석 스택은 프로젝트를 만들 때 고정합니다. 스택을 바꾸면 한 프로젝트에 두 분석기의 클립이 섞이고, 라벨러는 그 차이를 모른 채 작업합니다. 그래서 프로젝트 수정 API의 허용 목록에 스택 키가 없고, 테스트가 스택 키가 없는지 확인합니다.
- 조합은 코드가 정하고, 운영자는 값(연결·버킷)만 채웁니다. 저장소와 분석기를 자유롭게 짝지으면 등록은 되는데 분석만 실패하는 조합이 생깁니다. 쓸 수 없는 프리셋도 `available=False`와 이유를 달아 목록에 남깁니다.
- `analysis_job`은 판정용 `status`(중립 값)와 표시용 `stage`(어댑터의 원래 단계)를 나눠 둡니다. 막거나 허용하는 코드는 `status`와 `job_state`의 세 분류를 읽습니다. 진행 위치를 보여 주는 코드(준비 타임라인·운영 게이지)는 `stage`를 읽습니다. 두 값을 섞으면 MI 단계 값이 `status`의 CHECK 제약을 위반하거나, 운영 게이지가 MI가 어느 단계에서 멈췄는지 표시하지 못합니다.
- 단계 전이는 `job_state.advance` 한 곳에서 합니다. `job.stage`를 직접 바꾸면 `status`가 따라 바뀌지 않습니다.
- `partial`(일부 클립만 성공)은 별도 상태입니다. `done`으로 합치면 빠진 클립이 사라지고, `failed`로 합치면 성공한 클립이 사라집니다.
- 샷 검출에는 PySceneDetect 대신 ffmpeg를 씁니다. ffmpeg는 이미 필수 의존성이고, PySceneDetect는 OpenCV를 함께 설치해야 하기 때문입니다. 검출 함수는 인자로 바꿀 수 있으므로 계약 테스트는 ffmpeg 없이 스텁으로 실행됩니다.

## 의존성

- 부르는 것: `reborn_contracts.analysis`, `reborn_transcode.scene_split`(`scene_detect`), `services.platform_credentials`(`presets.readiness`). DB와 저장소에는 접속하지 않습니다.
- 부르는 쪽: `maiu`(요청·분석 결과 회수 작업), `media`(장면 분할·타임라인·보관 정리), `org.project`(프로젝트 생성 시 스택·옵션), `stacks`(분석 대상 선택), `analytics`, `caption_qc.runner`
- 테스트: `tests/analysis/`. 계약 테스트(`test_analysis_contract.py`)는 어댑터 목록(`ADAPTERS`)을 파라미터로 받아 실행됩니다. 새 어댑터는 목록에 이름을 더해 같은 테스트를 통과시킵니다.

## 알려진 제약

- MI 결과를 계약으로 옮기는 코드는 `services/maiu/reconciler`에 있습니다. `registry.MAIU`에는 능력 선언만 있습니다.
- `partial` 상태를 만드는 어댑터가 없습니다. 클립 단위 재시도도 없습니다.
