"""reborn-transcode 패키지. DB 없이 도는 워커의 미디어 처리를 담는다.

FFmpeg 트랜스코딩·probe, 검은 화면 검사, 산출 검사, 비식별화·씬 분할 러너를 제공한다.
api_gateway에 의존하지 않는다.
"""
from reborn_transcode.blackness import is_black_video, sample_luma
from reborn_transcode.deident import run_deident_job  # noqa: F401
from reborn_transcode.job_runner import JobError, run_job
from reborn_transcode.output_check import DURATION_TOLERANCE_S, OutputCheck, check_output
from reborn_transcode.probe import ProbeError, ProbeResult, ProbeTimeoutError, probe
from reborn_transcode.transcode import (
    TargetSpec,
    TranscodeError,
    build_h264_command,
    transcode_streaming,
)

__all__ = [
    "run_deident_job",
    "is_black_video",
    "sample_luma",
    "JobError",
    "run_job",
    "DURATION_TOLERANCE_S",
    "OutputCheck",
    "check_output",
    "ProbeError",
    "ProbeResult",
    "ProbeTimeoutError",
    "probe",
    "TargetSpec",
    "TranscodeError",
    "build_h264_command",
    "transcode_streaming",
]
