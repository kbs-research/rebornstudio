# services/exports

납품물 도메인입니다. 승인된 작업을 납품 패키지(zip)로 묶어 저장소에 올리고, 내려받기 주소를 줍니다. 완료 목록과 캡션 품질검수 보류함 조회도 맡습니다.
reporting 도메인 중 쓰기를 하는 쪽입니다. `exports` 테이블과 저장소에 쓰는 곳은 이 패키지뿐입니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `export_service.py` | 언제 만들고, 어디에 두고, 누가 받는지를 다룹니다. 납품 요청(`create`), 워커 처리(`process_pending`의 잡기·만들기와 `reclaim_stale` 회수. `single_runner`로 한 번에 하나만 돌고 beat 호출마다 한 건만 만듭니다), 납품 후보 쿼리(`approved_rows_stmt`), 다운로드 주소, 완료 목록·집계·상세(`list_completed`·`completed_summary`·`get_completed`), 품질검수 보류함(`list_qc_held`) |
| `export_package.py` | 패키지에 무엇이 들어가는지를 다룹니다. 클립 메타(`build_metadata`)·색인·원본 영상 목록(`sources_csv`·`source_row`)·데이터셋 카드·README·리포트 조립(`write_package`), 납품 행 검사(`check_row`), 제외 사유의 화면 이름(`FIELD_LABEL`), 무결성 검사, 선택 항목 생략(`OPTIONAL_LABEL_FIELDS`), 비식별화 정보(`deidentification_of`) |

## 설계 이유

- 납품 판정은 `export_package.check_row` 한 곳에서 합니다. 통계 화면(`analytics.dashboard`)의 납품 불가 사유 분포와 캡션 품질검수(`caption_qc.runner`)가 이 함수를 그대로 부릅니다. 각자 검사를 구현하면 같은 클립에 다른 결론을 냅니다.
- 패키지에 영상 파일을 넣지 않습니다. 받는 쪽이 원본을 갖고 있으므로, 클립은 `source_video_id`와 원본 기준 시각으로 구간을 가리킵니다. 분석이 변환본을 읽었으면 등록할 때 잰 시작 오프셋으로 원본 시간축에 맞춰 환산합니다. 오프셋이 없는 클립은 어긋난 시각을 내보내지 않도록 제외합니다.
- 원본 영상 목록 `sources.csv`는 담긴 클립의 원본 영상마다 한 행을 싣습니다. 메타 JSON의 `source_video_id`만으로는 플랫폼 DB 없이 원본 파일을 찾을 수 없어서, 제목·원래 파일 이름·버킷 키·원본 체크섬을 같이 둡니다. 체크섬·길이·방송 메타는 같은 영상 클립의 메타 dict에서 옮겨 값과 형식이 같습니다. 업로드 영상의 내부 키(`intake/…`)는 받는 쪽이 찾아갈 수 없어 싣지 않습니다. 파일 이름은 유일하지 않아도 되고, 원본은 `source_video_id`와 `source_checksum`으로 가리킵니다. 엑셀이 한글을 읽도록 UTF-8 BOM으로 시작하고 칸마다 수식 시작 글자를 막습니다(`csv_cell`). CSV는 열을 늘 같게 두므로 선택 항목 제외와 무관합니다. 닫기 전에 색인의 원본 영상·클립 수와 대조합니다(`_check_sources`).
- 제외는 폐기가 아닙니다. 싣지 못한 클립은 사유와 함께 `report.json`에 남깁니다. 품질검수가 보류한 승인분도 리포트의 제외로 셉니다. 세지 않으면 일부만 담긴 패키지가 제외 0건으로 표시됩니다.
- 라벨러가 채우는 내용은 캡션 하나입니다. 세부 묘사(`description`)와 키워드(`keywords`)는 패키지 형식을 유지하려고 키를 두지만 값은 늘 비어 있습니다. 이전 작업에 남은 값도 싣지 않습니다.
- 선택 항목 생략은 값이 늘 비어 있는 키(`OPTIONAL_LABEL_FIELDS`)를 빼고, 파일을 쓸 때만 적용합니다. 판정은 전체 모델로 하므로 옵션에 따라 납품 대상이 달라지지 않습니다. 색인 체크섬(xxh64)은 생략한 뒤의 바이트로 잽니다.
- 워커 처리는 잡기와 만들기를 서로 다른 트랜잭션으로 나눕니다. 잡기는 `pending`을 `running`으로 바꾸고 시도 수를 올립니다. 만들기는 행을 잠근 상태로 조립하고 업로드합니다. 워커가 만드는 도중 중단되면 잠금이 풀린 `running` 행이 남고, 다음 주기의 회수가 그 행을 되돌립니다. 시도 수는 이미 올라 있으므로, 매번 중단되는 건도 상한(`_MAX_ATTEMPTS`)에 이르면 `failed`로 멈춥니다.
- 승인 검사를 기다리는 승인분이 있는 프로젝트는 내보내기 시작을 최대 `_QC_WAIT`(10분)까지 미룹니다. 승인은 품질검수를 기다리지 않고 커밋되므로, 바로 내보내면 검사 전 승인분이 실립니다. 대기 판정은 워커와 같은 조건(`caption_qc.backlog.projects_awaiting_gate`·`gate_wait_of`)을 씁니다. 이력 응답에는 미루기가 끝나는 시각(`qc_wait_until`)을 싣습니다.
- 클립 메타의 비식별화 정보는 처리 여부, 방식, 누락 의심 세 가지입니다. 이 값은 영상의 비식별화 보고(`extra["deident"]`)에서 읽습니다. 처리했지만 가릴 얼굴을 찾지 못한 영상(보고의 `masked`가 거짓)은 방식이 `none`이고, `report.json`은 처리한 영상 수(`processed_videos`)와 가린 영상 수(`masked_videos`)를 따로 셉니다(`deident_masked`). 얼굴 수는 납품 항목이 아니므로 내보내기는 얼굴 좌표 파일을 읽지 않습니다. `write_package`는 저장소를 읽지 않는 순수 함수이므로 테스트가 직접 부릅니다.
- 저장소 일시 오류는 다음 주기에 다시 시도합니다(`export_upload_retrying`). 영구 오류만 실패로 확정합니다.

## 의존성

- 부르는 것: `models`, `reborn_contracts`(`ORIGINAL_FILENAME_KEY`), `services.audit`·`authz`·`caption`·`csv_cell`·`operating_time`·`platform_credentials`·`reject_reasons`·`text_search`·`video_meta`·`transient`, 허용 목록의 `media.storage`·`caption_qc.backlog`
- 부르는 쪽: `api/v1/exports.py`, `analytics.dashboard.capacity`(읽기), `caption_qc.runner`(`check_row`), `workers/worker-io`의 `run_pending_exports`(30초 주기)

## 알려진 제약

- 조립할 때 승인 행 전부를 메모리에 올립니다.
- 전체 납품 가능 집계에 상한(`DELIVERABLE_SCAN_LIMIT`)이 있습니다. 넘으면 화면이 세지 않았다고 표시합니다.
