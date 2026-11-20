# features/platform/lib

## 목적

플랫폼 화면의 순수 규칙과 표기를 둡니다. 화면 파일 밖에 두어서 렌더 없이 테스트하고, 두 화면이 같은 서비스와 상태를 같은 이름으로 부르게 합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `connection_provider.ts` | 자격증명 연결의 서비스 이름(`PROVIDER_LABEL`)과 묶음 판정(`isModelProvider`·`providerGroupLabel`). 연결 화면과 플랫폼 개요가 씁니다 |
| `connection_address.ts` | 연결 편집이 키를 다시 받아야 하는지의 판정(`ROUTING_KEYS`·`routingChanges`·`secretRequired`)과 허용 저장소 입력란의 글자 변환(`parseBucketList`·`bucketListText`). 서버 `connections.secret_required`와 같은 답을 냅니다 |
| `connection_address.test.ts` | 판정 표 `packages/reborn-contracts/connection-address.json`을 서버 테스트와 함께 읽어 같은 답인지, 편집 창의 키 칸 필수 표시가 주소를 고칠 때만 켜지는지 확인하는 테스트 |
| `connection_health.ts` | 플랫폼 개요의 자격증명 연결 한 줄(`connectionHealthLine`, 문구와 색)과 연결 화면 주소(`connectionsHref`) |
| `connection_health.test.ts` | 쓰지 않는 연결이 실패해도 경보를 내지 않는지 확인하는 테스트 |
| `analysis_off.ts` | 분석 연동이 꺼진 서버의 점검 안내를 한 문구(`ANALYSIS_OFF_HINT`, 서버 설정 `MAIU_ENABLED`와 최고관리자)로 맞추는 변환(`analysisOffText`). 연동 진단 목록, 플랫폼 개요의 조치 필요, 연결 저장 직후 점검 결과가 씁니다 |
| `analysis_off.test.ts` | 세 점검의 서로 다른 서버 문장이 같은 안내로 바뀌고 다른 결과는 그대로인지 확인하는 테스트 |
| `in_use_projects.ts` | 409 사용 중 거절을 막은 프로젝트를 `사용 중인 프로젝트: A, B 외 N개` 꼴의 한 줄로 만드는 `inUseProjectsLine`. 서버가 싣지 않은 나머지 프로젝트를 수로 적습니다 |
| `in_use_projects.test.ts` | 이름 잇기와 나머지 수 표기를 확인하는 테스트 |

## 설계 이유

- 연결 편집의 키 재입력 판정은 서버와 화면이 따로 갖습니다. 브라우저가 파이썬을 돌릴 수 없어 함수를 합칠 수 없으므로, 입력과 기대값을 JSON 판정 표에 두고 양쪽 테스트가 같은 표를 읽습니다. 화면이 더 느슨하면 저장을 누른 뒤에야 서버의 422를 봅니다.
- 연결 상태(실패·주의·점검 전)는 사용처가 있는 연결만 셉니다. 서버가 그렇게 세어서 주고(`ConnectionHealthSummary`), 화면은 사용처가 없는 연결의 수만 표시하고 색을 주지 않습니다.

## 의존성

- `@reborn/contracts`, `shared/api/client`(타입)
- 부르는 쪽: `../pages/*`

## 남은 일

- 없음.
