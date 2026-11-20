# services/work

작업 단계 도메인입니다. work_item의 생애주기를 맡습니다. work_item은 라벨링, 검수 순서로 진행됩니다.
이 단계들은 화면의 작업 단계(라벨링·검수)와 대응합니다. 클립이 정상 상태(`auto_cleared`)로 생기므로 분석이 끝나면 바로 라벨링 작업이 만들어집니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `service/` | 라벨링 작업 큐의 핵심. 작업 생성·닫기, 다음 작업·제출·초안·신고, 대기 수와 보유 수, 내 보유 목록과 기록([README](service/README.md)) |
| `review.py` | 검수. 큐, 다음 검수·초안, 판정(승인·반려·수정 후 승인), 작업의 검수 이력 |
| `deident_reports.py` | 비식별화 미흡 신고로 멈춘 클립의 목록(`list_for_project`, 클립마다 가장 최근 신고)과 처리(`resolve`). 재개하면 클립을 `auto_cleared`로 되돌려 작업이 대기열로 돌아가고, 제외하면 클립을 `rejected`로 바꾸고 미완 작업을 닫습니다(`close_items`, 사유 `deident_excluded`) |
| `repeated_rejects.py` | 반려가 기준 횟수(`classify.REPEATED_REJECTS`) 이상 쌓인 미완 작업 목록. 마지막 반려 사유와 보유자를 함께 싣습니다 |
| `edit_rate.py` | 수정률의 재료. 라벨러가 받은 AI 초안, 정규화, 글자 단위 편집 거리. 작업의 첫 제출에서 한 번 재서 라벨 행에 남깁니다 |

## 설계 이유

- 두 역할(라벨러·검수자)은 다음 작업 버튼 하나로 일합니다. 서버가 역할마다 한 건만 맡기고, 보유한 작업이 있으면 새로 배정하지 않고 그 작업을 다시 돌려줍니다(`service.next_work`·`review.next_review`). 배정은 배타 점유(`FOR UPDATE SKIP LOCKED`)입니다. 점유 컬럼은 라벨링 `work_items.claimed_by`, 검수 `work_items.review_claimed_by`로 나뉘어 있어 서로 간섭하지 않습니다.
- 보유에는 끝나는 시각, 동시 보유 한도, 반납, 자동 회수가 없습니다. 보유는 제출·판정, 비식별화 미흡 신고, 멤버 제거·나가기와 계정 정지(라벨링·검수 둘 다, `service.release_holds`)로 풀립니다. 푼 작업의 ID와 수는 그 이벤트(`member.removed`·`member.left`·`user.status_changed`)에 남습니다. 한 사람이 한 건씩 맡으므로 큐를 통째로 잡을 수 없고, 반납이 없으므로 받은 건을 끝내야 다음 건이 열립니다.
- 한 행을 바꾸는 요청은 그 행을 잠그고 상태를 다시 확인합니다. 제출·초안(`service.lock_item`), 검수 판정·초안(`review._lock_item`), 비식별화 신고 처리(클립 행 잠금)가 이렇게 동작합니다. 늦은 요청은 먼저 끝난 요청이 바꾼 상태를 보고 409로 끝납니다. 같은 사람의 다음 작업 요청은 멤버 행 잠금으로 순서를 정하므로 두 번 눌러도 두 건이 배정되지 않습니다.
- 도구는 제출량·승인율·반려율·수정률을 보여 주고, 사람을 평가하지 않습니다.
- 보유 목록의 순서는 `service.held_order` 한 곳에서 정합니다. 재작업이 먼저 오고, 그다음은 가져온 순입니다. 다음 작업이 다시 여는 작업과 내 목록이 이 순서를 그대로 씁니다. 반려된 재작업이 원 작업자에게 돌아오면 그 사람은 잠시 두 건을 보유하고, 다음 작업이 재작업을 먼저 엽니다.
- 검수자는 자기가 라벨링한 작업을 가져갈 수 없습니다(`review._review_claimable_where`).
- 반려된 작업은 횟수와 관계없이 만든 사람에게 재작업으로 돌아갑니다. 만든 사람이 프로젝트를 떠났으면 대기열로 돌아갑니다. 반려가 기준 횟수 이상 쌓인 미완 작업은 관리자 확인 목록(`repeated_rejects`)에 나타날 뿐 흐름은 바꾸지 않습니다. 관리자가 가이드나 클립을 살펴볼 대상입니다.
- 비식별화 신고를 제외로 처리해 작업을 닫을 때 작업 행을 지우지 않습니다. 행을 지우면 라벨·검수 이력이 함께 사라져 사람 지표가 바뀌기 때문입니다. 닫힌 작업의 클립이 다시 허가 상태가 되면 `ensure_for_clip`이 닫힌 작업을 반려 0회로 되살립니다. 행을 지우는 경우는 클립이 없는 영상 단위 티켓뿐입니다.
- 비식별화 미흡 신고(`service.report_deident_miss`)는 라벨링 보유자와 검수 보유자가 올립니다. 신고하면 클립이 `legal_review`가 되고 점유가 풀리며(`work.released`·`review.released`, `cause=deident_miss_reported`), 작업은 `_human_workable`이 클립 상태를 보고 큐에서 뺍니다. 신고 내용은 감사 이벤트(`clip.deident_miss_reported`)에만 남고, 처리 결과는 `clip.deident_report_resumed`·`clip.deident_report_excluded`로 남습니다.
- 신고로 멈춘 상태는 work_item 상태가 아니라 클립(`clips.rights_status`)에 있습니다. 그래서 파이프라인 집계는 신고 건수(`deident_reports`)를 따로 셉니다.

## 의존성

- 부르는 것: `models`(WorkItem·Label·Review·Clip·Video·DomainEvent·Project·ProjectMember), `services.audit`·`authz`·`caption`·`reject_reasons`, 허용 목록의 `media.clip`(`deident_reports`)·`caption_qc.backlog`
- 부르는 쪽: `api/v1/work.py`·`review.py`·`workforce.py`, `maiu`(분석 뒤 작업 생성), `media.video`, `analytics.dashboard`, `org.project`·`auth.admin`(보유 풀기)
