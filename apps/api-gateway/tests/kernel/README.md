# tests/kernel

`services/` 바로 아래 평면 모듈(커널 계약 대상) 테스트입니다. 이벤트 행위자 이름(`audit`), 오류 문장(`error_text`), 영상 메타 상속과 반려 사유 코드(`video_meta`·`reject_reasons`)를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_api_hardening.py` | CORS 사전 요청이 PUT(스택 저장)을 허용하는지, API 엔진이 풀의 커넥션을 쓰기 전에 확인(`pool_pre_ping`)하는지 검사합니다. |
| `test_credentials_key_rotation.py` | 자격증명 암호화 키 교체를 검사합니다. 이전 키 목록으로 옛 암호문을 읽는지, 틀린 키가 시크릿 없는 오류로 실패하는지, 드라이런은 바꾸지 않고 적용은 이전 키 행만 한 트랜잭션으로 바꾸며 다시 적용해도 같은지, 명령 출력이 건수뿐인지 봅니다. |
| `test_environment_default.py` | `ENVIRONMENT`가 없으면 prod로 보고 기본 비밀값을 거절하는지, 실패 문구가 시크릿을 싣지 않고 `ENVIRONMENT=local` 안내를 붙이는지 검사합니다. |
| `test_prod_fake_maiu_guard.py` | 운영 설정이 `MAIU_ENABLED=false`(가짜 MI)로 기동하지 않고, `ALLOW_FAKE_MAIU=true`를 함께 줄 때만 기동하는지 검사합니다. |
| `test_csv_cell.py` | CSV 칸이 수식 시작 글자로 시작하면 작은따옴표를 붙이는 규칙(`services/csv_cell`)을 검사합니다. |
| `test_error_text.py` | 외부 서비스 예외를 운영자용 문장으로 바꾸는 규칙(`services/error_text`)을 검사합니다. |
| `test_event_actor_name.py` | 이벤트의 행위자 이름은 서버가 붙입니다. |
| `test_meta_reject_codes.py` | 영상 메타 상속과 반려 사유 코드를 서버가 지키는지 검증합니다. 사유 목록은 고정 기본값이라 편집 API는 없고, 저장된 목록을 읽는 규칙과 끈 사유의 이력 표시는 DB에 목록을 직접 써서 봅니다. |
| `test_text_search.py` | 검색어를 LIKE 패턴으로 바꾸는 규칙(`services/text_search`)을 검사합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
평면 모듈은 여러 도메인이 같이 쓰므로 어느 도메인 폴더에도 넣지 않습니다. 캡션 게이트(`caption.py`)는 웹과 판정 표를 나눠 읽어 `contract/`에 있습니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
