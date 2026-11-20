# features/audit/lib

## 목적

감사 기록의 이벤트 종류 표를 둡니다. 화면 파일 밖에 두어서 렌더 없이 테스트합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `event_label.ts` | 이벤트 종류(`event_type`)마다 화면 이름·심각도를 한 줄씩 적은 표 `EVENTS`, 도메인 이름 `DOMAIN_KO`, 이름·심각도 함수(`humanizeEvent`·`eventTone`), 감사 시각 형식 `fmtEventTime`(`2026-09-23 19:12:14`) |
| `event_label.test.ts` | 심각도·이름·시각 형식 테스트 |

## 설계 이유

- 이벤트 종류마다 한 줄씩 적습니다. 도메인 사전과 동작 사전을 조합하거나 이름 정규식으로 심각도를 추측하면, 같은 낱말이 든 다른 이벤트의 심각도가 틀립니다.
- 감사 기록과 서버 로그는 줄을 맞춰 대조하는 장부입니다. 그래서 같은 시각 형식(`fmtEventTime`)을 씁니다. 이 형식은 다른 화면의 날짜 표기(`shared/lib/time`)와 다릅니다.

## 의존성

- 없음
- 부르는 쪽: `../index.ts`를 거쳐 다른 기능, `scripts/ci/check-event-labels.*`

## 남은 일

- 없음.
