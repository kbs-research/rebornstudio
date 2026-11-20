"""저장소 클라이언트의 시한·재시도 설정과 호출별 용도를 확인한다.

botocore 기본값이면 응답 없는 엔드포인트 하나가 HTTP 요청을 몇 분씩 붙잡는다. 대용량 전송은 일반
시한으로 끊으면 서버 복사·멀티파트 완료가 다시 보내져 실패한다. 두 시한이 실제 클라이언트에 실리는지와
호출마다 맞는 쪽을 고르는지를 본다. 네트워크는 쓰지 않는다(클라이언트 생성과 가짜 클라이언트만 쓴다).
"""
import pytest
from api_gateway.services import platform_credentials
from api_gateway.services.media import storage


def _store(conn_id: str) -> platform_credentials.StorageCredentials:
    """네트워크를 쓰지 않는 저장소 값을 만든다. 클라이언트 생성만 확인한다."""
    return platform_credentials.StorageCredentials(
        endpoint="https://kr.object.ncloudstorage.com", region="kr-standard",
        bucket="sample-bucket", access_key="ak", secret_key="sk", connection_id=conn_id)


def test_request_client_carries_short_timeouts():
    """일반 호출 클라이언트가 연결 5초·읽기 60초·standard 재시도를 싣는지 확인한다."""
    cfg = storage._client(_store("timeouts-request")).meta.config
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 60
    # botocore가 `max_attempts`(재시도 횟수)를 첫 시도를 더한 `total_max_attempts`로 바꿔 담는다.
    assert cfg.retries == {"total_max_attempts": 3, "mode": "standard"}
    # NCP가 요구하는 체크섬 설정이 함께 남아 있어야 한다.
    assert cfg.request_checksum_calculation == "when_required"
    assert cfg.signature_version == "s3v4"


def test_bulk_client_carries_long_read_timeout():
    """대용량 전송 클라이언트가 같은 연결 시한에 긴 읽기 시한을 싣는지 확인한다."""
    store = _store("timeouts-bulk")
    bulk = storage._client(store, bulk=True)
    cfg = bulk.meta.config
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 600
    assert cfg.retries == {"total_max_attempts": 5, "mode": "standard"}
    assert cfg.request_checksum_calculation == "when_required"
    # 두 용도는 따로 캐시한다. 같은 객체면 한쪽 설정이 다른 쪽을 덮는다.
    assert bulk is not storage._client(store)
    assert bulk is storage._client(store, bulk=True)


class _Fake:
    """호출만 받아 두는 가짜 S3 클라이언트."""

    def __getattr__(self, name):
        """어떤 메서드든 빈 응답을 돌려준다."""
        def call(*a, **k):
            """빈 응답을 돌려준다."""
            return {"Body": b"", "Contents": [], "UploadId": "u"}
        return call


@pytest.fixture
def picked(monkeypatch) -> list[bool]:
    """`_client`가 받은 `bulk` 값을 차례로 기록한다."""
    seen: list[bool] = []
    store = _store("timeouts-picked")

    def fake_client(s, *, bulk: bool = False):
        """용도를 기록하고 가짜 클라이언트를 돌려준다."""
        seen.append(bulk)
        return _Fake()

    monkeypatch.setattr(storage, "_client", fake_client)
    monkeypatch.setattr(storage, "_for_bucket", lambda bucket: store)
    return seen


@pytest.mark.parametrize("call", [
    lambda s: storage.upload_file("/dev/null", "k", store=s),
    lambda s: storage.get_object_body(bucket="sample-bucket", key="k"),
    lambda s: storage.copy_object(bucket="sample-bucket", src_key="a", dst_key="c"),
    lambda s: storage.complete_multipart("k", "u", [], store=s),
], ids=["upload_file", "get_object_body", "copy_object", "complete_multipart"])
def test_bulk_paths_use_the_long_timeout(picked, call):
    """패키지 업로드·본문 스트림·서버 복사·멀티파트 완료가 대용량 클라이언트를 고르는지 확인한다."""
    call(_store("timeouts-picked"))
    assert picked == [True]


@pytest.mark.parametrize("call", [
    lambda s: storage.head_in(bucket="sample-bucket", key="k"),
    lambda s: storage.list_one(s),
    lambda s: storage.create_multipart("k", store=s),
    lambda s: storage.list_parts("k", "u", store=s),
    lambda s: storage.put_bytes("k", b"x", store=s),
], ids=["head_in", "list_one", "create_multipart", "list_parts", "put_bytes"])
def test_request_paths_use_the_short_timeout(picked, call):
    """화면 요청 안에서 부르는 호출이 일반 클라이언트를 고르는지 확인한다."""
    call(_store("timeouts-picked"))
    assert picked == [False]
