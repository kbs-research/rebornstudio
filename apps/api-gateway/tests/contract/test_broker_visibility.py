"""두 Celery 앱의 브로커 재전달 시간이 같고 잡 URL 유효 기간보다 긴지 확인한다.

확인 응답이 없는 메시지를 되돌리는 일은 같은 Redis에 붙은 모든 워커가 자기 설정값으로 한다.
한 앱만 값을 늘리면 다른 앱이 기본값(3,600초)으로 긴 잡을 다시 전달해 같은 토큰의 잡이 두 번 돈다.
worker-io와 worker-jobs는 api-gateway가 의존하지 않는 별도 패키지라 없으면 건너뛴다.
"""
import pytest
from api_gateway.services.media import scene_split, transcode


def test_both_apps_share_a_visibility_timeout_longer_than_job_urls():
    """두 앱의 `visibility_timeout`이 같고 모든 잡 URL 유효 기간 이상인지 확인한다."""
    io_app = pytest.importorskip("worker_io.celery_app").app
    jobs_app = pytest.importorskip("worker_jobs.celery_app").app
    io_value = (io_app.conf.broker_transport_options or {}).get("visibility_timeout")
    jobs_value = (jobs_app.conf.broker_transport_options or {}).get("visibility_timeout")
    assert io_value is not None, "worker-io 앱이 visibility_timeout을 정하지 않았습니다"
    assert io_value == jobs_value
    assert io_value >= transcode._JOB_URL_TTL_S
    assert io_value >= scene_split.JOB_URL_TTL_S
