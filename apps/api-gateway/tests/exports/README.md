# tests/exports

납품물 도메인(`services/exports`) 테스트입니다. 납품 패키지 빌드, 완료 목록과 검색, 일시 오류 재시도, 기준 정의서와 코드 스키마 대조를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_completed_caption_search.py` | 완료 목록의 캡션 검색을 검증합니다. |
| `test_export_counts_and_names.py` | 이력 행의 납품 불가·품질검수 보류 분리, 같은 분 패키지의 파일 이름 구별, 패키지 수정 횟수를 확인합니다. |
| `test_export_download_authz.py` | 납품 패키지 다운로드 링크를 그 프로젝트의 관리자만 받고 검수자·라벨러·다른 프로젝트 관리자는 403인지 확인합니다. |
| `test_export_deidentification.py` | 납품 메타의 비식별화 정보(처리 여부·방식·재확인 표시), 가린 영역이 없는 영상이 `mosaic` 대신 `none`으로 나가는지, 블록에 `face_count`가 없는지, 리포트의 처리·가린 영상 집계, 섞인 패키지의 자체 검증, 워커가 얼굴 좌표 파일을 읽지 않는지 확인합니다. |
| `test_export_omit_optional.py` | 선택 항목 제외 옵션이 메타 파일에서 다섯 키만 빼는지, 판정·체크섬·스키마 검증·리포트·카드·README와 API·감사 기록을 확인합니다. |
| `test_export_sources_index.py` | 원본 영상 목록 `sources.csv`의 열 이름·순서와 BOM, 영상당 한 행과 클립 수, 메타와 같은 값, 내부 업로드 키 제외, 수식 시작 글자 막기, 선택 항목 제외와의 관계, README 안내, 색인과 어긋날 때의 중단을 확인합니다. |
| `test_export_timecode_precision.py` | 납품 시각이 소수 3자리 이내이고 길이가 시각의 차와 1ms 안에서 맞는지, 메타와 색인이 같은 값을 싣는지 검사합니다. |
| `test_export_transient.py` | 내보내기가 멈추거나 끝없이 재시도하지 않는지 검사합니다. |
| `test_export_snapshot.py` | 내보내기의 후보 행과 품질검수 보류 목록을 같은 시점에서 읽어, 같은 건을 담긴 수와 제외 수에서 두 번 세지 않는지 검사합니다. |
| `test_export_runner.py` | 내보내기 처리가 한 번에 하나만 돌고(겹친 실행은 아무 건도 잡지 않음) beat 기본 호출이 한 건만 만드는지 검사합니다. |
| `test_schema_public_text.py` | 납품 스키마 파일의 설명이 공개용 글인지, 개발용 표시(PR·기능 번호·코드 이름)가 새지 않는지 검사합니다. |
| `test_exports.py` | Export 도메인을 검사합니다. 납품 패키지 빌드, 목록, presigned download, 완료 목록을 봅니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
`test_completed_caption_search.py`·`test_export_deidentification.py`·`test_export_omit_optional.py`·`test_export_sources_index.py`·`test_export_timecode_precision.py`는 `tests.exports.test_exports`의 헬퍼를 씁니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
