# tests/workforce

작업자 운영 도메인(`services/workforce`)과 멤버 운영 테스트입니다. 본인 판정 목록, 초대 가드, 다음 작업 배정, 나가기·멤버 제거, 보유 해제를 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_my_decisions.py` | 본인 판정 목록(`me/decisions`)이 최신순으로 오고, 다른 프로젝트 판정을 빼고, 커서가 빠짐없이 이어지며, 비멤버는 403인지 확인합니다. |
| `test_member_invite_guards.py` | 초대 실패가 없는 계정과 정지 계정을 같은 응답으로 묶는지, 초대에 사람별 분당 한도가 있는지 확인합니다. |
| `test_workforce.py` | 작업자 운영을 검증합니다. 다음 작업이 한 사람에 한 건만 맡기고 이전 버전의 한도 값을 읽지 않는지, 마지막 관리자의 나가기 가드, 멤버 제거 뒤 작업이 큐로 돌아가는지 봅니다. |
| `test_hold_release.py` | 멤버 제거·나가기·계정 정지가 라벨링과 검수 보유를 함께 풀고 푼 작업을 감사 기록에 남기는지 확인합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
작업자 본인 집계는 `analytics/test_stats_cache_annotator.py`가 같이 봅니다.
본인 로그인 이력은 `auth/test_auth_events.py`가 봅니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
