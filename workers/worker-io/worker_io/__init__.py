"""worker-io 패키지. Celery 워커와 beat가 실행하는 태스크를 담는다.

대부분의 태스크는 api_gateway 서비스 함수를 감싸는 얇은 래퍼다.
예를 들어 MAIU reconciler 로직은 `api_gateway.services.maiu.reconciler.run_once`에 있고
이 워커는 그 함수를 5초마다 호출한다.
DB 없이 도는 잡(트랜스코딩·publish·비식별화·씬 분할)은 `workers/worker-jobs`가 실행한다.
이 패키지는 그 잡을 이름으로 보내고 회신을 DB에 반영한다.
"""
