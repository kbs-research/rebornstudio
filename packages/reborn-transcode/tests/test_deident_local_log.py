"""로컬 비식별화의 입력 다운로드 실패 로그가 presigned 서명을 남기지 않는지 보는 테스트.

입력 주소는 비식별화 전 원본을 읽는 presigned URL이다. 서명은 만료 전까지 원본 접근 권한과 같다.
httpx 예외의 repr에는 요청 주소 전체가 들어 있어 그대로 로그에 쓰면 워커 로그에 서명이 남는다.
"""
from __future__ import annotations

import contextlib
import logging

import httpx
import pytest
from reborn_transcode import deident
from reborn_transcode.job_runner import JobError

_URL = (
    "https://storage.example.com/sample-bucket/intake/v1/source"
    "?X-Amz-Credential=AKEXAMPLE&X-Amz-Signature=deadbeefsignature"
)


def test_download_failure_log_has_no_presigned_signature(monkeypatch, caplog):
    """입력 다운로드가 403으로 실패해도 로그에 서명과 자격증명 쿼리가 없는지 확인한다."""
    monkeypatch.setenv("DEIDENT_MODEL", "passthrough")

    @contextlib.contextmanager
    def failing_stream(method, url, **_kw):
        """403 응답을 돌려준다. 다운로드 코드의 `raise_for_status`가 주소를 담은 오류를 만든다."""
        yield httpx.Response(403, request=httpx.Request(method, url))

    monkeypatch.setattr(deident.httpx, "stream", failing_stream)
    with caplog.at_level(logging.WARNING), pytest.raises(JobError):
        deident.run_deident_job({"input_url": _URL, "outputs": []})
    text = caplog.text
    assert "입력 다운로드 실패" in text
    assert "deadbeefsignature" not in text and "AKEXAMPLE" not in text
