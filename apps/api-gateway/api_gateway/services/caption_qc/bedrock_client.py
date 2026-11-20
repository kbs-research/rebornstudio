"""Bedrock boto3 클라이언트를 만든다. 검사 호출(`adapters`)이 쓴다.

botocore 기본값(연결 60초, legacy 재시도 4회)이면 막힌 네트워크에서 검사 호출이 몇 분씩 멈추므로
시한과 재시도를 이 모듈 한 곳에서 정한다.
연결 점검 프로브(`stacks.connections`의 Bedrock 점검)는 더 짧은 시한을 따로 쓴다.

키가 없으면 클라이언트를 만들지 않는다. 키 없이 만들면 boto3가 기본 자격증명 체인으로 넘어가
서버 인스턴스 메타데이터의 역할 자격증명으로 모델을 부른다.
"""
from __future__ import annotations

from typing import Final

from botocore.config import Config

#: 연결 시한(초). 정상 엔드포인트는 1초 안에 연결한다.
CONNECT_TIMEOUT_S: Final = 5
#: 읽기 시한(초). 검사 한 번의 생성 응답(최대 2,000토큰)이 이 안에 온다.
READ_TIMEOUT_S: Final = 60
#: 첫 시도 뒤 다시 보내는 횟수. standard 재시도는 스로틀·5xx·연결 오류만 다시 보낸다.
RETRIES: Final = 2


class BedrockKeysMissingError(ValueError):
    """액세스 키나 비밀 키가 없어 클라이언트를 만들지 않은 경우."""


def config() -> Config:
    """Bedrock 클라이언트 설정을 만든다."""
    return Config(
        connect_timeout=CONNECT_TIMEOUT_S,
        read_timeout=READ_TIMEOUT_S,
        retries={"max_attempts": RETRIES, "mode": "standard"},
    )


def make(service: str, region: str, access_key: str, secret_key: str):
    """연결에 저장한 키로 Bedrock 클라이언트를 만든다.

    Args:
        service: `bedrock-runtime`(생성 호출) 같은 boto3 서비스 이름.
        region: 호출할 리전.
        access_key: 액세스 키.
        secret_key: 비밀 키.

    Raises:
        BedrockKeysMissingError: 두 키 가운데 하나라도 비어 있는 경우.
    """
    import boto3

    if not access_key or not secret_key:
        raise BedrockKeysMissingError(service)
    return boto3.client(
        service, region_name=region, config=config(),
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )
