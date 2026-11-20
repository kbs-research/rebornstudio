# services/ops

플랫폼 운영 도메인입니다. 플랫폼이 제대로 동작하는지 점검하고, 감사 로그를 조회하고 보존합니다. 업무 도메인(작업·미디어·분석)이 아니라 운영자가 플랫폼을 관리하는 일을 다룹니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `diagnostics.py` | 연동 진단. DB·Redis·저장소 연결·분석 연결·주기 태스크(태스크마다 주기의 3배)·워커 세 종류의 동작 기록·브로커 큐 길이(판정 없이 수만)·DB 백업 하트비트·분석 호출 분배 카운터·TLS를 점검합니다(`run`). 결과를 스냅숏으로 기록하고, 상태가 바뀐 항목은 `system.integration_changed` 이벤트로 남깁니다(`record`·`run_and_record`). 연동 예외를 사유 문장으로 바꾸는 `exception_reason`과 점검 결과 모양(`Check`)은 다른 도메인도 씁니다 |
| `audit_log.py` | 플랫폼 감사 로그(`domain_events`)에서 한 사용자가 행위자인 이벤트를 읽는 사용자 활동 이력 조회(`user_activity`) |
| `csp_report.py` | 브라우저 CSP 위반 보고를 읽어 api 로그에 경고로 남깁니다(로거 `reborn.csp`). `application/csp-report`·`application/reports+json` 두 형식을 받고, 막힌 주소는 출처만, 화면은 경로만 남겨 presigned 서명이 로그에 남지 않게 합니다. 같은 위반은 수로 묶고 요청 하나에 20줄까지 남깁니다 |
| `audit_retention.py` | 감사 로그 보존 기간 적용. 보존 기간은 3년(1,095일)으로 고정하고, `domain_events`를 지우는 유일한 경로입니다 |

## 설계 이유

- 이 시스템의 고장은 대개 500 오류가 아니라 동작하는 것처럼 보이는 상태로 나타납니다. 연결 없음, Redis 대체, 워커 중단, 백업 중단, 평문 전송이 그런 예입니다. 그래서 진단은 화면이 정상으로 보이는 동안에도 실제 상태를 점검합니다. 진단은 비용이 크므로 주기 태스크(`check_integrations`)만 부르고, 플랫폼 개요는 그 결과를 읽습니다. 요청이 있어야 판정하는 클라이언트 주소·TLS는 개요를 연 요청으로 다시 판정합니다(`with_request_checks`).
- 저장소·분석 점검은 등록한 연결 행의 수와 마지막 점검 결과만 셉니다. 실제 접속은 연결 주기 점검(`stacks.connections.recheck_all`)과 프로젝트 대상 점검이 합니다. 진단이 다시 접속하면 같은 판정을 두 곳이 따로 내고, 어느 프로젝트에도 속하지 않은 버킷에 쓰게 됩니다.
- 감사 로그 보존 기간 3년은 개인정보 처리방침에 고지한 값이고, 설정으로 바꾸지 않습니다. 감사 로그는 저장 공간 때문이 아니라 고지한 약속 때문에 지웁니다.
- 이 패키지는 업무 도메인을 거의 import하지 않습니다. 다른 도메인을 읽는 곳은 `diagnostics`의 `maiu.slot_counter` 하나이고, 허용 목록에 이유와 함께 적었습니다.

## 의존성

- 부르는 것: `models`(DomainEvent·Connection·User), `api_gateway.config`·`redis_infra`, `services.audit`·`platform_credentials`, 허용 목록의 `maiu.slot_counter`
- 부르는 쪽: `api/v1/admin.py`(진단·감사 로그), `maiu.provisioning`·`stacks.connections`·`stacks.project_stack`·`stacks.project_storage`(`Check`·`exception_reason`), `workers/worker-io`의 `check_integrations`·`purge_audit_events`

## 한계

- 감사 로그(`domain_events`)에 요청 ID가 없습니다. 그래서 사용자 행동 한 번이 만든 여러 이벤트를 묶을 수 없습니다.
