# services/caption_qc

캡션 품질검수 도메인입니다. 제출본과 승인본의 캡션을 규칙과 모델로 검사합니다. 판정(`verdict`)과 납품 게이트(`qc_status`)를 기록하고, 사람이 보류를 푸는 기능을 제공합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `rules.py` | 규칙 검사(캡션 문장·메타데이터 필수값·비속어 사전·개인정보 정규식·클립 길이·비식별화·납품 관문 사유)와 판정 도출(`verdict_of`·`qc_status_of`). 순수 함수만 둡니다 |
| `adapters.py` | 검사 모델 어댑터. `QcRequest`·`QcResult` 계약, `QcAdapter` 프로토콜, 어댑터(`dummy`·`llm`·`bedrock`) 카탈로그, 프롬프트 지문(`prompt_hash`), 연결 서비스별 사용 가능 어댑터(`adapter_keys_for`). DB를 import하지 않습니다 |
| `settings.py` | 프로젝트별 설정(`settings.caption_qc`: 켜기·어댑터·연결 이름·프롬프트·용어집·비속어 사전), 저장 검증(`check_connection_name`), 미적용 사유 문장 상수. 관리자가 바꾸는 것은 켜기·어댑터·연결 이름이고 프롬프트·용어집·비속어 사전은 고정 기본값(비움)입니다 |
| `backlog.py` | 아직 검사받지 못한 제출·승인을 찾는 조건. 워커 투입(`find_owed`), 실행 직전 확인(`is_owed`), 화면의 `검사 중`(`state_of`·`running_ids`), 내보내기 대기(`projects_awaiting_gate`·`gate_wait_of`), Redis 투입 표시와 재시도 간격 |
| `runner.py` | 재료를 모아 검사하고 실행 기록(`caption_qc_runs`)과 게이트를 남깁니다. 워커 실행(`run_owed`)과 요청 안 실행(`run`) |
| `hold.py` | 사람이 보류를 푸는 세 방법. 캡션을 고쳐 다시 승인(`revise`), 다시 검사(`recheck`), 사유를 남긴 보류 해제(`release`). 셋 다 `project_admin`이 합니다 |
| `bedrock_client.py` | Bedrock 클라이언트 생성(`make`). 검사 호출이 시한(연결 5초·읽기 60초·standard 재시도 2회)을 쓰고, 키가 하나라도 없으면 만들지 않습니다(`BedrockKeysMissingError`) |
| `__init__.py` | 저장된 기록을 화면 DTO로 바꿉니다(`qc_finding_dto`·`qc_stages_dto`). 건너뛴 단계의 사유는 원인 코드(`cause`)로 현재 문장을 만듭니다 |

## 설계 이유

- 제출과 승인은 검사를 기다리지 않고 커밋합니다. 검사는 커밋 뒤에 워커가 합니다. 그래서 검사 대상인지는 커밋된 사실로 다시 판정합니다. 기준은 마지막 라벨 행이나 마지막 승인 판정 뒤에 같은 시점의 실행 기록이 있는지입니다. 이 조건은 `backlog.py` 한 곳에 둡니다. 각자 조건을 적으면 화면은 `검사 중`인데 워커는 대상이 아니라고 보는 건이 생깁니다.
- 납품 게이트는 승인과 다시 검사에서만 겁니다(`models.work.CAPTION_QC_GATE_TRIGGERS`). 제출 검사의 결과는 검수자에게 보여 줄 근거입니다.
- `rules.py`와 `adapters.py`는 DB도 다른 도메인도 import하지 않습니다. 재료는 `runner.py`가 모읍니다. 그래서 규칙의 경계값을 DB 없이 표로 시험할 수 있습니다.
- 규칙 검사를 먼저 합니다. 비속어는 사전이, 개인정보는 정규식이 더 정확하고 비용도 들지 않습니다. 모델은 맞춤법, 부적절한 문장, 용어 일관성처럼 규칙으로 찾지 못하는 항목만 맡습니다.
- 판정은 `rules.verdict_of`·`qc_status_of`에서만 도출합니다. 어댑터는 지적만 내고 판정하지 않습니다. 그래서 모델을 바꿔도 같은 캡션은 같은 판정을 받습니다.
- 필수값·스키마 검사는 다시 구현하지 않습니다. `exports.export_package.check_row`의 결과를 `from_delivery_blockers`로 옮깁니다. 구현이 둘이면 납품은 되는데 품질검수는 막는 경우가 생깁니다.
- 삭제 표시한 문장을 빼는 규칙은 `services/caption.py`의 `live_sentences`에 있습니다. 제출 게이트도 같은 함수를 씁니다.
- 지적 등급은 `info`·`warn`·`error`입니다. `info`는 판정에 들어가지 않습니다. 메타데이터 누락처럼 선택 항목이 빈 경우는 `info`로 냅니다. `warn`으로 내면 선택 항목이 사실상 필수가 되기 때문입니다.
- 라벨러가 채우는 내용은 캡션 하나라 세부 묘사 단계는 실행하지 않고, 지난 실행을 표시하려고 단계 이름만 남깁니다.
- 화자 목록이 비어 있으면 누락으로 보지 않습니다. 분석 스택이 화자를 내지 않은 경우이기 때문입니다. 화자가 있는데 이름이 빈 경우만 지적합니다.
- 워커 실행에서 모델 호출이 실패하면 기록하지 않고 다시 시도합니다. 세 번째 실패(`backlog.MAX_FAILURES`)에서는 `model_failed` 단계와 규칙 판정으로 기록합니다. 다시 검사는 요청 안에서 한 번 시도하고, 모델이 실패하면 보류를 그대로 둡니다. 실패한 시도는 `caption_qc.failed` 감사 이벤트로 남깁니다.
- 보류를 푸는 방법이 캡션 수정뿐이면, 오탐 한 건이 납품에서 계속 빠지거나 사람이 검사를 통과하려고 캡션을 고칩니다. 그래서 다시 검사와 사유를 남긴 해제를 함께 둡니다. 판정은 사람이 합니다.
- 어댑터 카탈로그는 `analysis.registry`가 아니라 이 패키지에 둡니다. `analysis.registry`는 영상 분석 어댑터 카탈로그입니다. 그곳에 두면 `work`가 `caption_qc`를, `caption_qc`가 `analysis`를 부르는 의존이 생깁니다.

## 의존성

- 부르는 것: `models`(WorkItem·CaptionQcRun·Label·Review·Clip·Video·Project·Connection), `services.audit`·`authz`·`caption`·`crypto`·`video_meta`·`deident_gap`, Redis(`backlog`의 투입 표시), httpx·boto3(모델 호출), 허용 목록의 `exports.export_package`·`analysis.scene_options`
- 부르는 쪽: `workers/worker-io`의 `dispatch_caption_qc`·`caption_qc_run`, `api/v1/exports.py`(보류함과 보류 처리), `work.service.queries`(작업 상세의 품질검수 상태, `backlog.state_of`), `exports.export_service`(`backlog`), `org.project`·`stacks.connections`(`settings`·`adapters`)

## 남은 일

- 비속어 사전은 부분 문자열로 매칭하므로 정당한 낱말에도 오탐이 날 수 있습니다.
- 모델 호출이 거듭 실패한 건은 모델 검사를 다시 받지 못합니다.
- 설정 기본값이 꺼짐입니다. 프로젝트가 `enabled`·`adapter_key`·`connection_name`을 정하지 않으면 규칙 검사만 실행됩니다. 건너뛴 이유는 화면에 표시됩니다.
