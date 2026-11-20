# tests/org

조직·프로젝트 도메인(`services/org`) 테스트입니다. 조직이 기본 조직 한 행뿐인지, 프로젝트 CRUD, 생성 권한, 설정 PATCH와 역할별 설정 가리기, 멤버 개인정보 가리기, 삭제한 프로젝트 가드, 멤버 초대와 역할을 봅니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `test_deleted_project_guard.py` | 삭제한 프로젝트를 id로 조회·수정할 수 없는지(최고관리자 포함 404) 확인합니다. |
| `test_member_audit_targets.py` | 멤버 감사 기록의 대상이 그 사람인지, 바뀐 것 없는 수정은 기록하지 않고 품질검수 설정 변경은 기록하는지, 지난 계약 마감일 거절을 확인합니다. |
| `test_member_pii_by_role.py` | 멤버 목록의 연락처·개인정보가 관리자에게만 실리는지 확인합니다. |
| `test_project_delete_super_admin.py` | 프로젝트 삭제는 최고관리자만 하고 프로젝트 관리자는 403인지 검증합니다. |
| `test_project_create_guard.py` | POST /projects 권한 가드를 검증합니다. super_admin만 만들 수 있습니다. |
| `test_project_update.py` | 프로젝트 설정 수정 API(PATCH /projects/{id})를 검증합니다. |
| `test_fixed_options.py` | 처리 옵션이 고정 기본값인지 검증합니다. 수정·생성 요청에 고정 옵션(보유 만료 시간·캡션 최소 길이·클립 길이·변환·반려 사유·품질검수 프롬프트 등)을 실으면 422이고 아무것도 바뀌지 않는지, 바꿀 수 있는 필드는 저장되는지, 새 프로젝트가 기본 클립 길이(MEDIUM)와 기본 반려 사유를 갖는지, 분석 요청에 클립 길이를 실으면 422인지 확인합니다. |
| `test_project_settings_by_role.py` | 프로젝트 응답의 설정 값(`settings`·품질검수 세부·비식별화 연결 이름)이 관리자·최고관리자에게만 그대로 실리고 그 밖의 역할에게는 가려지는지 목록·상세 둘 다 확인합니다. |
| `test_projects.py` | 프로젝트 CRUD와 멤버 초대 흐름(초대·나간 멤버 재초대·남은 거절·대기 행 되살리기·정지 계정 초대 거절), 멤버 제거와 역할 변경 가드, 멤버 목록 상한을 검증합니다. |
| `test_single_org.py` | 조직 행을 하나 더 넣으면 유일 인덱스(`uq_organizations_single`)가 거절하는지, `/organizations` 경로가 404인지, 조직 없이 만든 프로젝트가 기본 조직에 속하는지 확인합니다. |
| `test_three_roles.py` | 없어진 역할(staff·operator·viewer·rights_reviewer·legal_reviewer)을 DB 제약이 IntegrityError로, 초대·역할 변경 API가 422로 거절하는지 확인합니다. |
| `test_role_settings_gate.py` | 일반 설정 변경을 검수자·라벨러는 403으로 거절하고 멤버인 프로젝트 관리자는 감사 기록과 함께 바꾸는지 검사합니다. |

## 설계 이유

폴더 규칙과 둘 이상에 걸친 파일의 위치는 [`../README.md`](../README.md)에 있습니다.
고정 기본값인 처리 옵션의 동작은 각 도메인 테스트가 `tests/_helpers.py`의 `set_project_options`로 값을 DB에 직접 써서 확인합니다.

## 의존성

- 픽스처(`client`·`admin_token`)는 상위 `tests/conftest.py`, 시드 헬퍼는 `tests/_helpers.py`에서 옵니다.
- 모듈 이름은 폴더가 달라도 전체에서 유일해야 합니다. 하위 폴더에 `__init__.py`를 두지 않기 때문입니다.

## 남은 일

- 없음. 파일을 더하면 위 표에 한 줄을 더합니다.
