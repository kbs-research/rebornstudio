# features/audit

## 목적

감사 기록(`domain_events`)의 이벤트 종류마다 화면 이름·심각도를 정하는 표(`lib/event_label.ts`)와 감사 시각 형식을 둡니다.
감사 기록을 훑어보는 화면은 없습니다. 사용자 활동 이력이 `index.ts`에서 이 표를 가져가 씁니다.

## 주요 파일

| 파일·폴더 | 역할 |
|---|---|
| `index.ts` | 밖으로 내보내는 것: `humanizeEvent`·`eventTone`·`EventTone`·`fmtEventTime` |
| [`lib/`](./lib/README.md) | 이벤트 종류 표 |

## 설계 이유

- 이벤트 표와 시각 형식을 빌려 쓰는 화면(사용자 활동 이력, 비밀번호 재설정)은 각자의 기능에 있고 `index.ts`에서 표를 가져갑니다.
- 서버가 내는 이벤트 종류가 표에 모두 있는지는 `scripts/ci/check-event-labels.py`·`check-event-labels.mjs`가 `lib/event_label.ts`를 읽어 확인합니다.

## 의존성

- 부르는 것: 없음
- 부르는 쪽: 이벤트 표·시각 형식을 쓰는 기능(`features/platform`·`auth`)

## 남은 일

- `fmtEventTime`은 감사 밖의 여러 화면이 쓰는 장부용 시각 형식입니다. 도메인과 무관한 형식이므로 `shared/lib/time`으로 옮길지 정해야 합니다.
