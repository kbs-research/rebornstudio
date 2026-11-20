# api/v1

HTTP 요청을 받아 서비스 계층(`services/`)에 넘기는 라우터 모음입니다. 도메인마다 모듈 하나를 둡니다.
라우터는 요청 스키마 검증, 인증 의존성, 서비스 호출, 응답 직렬화만 합니다.
경로·메서드·요약의 전체 목록은 `docs/api/openapi.json`을 기준으로 봅니다. 엔드포인트 수는 여기에 적지 않습니다. 세는 명령은 `apps/api-gateway/README.md`에 있습니다.

## 주요 파일

| 파일 | 경로 | 역할 |
|---|---|---|
| `auth.py` | `/auth/*` | 로그인, 비밀번호 변경, 재설정 링크 상태 확인·확정, 토큰 갱신(rotation), 로그아웃, 내 정보 |
| `setup.py` | `/setup/*` | 최초 설정. 상태 확인과 첫 super_admin 생성. 셋업 토큰이 필요합니다. 연동 진단은 인증 없는 경로로 열지 않고 플랫폼 개요(`admin.py`)에서 합니다 |
| `admin.py` | `/admin/*` | 계정 생성, 역할·상태 변경, 재설정 링크 발급, 마지막 연동 진단 조회, 연결 상태 집계, 사용자 활동 |
| `connections.py` | `/connections`, `/projects/{project_id}/connection-choices` | 자격증명 연결 목록·생성·수정·삭제. 생성·수정은 저장 직후 점검하고 결과를 함께 돌려줍니다. 프로젝트 관리자용 연결 이름 목록 |
| `projects.py` | `/projects/*` | 프로젝트 생성·조회·수정·soft delete, 멤버 초대·역할·제거, 분석 스택 카탈로그, 분석 대상·저장소 대상의 선택지·저장·점검, MI 분석 자원 진단(읽기 전용), 프로젝트 통계·감사 로그 |
| `users.py` | `/users/*` | 내 프로필 수정, 필수 개인정보 동의, 이전 선택 동의의 철회, 전체 사용자 목록 |
| `videos/` | `/projects/{project_id}/videos`, `/videos/{video_id}/*`, 클립·반입 묶음 | 영상 등록·업로드·준비 단계·클립. 하위 모듈은 `videos/README.md`에 있습니다 |
| `storage.py` | `/projects/{project_id}/storage/*` | 입력한 키 목록 등록(저장소 존재 확인 포함), 폴더 단위 등록(비동기 작업). 버킷 안을 나열하는 라우트는 없습니다 |
| `work.py` | `/projects/{project_id}/work/*`, `/work/*`, `/projects/{project_id}/deident-reports`, `/clips/{clip_id}/deident-report/resolve` | 배정할 수 있는 건수와 내 보유 수, 반려 누적 작업 목록, 다음 작업(`work/next`, 한 사람에 한 건), 제출, 초안, 비식별화 미흡 신고, 비식별화 신고 목록과 처리(재개·제외), 내 작업, 단건·품질검수 상태·라벨 이력·검수 이력 |
| `review.py` | `/projects/{project_id}/review/*`, `/work/{work_item_id}/review*` | 배정할 수 있는 건수와 내 보유 수, 다음 검수(`review/next`, 한 사람에 한 건), 내 검수 작업, 초안, 판정 |
| `exports.py` | `/projects/{project_id}/exports`, `/exports/{export_id}/download`, `/completed*`, `/qc/*` | 납품 패키지 생성·이력·다운로드 링크, 완료 목록·집계·상세, 캡션 품질검수 보류함·해제·재검사·수정 후 재승인 |
| `maiu.py` | `/projects/{project_id}/maiu/*` | MI 요청·재시도, 잡 목록, 비용 통계 |
| `dashboard.py` | `/projects/{project_id}/dashboard/*`, `/pipeline/summary` | 사람별 실적, 처리 능력, 워크플로 건강, 단계별 대기 건수, 사업 KPI |
| `workforce.py` | `/projects/{project_id}/me/*`, `/me/auth-events` | 본인 판정 목록, 본인 로그인 이력, 내 작업 이력, 프로젝트 나가기 |
| `csp_report.py` | `/csp-report` | 브라우저 CSP 위반 보고 수집. 두 보고 형식을 받아 막힌 주소의 출처와 화면 경로만 api 로그에 남깁니다. 주소별 분당 한도(`RATE_LIMIT_CSP_REPORT_PER_MIN`)와 본문 64KiB 상한을 둡니다 |

`auth`·`setup`·`admin`·`projects`·`users`는 `main.py`가 접두사를 붙입니다. 나머지는 라우터가 전체 경로를 선언합니다.

## 인증과 권한

| 단계 | 적용 |
|---|---|
| 인증 없음 | 로그인·재설정 확인과 확정·토큰 갱신·로그아웃, `/setup/*`(초기화 전에만 쓰기 허용, 셋업 토큰 필요), `/csp-report`(브라우저 위반 보고, 주소별 한도와 본문 64KiB 상한). 전체 목록과 이유는 `tests/contract/test_public_routes.py`의 `PUBLIC_ROUTES`에 있고, 그 테스트가 목록을 앱의 라우트와 대조합니다 |
| 로그인만 확인(`get_signed_in_user`) | `/auth/me`, `/users/me/privacy-consent`. 개인정보 동의 전에도 불러야 하는 경로입니다 |
| 로그인과 필수 동의(`get_current_user`) | 그 밖의 업무 API 전부. 동의 기록이 없으면 403 `privacy_consent_required`를 돌려줍니다 |
| super_admin(`require_super_admin`) | `admin.py`, 자격증명 연결 관리, 전체 사용자 목록 |
| 프로젝트 역할 | 라우터나 서비스가 `services/authz.py`(`require_role`·`require_project_admin`·`require_work_result_viewer`·`require_member` 등)로 확인합니다. super_admin은 프로젝트 역할 검사를 통과합니다. 각 경로가 요구하는 역할은 OpenAPI `summary`의 괄호에도 적습니다 |

라우터별 주요 권한은 아래와 같습니다. 경로별 기준은 서비스 코드와 OpenAPI `summary`에 있습니다.

| 라우터 | 권한 |
|---|---|
| `admin.py` | super_admin |
| `connections.py` | 연결 관리는 super_admin, 연결 이름 선택지는 그 프로젝트의 project_admin |
| `projects.py` | 생성과 soft delete는 super_admin. 설정 수정·멤버 관리·분석 대상과 저장소 대상은 project_admin |
| `users.py` | 본인. 전체 사용자 목록은 super_admin |
| `videos/` | 목록·상세·재생 주소·클립 조회는 멤버. 등록·업로드·삭제·준비 단계 재시도·클립 직접 추가는 project_admin. 메타 수정과 반입 묶음 상세·수정은 project_admin |
| `storage.py` | project_admin |
| `work.py` | annotator·reviewer·project_admin. 작업 단건·라벨 이력·검수 이력은 점유자나 검수자. 반려 누적 목록·비식별화 신고 목록·신고 처리는 project_admin |
| `review.py` | reviewer·project_admin |
| `exports.py` | 납품 생성은 project_admin. 완료 목록은 project_admin·reviewer. 품질검수 보류함과 보류 처리는 project_admin |
| `maiu.py` | 분석 요청·잡 목록·비용은 project_admin |
| `dashboard.py` | 대부분 project_admin. 사업 KPI는 project_admin·reviewer |
| `workforce.py` | 본인 기록만 돌려줍니다. 프로젝트 경로는 활성 멤버 |

## 응답 코드

| 상황 | HTTP |
|---|---|
| 토큰 없음·만료·비밀번호 변경 전 발급 | 401 |
| 권한 부족, 비활성 계정, 동의 없음 | 403 |
| 리소스 없음, 가져갈 작업 없음(`no_work_available`) | 404 |
| 이미 처리된 상태, 일시정지·종료된 프로젝트의 새 배정, 잠긴 대상 | 409 |
| 프로젝트가 고른 저장소·분석 연결에서 자격증명을 얻지 못함 | 409 `storage_not_configured`·`analysis_not_configured`(`main.py`의 예외 처리기) |
| 등록 시트의 경로 등록이 프로젝트 버킷이 아닌 버킷을 가리킴 | 422 `storage_bucket_not_project` |
| JSON 본문이 크기 한도를 넘음 | 413(`BodySizeLimitMiddleware`) |

## 설계 이유

- 판정은 서비스에 둡니다. 라우터에 판정을 두면 워커와 배치가 같은 판정을 다시 구현하게 되고, 두 구현의 결과가 달라집니다.
- 오류 `detail`은 snake_case 코드입니다. 화면은 `apps/web/src/shared/api/error_text.ts`의 사전으로 코드를 문구로 바꿉니다. 새 코드를 쓰면 사전에 넣어야 `scripts/ci/check-error-codes.py`를 통과합니다.
- 모든 경로에 `response_model`과 한국어 `summary`를 둡니다. OpenAPI 문서에서 TypeScript 타입을 생성하므로, 응답 모델이 빠지면 화면의 타입도 빠집니다.

## 의존성

- 부르는 것: `api_gateway.dependencies`, `api_gateway.services.*`, `reborn_contracts`(요청·응답 스키마), `api_gateway.models`(주로 `User` 타입)
- 부르는 쪽: `api_gateway/main.py`
- 테스트: 라우터가 부르는 서비스의 도메인 폴더(`tests/<도메인>/`)에 둡니다. 예를 들어 `work.py`·`review.py`는 `tests/work/`, `videos/`·`storage.py`는 `tests/media/`, `dashboard.py`는 `tests/analytics/`에 둡니다. 규칙은 [`tests/README.md`](../../../tests/README.md)에 있습니다.

## 남은 일

- 라우터에서 커밋하는 곳이 남아 있습니다. `admin.py`의 연동 진단, `videos/artifacts.py`의 분석본 단건 정리입니다.
- 오류 코드가 OpenAPI 계약에 실리지 않습니다. 500 응답에는 `X-Request-Id`가 붙지 않습니다.
