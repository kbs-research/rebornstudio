# tests/work

작업 파이프라인 도메인(`services/work`) 테스트입니다. 가져오기·제출·검수·비식별화 신고 처리·반려 누적·보유 목록 순서·제출 게이트·수정률·우선순위를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_deleted_video_work.py` | 삭제된 영상의 작업이 사람 큐에서 빠지는지 검사합니다. |
| `test_edit_rate.py` | 수정률을 검사합니다. AI 초안 보관, 편집 거리, 라벨러 집계와 분모, 권한을 봅니다. |
| `test_held_order.py` | 보유 목록 두 가지(라벨링·검수)의 순서 회귀 테스트입니다. |
| `test_labeling_full.py` | 라벨링 전체 기능의 BE를 검사합니다. 대상은 draft와 report-deident-miss입니다. |
| `test_labels_reviews.py` | labels(리비전 체인)와 reviews INSERT를 검사합니다. 수정 후 승인이 다른 작업의 라벨을 기준으로 하면 409, 기준이 없으면 422인지도 봅니다. |
| `test_claim_order.py` | 가져오기 순서를 검증합니다. 라벨링은 재작업 다음 오래된 순, 검수는 제출한 순이고 `priority` 열은 쓰지 않습니다. |
| `test_rework_holder_gone.py` | 원 작업자가 프로젝트에 없을 때 재작업이 대기열로 가는지(반려·회수), 다른 사람이 받으면 새 작업으로 시작하고 만든 사람이 다시 받으면 재작업 그대로인지 검사합니다. |
| `test_queue_counts_and_rights_claims.py` | 큐 집계와 보유 규칙의 서버 결함 회귀 테스트입니다. 검수 보유자의 비식별화 신고와 신고 목록 행이 사유와 시각을 싣는지도 봅니다. |
| `test_repeated_rejects.py` | 세 번째 반려 뒤에도 작업이 재작업으로 남고, 단건·단계 요약·반려 누적 목록에 관리자 확인 대상으로 표시되는지, 목록이 작업자에게 닫혀 있는지 검증합니다. |
| `test_review_reject_audit.py` | 검수자의 반려가 행위자·대상 작업·사유를 담은 `review.reject` 감사 행을 남기는지 검사합니다. |
| `test_rework_notice.py` | 내 작업 목록의 재작업 행이 마지막 반려 사유를 싣는지 검증합니다. |
| `test_submit_caption_gate.py` | 제출은 화면이 약속한 캡션 기준을 서버에서도 지킵니다. |
| `test_review_caption_gate.py` | 검수자의 수정 후 승인도 라벨러 제출과 같은 캡션 기준을 지킵니다. 거절된 판정은 작업과 검수 보유를 바꾸지 않습니다. |
| `test_work.py` | 작업의 전체 생애주기(claim, submit, review approve 순서)와 실패 경로를 검증합니다. |
| `test_work_flow_gates.py` | 작업 흐름 관문을 검증합니다. 비식별화 신고로 멈춘 작업을 처리 API로 재개·제외하는 흐름과 처리 가드를 봅니다. |
| `test_work_flow_guards.py` | 라벨링·검수 입력 가드를 검증합니다. 남은 문장 구간 키 무시, 저장 경로 비노출, 남은 경계 신고 값 무시, 반납 작업 순서, 넘겨받은 작업의 이력 범위, 검수 보유 만료를 봅니다. |
| `test_work_races.py` | 같은 작업 행을 동시에 바꾸는 요청(제출·멤버 제거)이 한 번만 반영되는지 검증합니다. |
| `test_work_queue_scope.py` | 영상 티켓(MAIU 디스패치 표식)이 사람 큐에 나오지 않는지 검증합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
