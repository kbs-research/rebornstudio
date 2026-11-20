# services/work/service

라벨링 작업 큐의 핵심입니다. work_item의 생성·닫기, 다음 작업 배정(한 사람에 한 건)·제출·임시저장·신고, 대기 수·보유 수,
내 보유 목록과 내 라벨링 기록을 맡습니다. 판정 규칙·쓰기·조회·변환을 하위 모듈로 나눈 패키지입니다.

## 주요 파일

| 파일 | 역할 | 들어 있는 것 |
|---|---|---|
| `__init__.py` | 파사드 | 하위 모듈의 이름(비공개 `_` 이름 포함)을 모두 다시 내보냅니다. 부르는 쪽은 `from api_gateway.services.work import service as work_service`를 그대로 씁니다 |
| `classify.py` | 판정 규칙 | 사람 큐 대상(`_human_workable`) · 닫을 수 있는 상태(`_CLOSABLE`) · 새로 배정할 수 있는 조건(`_claimable_where`) · 호출한 사용자의 보유(`_held_by`) · 새 배정 가능 여부(`reject_if_not_claimable`) · 보유 순서(`held_order`·`HELD_ORDER`) · 점유한 사람이 프로젝트를 떠났는지 여부(`holder_left`, 반려가 재작업을 돌려줄 곳을 정할 때 씀) · 관리자 확인 대상(`needs_admin_check_where`, 기준 `REPEATED_REJECTS`) |
| `dto.py` | 변환 | `WorkItem` → `WorkItemDTO`(`_to_dto`) |
| `commands.py` | 쓰기 | 작업 생성·되살림(`ensure_for_clip`·`ensure_for_video`) · 닫기와 그 수(`close_items`·`count_closable`) · 영상 티켓 정리(`retire_video_ticket`) · `next_work`(보유한 작업이 있으면 그 작업, 없으면 한 건 배정) · 일할 수 없게 된 사람의 보유 풀기(`release_holds`·`released_payload`, 멤버 제거·나가기·계정 정지가 부름)·`submit`·`save_draft`·`report_deident_miss`(보유를 풂) · 다음 작업 응답 변환(`_next_dto`, 검수도 씀) · 행 잠금(`lock_item`) · 보유자·역할 확인(`_load_my_claim`·`_load_working_claim`·`_require_work_viewer`·`_work_view_scope`·`_require_annotator`) |
| `queries.py` | 조회 | 대기 수·보유 수(`count_claimable`·`claimable_summary`) · 내 보유 목록(`list_mine`, 재작업의 마지막 반려 사유 포함, 최신 제출 스냅숏은 내 제출일 때만) · 작업 한 건(`get_one`) · 라벨 체인(`list_labels`) · 최근 품질 검사(`latest_qc`) |
| `history.py` | 조회(내 기록) | 내 라벨링 기록 목록·상세(`list_my_history`·`my_history_detail`)와 그 조회식(`_history_query`·`_history_rows`) |

## 설계 이유

- 판정 규칙은 `classify` 한 곳에 둡니다. 쓰기·조회와 다른 도메인은 이 규칙을 가져다 쓰고 다시 구현하지 않습니다. 다른 도메인의 예는 `analytics.dashboard`의
  `_claimable_where`, `media.video`·`work.deident_reports`의 `_CLOSABLE`, `work.review`의 `_human_workable`·
  `held_order`·`reject_if_not_claimable`입니다.
- `count_closable`은 `close_items`와 같은 `commands`에 둡니다. 확인 창이 세는 수와 실제로 닫히는 수가
  같아야 하므로, 두 함수가 같은 `_CLOSABLE`을 쓰도록 나란히 둡니다.
- 보유에는 끝나는 시각도 자동 회수도 없습니다. 보유는 제출, 비식별화 미흡 신고, 멤버 제거·나가기와
  계정 정지(`release_holds`, 라벨링·검수 둘 다)로만 풀립니다. 다음 작업은 보유한 작업을 먼저 다시 엽니다.
- 보유자·역할 확인은 `commands`에 둡니다. 쓰기 경로가 모두 이 확인을 거칩니다. 조회(`get_one`·`list_labels`)와
  `work.review`는 `_work_view_scope`를 가져다 씁니다. 작업을 넘겨받은 보유자는 자기 제출과 그 판정만 봅니다.
- 같은 작업을 바꾸는 요청은 `lock_item`으로 행을 잠급니다. 제출, 검수 판정이 같은 잠금을 씁니다. 그래서
  늦은 요청은 먼저 끝난 요청이 바꾼 상태를 보고 판정합니다.
- 새 코드는 하위 모듈 경로와 파사드(`__init__.py`) 중 어느 쪽으로 가져와도 됩니다.

### 의존 방향

```
classify · dto          ← 같은 패키지의 다른 모듈을 import하지 않는다
   ↑
   commands             ← classify·dto
   ↑
queries                 ← classify·dto·commands(_work_view_scope)
history                 ← dto
__init__                ← 전부(파사드)
```

반대 방향 import는 두지 않습니다. 순환이 생기면 이 표를 먼저 확인합니다.

## 의존성

- 부르는 것: `models`(WorkItem·Clip·Video·Label·Review·Project·ProjectMember·DomainEvent),
  `services.{audit,authz,caption,reject_reasons}`, `work.edit_rate`
- 다른 도메인: `caption_qc`의 DTO 변환과 검사 상태. `queries.latest_qc`가 `caption_qc.backlog.state_of`를 부릅니다.
  제출(`commands.submit`)은 품질검수를 부르지 않습니다. 커밋된 라벨 행을 워커가 읽어 검사합니다.
  import-linter 허용 줄은 가져오는 하위 모듈 이름(`work.service.queries`)으로 적혀 있습니다.
- 부르는 쪽: `api/v1/work.py`·`workforce.py`, `work.{review,deident_reports,repeated_rejects}`, `analytics.dashboard`, `org.project`·`auth.admin`(`release_holds`),
  `media.video`, `maiu.{service,reconciler}`, `scripts/dev/seed_demo.py`
