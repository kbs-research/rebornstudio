# services/org

프로젝트 도메인입니다. 프로젝트와 멤버(역할)를 맡고, 프로젝트가 속하는 기본 조직 한 행을 찾습니다. 조직 관리 화면과 API는 없습니다.
다른 도메인의 작업은 모두 프로젝트 안에서 실행됩니다. 인가의 근거인 `ProjectMember.role`도 이 도메인의 테이블에 있습니다. 역할 조회와 가드는 평면 모듈 `services/authz.py`가 맡습니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `project.py` | 프로젝트 생성·조회·설정 수정·soft delete, 멤버 초대·역할·제거·나가기(보유한 라벨링·검수 작업을 대기열로 되돌림). 역할별 설정 값 가리기(`redact_for_role`) |
| `organization.py` | 기본 조직 id를 돌려주는 `default_org_id`. 한 행만 있다는 것은 마이그레이션 0101의 유일 인덱스가 보장합니다 |

## 설계 이유

- 권한 가드는 `services/authz.py`에 둡니다. 가드가 이 패키지에 있으면 권한 검사 하나 때문에 모든 도메인이 `org`를 import하게 됩니다. 지금은 `work`·`media`·`maiu`·`workforce`·`analytics`가 `org`를 import하지 않습니다.
- 프로젝트 응답의 `settings`에는 품질검수 프롬프트와 분석 스택 식별자가 함께 저장됩니다. 그래서 `redact_for_role`은 관리자가 아닌 역할에게 `settings`를 비워서 주고, 품질검수는 켜짐 여부와 검사 방식만 줍니다. 판정은 이 함수 한 곳에서 합니다.
- 멤버가 나가거나 제거되면 그 사람이 보유한 라벨링·검수 작업을 대기열로 되돌리고 초안을 비웁니다(`work.service.release_holds`). 보유에는 자동 회수가 없어서 그대로 두면 작업이 멤버가 아닌 사람에게 묶이고, 초안에 이전 작업자의 입력이 남습니다. 푼 작업의 ID와 수는 `member.removed`·`member.left`에 남깁니다.
- 나가기, 제거, 역할 변경으로 마지막 관리자를 없앨 수 없습니다(409 `last_admin_cannot_leave`, `_ensure_other_admin_exists`).
- 참여 요청과 승인, 참여 기간은 없습니다. 멤버는 관리자의 초대로만 들어옵니다. 승인 대기(`pending`) 행이 있으면 초대로 되살립니다.
- 프로젝트 상태는 운영 중(`active`)·일시정지(`paused`)·종료(`completed`) 셋입니다. DB 제약이 받는 `archived`는 응답에서 `completed`로 내보내고 수정 요청은 받지 않습니다. 상태에 따라 신규 가져오기를 막는 판정은 `work.service.classify.reject_if_not_claimable` 한 곳에 있습니다.
- 분석 스택, 장면 분할, 품질검수, 비식별화 설정의 모양과 기본값은 각 도메인이 정합니다. `project.py`는 그 값을 읽어 `settings`에 넣습니다.
- 처리 옵션(캡션 최소 길이·문장 타임코드·원본 보관·비식별화 목표·클립 길이·반려 사유·품질검수 프롬프트와 용어집·비속어 사전)은 고정 기본값입니다. 수정에서 바꿀 수 있는 것은 이름·설명·가이드·마감일·목표 시간·상태, 비식별화 켜기, 품질검수 켜기·검사 방식·모델 연결입니다. 동시 보유 한도는 없습니다. 한 사람이 역할마다 한 건씩 맡습니다(`work.service.next_work`). 생성·수정 요청 모델은 모르는 필드를 422로 거절합니다. 무시하면 200을 받고도 값이 바뀌지 않습니다.

## 의존성

- 부르는 것: `models`(Organization·Project·ProjectMember·User·WorkItem), `services.audit`, `services.authz`, 허용 목록의 `analysis.presets`·`analysis.scene_options`·`caption_qc.settings`·`work.service`(`release_holds`)
- 부르는 쪽: `api/v1/projects.py`·`connections.py`·`workforce.py`

## 남은 일

- `project.py`가 큽니다. 프로젝트 설정과 멤버 관리로 나눌 수 있습니다.
- 프로젝트 slug의 전체 유일성은 DB 제약 없이 코드로만 지킵니다.
- `org_id` 컬럼은 지우지 않습니다. RebornStudio Pro와 같은 스키마를 유지하기 위해서입니다.
- 두 탭에서 같은 설정을 저장하면 늦게 저장한 쪽이 알림 없이 덮어씁니다.
- `guide_ack_at`(작업 가이드 확인 시각)을 쓰는 코드가 없습니다.
