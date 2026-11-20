# worker_io

## 목적

worker-io의 파이썬 패키지입니다. Celery 앱 하나와 그 앱에 등록하는 태스크 패키지가 있습니다.
beat 스케줄과 큐 라우팅도 이 앱이 정합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `__init__.py` | 패키지 표식 |
| `celery_app.py` | Celery 앱, 직렬화·결과 설정, `task_routes`(`reborn_contracts.queues.TASK_ROUTES`), `beat_schedule`, 워커 신호 처리 |
| [`tasks/`](tasks/README.md) | 태스크 패키지. 주기 작업, 잡 투입과 회수, 회신 반영, 품질검수 |

`beat_schedule`의 주기는 다음과 같습니다. 정확한 값은 `celery_app.py`를 기준으로 봅니다.

| 주기 | 태스크 |
|---|---|
| 5초 | MI 분석 결과 회수(`run_maiu_reconciler`), 캡션 품질검수 투입(`dispatch_caption_qc`) |
| 10~60초 | 폴더 등록, 내보내기, DB를 쓰지 않는 작업의 투입(변환·재생본·비식별화 30초, 씬 분할 60초), 클립·영상 썸네일, probe |
| 5~15분 | 작업 점유 회수, 검수 점유 해제, DB를 쓰지 않는 작업의 리스 회수, 원본 정리·체크섬 채우기, 연동·연결 점검 |
| 매일 | 감사 이벤트 정리(03시) |

## 설계 이유

- 주기 태스크가 성공하면 `reborn:beat:<태스크 이름>` Redis 키에 `<시각> <주기 초>`를 씁니다(`_mark_beat_alive`). 관리자 화면 연동 진단의 "워커" 항목이 태스크마다 마지막 성공이 자기 주기의 3배를 넘었는지 판정합니다.
- 워커 노드는 기동할 때와 이벤트 하트비트 때(30초마다) `reborn:node:<노드 역할>`에 시각을 씁니다(`_node_ready`·`_node_alive`). 역할은 노드 이름의 `@` 앞부분(worker·qc)이고, 연동 진단의 "워커 프로세스" 항목이 읽습니다.
  성공만 기록하므로 계속 실패하는 태스크는 정상으로 보이지 않습니다.
- prefork 자식 프로세스는 시작할 때 모듈 전역 DB 엔진의 풀을 닫지 않고 버립니다(`_dispose_inherited_pool`, `Engine.dispose(close=False)`).
  부모의 커넥션 소켓을 여러 자식이 함께 쓰면 asyncpg가 `another operation is in progress`로 실패하고, 분석 결과 회수 작업이 매 주기 실패합니다.
  자식이 소켓을 정상 종료하면 부모가 쓰는 연결까지 끊기므로 닫지 않습니다.
- 시간대는 `Asia/Seoul`입니다. crontab 주기(02시·03시 등)는 운영 시간대 기준입니다.
- httpx가 요청마다 남기는 INFO 로그를 끕니다. MI 폴링이 같은 줄을 반복해서 단계 전환·실패 로그가 묻히기 때문입니다.

## 의존성

- 이 폴더가 부르는 것: `celery`, `redis`, `reborn_contracts.queues`, `api_gateway.database`(신호 처리)
- 부르는 쪽: `celery -A worker_io.celery_app`(compose 서비스 worker(beat 포함)·worker-qc), `../tests`

## 남은 일

- 없음.
