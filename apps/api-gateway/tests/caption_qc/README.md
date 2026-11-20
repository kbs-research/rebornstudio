# tests/caption_qc

캡션 품질검수 도메인(`services/caption_qc`) 테스트입니다. 규칙 경계값, 어댑터 계약, 러너, 요청 밖 실행, 기록 DTO를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_bedrock_client_config.py` | Bedrock 클라이언트가 연결 5초·읽기 60초·standard 재시도를 싣는지, 키가 하나라도 없으면 만들지 않는지, 검사 호출이 그 설정을 쓰는지 확인합니다. |
| `test_caption_qc_prompt_guard.py` | 기본 시스템 프롬프트가 입력 캡션을 지시가 아닌 데이터로 다루라고 적는지, 모델 지적 문장을 `MESSAGE_MAX`자로 자르는지 검사합니다. |
| `test_caption_qc_adapters.py` | QC 어댑터 계약 테스트입니다. 등록된 모든 어댑터가 이 테스트 묶음을 통과해야 합니다. |
| `test_caption_qc_dto.py` | 저장된 품질검수 기록을 화면용 DTO로 바꿀 때 지금 문구가 나오는지 DB 없이 확인합니다. |
| `test_caption_qc_rules.py` | 캡션 품질검수 규칙을 DB 없이 경계값으로 검사합니다. |
| `test_caption_qc_async.py` | 제출·승인이 검사를 기다리지 않고 커밋되는지, 워커가 대상을 찾아 한 번만 기록하는지, 낡은 시점·실패 상한·조회 기간, 다른 프로젝트의 제출·승인이 끝난 검사와 실패 횟수를 되돌리지 않는지, 끝난 실행의 투입 표시 해제, 화면 상태(`running`·`failed`·`recorded`·`none`)와 내보내기 대기, 이력 행의 대기 시각(`qc_wait_until`)을 봅니다. 워커 태스크 대신 `backlog.find_owed`·`runner.run_owed`를 직접 부릅니다. |
| `test_caption_qc_runner.py` | 러너가 시점마다 무엇을 기록하고 게이트를 언제 바꾸는지 봅니다. `runner.run`을 직접 부릅니다. 다시 검사의 모델 실패 시 보류 유지, 보류분 캡션 고쳐 다시 승인(새 개정·판정·워커 승인 검사)도 여기서 봅니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_caption_qc_rules.py`는 웹 소스(`apps/web/src/lib/video_meta.ts`)를 읽습니다(`parents[4]`). 캡션 읽기 규칙의 판정 표는 `contract/test_caption_shapes.py`가 돌립니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
