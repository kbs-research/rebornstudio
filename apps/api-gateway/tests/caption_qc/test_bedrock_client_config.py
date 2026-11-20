"""Bedrock 클라이언트의 시한·재시도 설정을 확인한다.

기본값(연결 60초, legacy 재시도)이면 막힌 네트워크에서 검사 호출이 몇 분씩 멈춘다.
검사 호출이 시한을 실은 설정으로 클라이언트를 만드는지, 키 없이는 클라이언트를 만들지 않는지 본다.
네트워크는 쓰지 않는다.
"""
import pytest
from api_gateway.services.caption_qc import adapters, bedrock_client


def test_client_carries_timeouts():
    """만든 클라이언트에 연결 5초·읽기 60초·standard 재시도가 실리는지 확인한다."""
    cfg = bedrock_client.make("bedrock-runtime", "us-east-1", "ak", "sk").meta.config
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 60
    assert cfg.retries == {"total_max_attempts": 3, "mode": "standard"}


@pytest.mark.parametrize(("access", "secret"), [("", ""), ("ak", ""), ("", "sk")])
def test_client_needs_both_keys(access: str, secret: str):
    """키가 하나라도 없으면 클라이언트를 만들지 않는지 확인한다.

    키 없이 만들면 boto3가 기본 자격증명 체인으로 넘어가 서버 인스턴스의 역할 자격증명을 쓴다.
    """
    with pytest.raises(bedrock_client.BedrockKeysMissingError):
        bedrock_client.make("bedrock", "us-east-1", access, secret)


def _capture(monkeypatch) -> list[dict]:
    """`boto3.client`에 넘어간 인자를 기록하고 빈 응답 대역을 돌려주게 한다."""
    seen: list[dict] = []

    class _Client:
        """빈 응답을 돌려주는 Bedrock 대역."""

        def converse(self, **_):
            """빈 생성 응답."""
            return {"output": {"message": {"content": [{"text": "[]"}]}}}

    def fake(service, **kwargs):
        """인자를 기록한다."""
        seen.append({"service": service, **kwargs})
        return _Client()

    monkeypatch.setattr("boto3.client", fake)
    return seen


def test_converse_uses_the_shared_config(monkeypatch):
    """검사 호출이 시한을 실은 설정으로 클라이언트를 만드는지 확인한다."""
    seen = _capture(monkeypatch)
    cfg = adapters.ModelConfig(model="m", region="us-east-1", api_key="ak", api_secret="sk")
    adapters._bedrock_call(cfg, "sys", "user")
    assert [s["service"] for s in seen] == ["bedrock-runtime"]
    assert seen[0]["config"].connect_timeout == 5
    assert seen[0]["config"].read_timeout == 60
