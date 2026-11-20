"""분석 작업 감사 이벤트의 대상 id가 분석 대상을 가리키는지 확인하는 테스트.

잡 번호(BIGSERIAL)를 UUID로 바꾼 값은 앞자리가 모두 `00000000`이다. 그래서 감사 로그의 짧은
대상 ID로 잡을 구분할 수 없다. 대상은 잡이 분석하는 클립 또는 영상의 id다.
"""
from uuid import uuid4

from api_gateway.models import AnalysisJob
from api_gateway.services.maiu.reconciler.common import _job_target


def test_job_target_is_the_analysed_video_or_clip():
    """영상 단위 잡은 영상 id, 클립 단위 잡은 클립 id가 대상이다. 잡 번호로 만든 값을 쓰지 않는다."""
    v1, v2, clip = uuid4(), uuid4(), uuid4()
    video_job = AnalysisJob(id=62, video_id=v1, clip_id=None)
    other_job = AnalysisJob(id=63, video_id=v2, clip_id=None)
    clip_job = AnalysisJob(id=64, video_id=v1, clip_id=clip)
    assert _job_target(video_job) == v1
    assert _job_target(clip_job) == clip
    assert _job_target(other_job) == v2
