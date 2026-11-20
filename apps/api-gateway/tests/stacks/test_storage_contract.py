"""저장소 계약 테스트. 어댑터마다 따로 쓰지 않고 한 세트를 파라미터로 돌린다.

분석 계약 테스트(`test_analysis_contract.py`)와 같은 구조다.
그 파일의 `ADAPTERS` 목록에 해당하는 것이 여기서는 `store` 픽스처의 `params`다.
새 백엔드를 붙이려면 그 목록에 이름을 더하고 이 테스트를 통과시킨다.

더미(`dummy-gcs`)와 NCP 두 구현을 같은 테스트로 돌려 두 구현의 차이를 드러낸다.
`media/storage.py`의 나머지 기능은 아직 이 계약 뒤로 옮기지 않았다(`storage_adapters/README.md`의 "남은 일").
"""
import pytest
from api_gateway.services.stacks.storage_adapters.dummy_gcs import DummyGcsStorage
from reborn_contracts.storage_backend import ObjectRef, StorageCapability


@pytest.fixture(params=["dummy-gcs", "ncp"])
def store(request, tmp_path):
    """어댑터마다 따로 쓰지 않고 한 세트를 파라미터로 돌린다. 분석 계약과 같은 구조다.

    두 구현은 일부러 반대 방식이다.
    더미는 오프셋으로 재개하고 `gs://`를 쓰며 서버 복사가 없다.
    NCP는 파트 번호로 올리고 `s3://`를 쓰며 서버 복사가 있다.
    둘이 같은 테스트를 통과하면 계약이 백엔드 방식과 무관하게 동작한다.

    NCP는 인프로세스 moto를 쓰고 외부를 호출하지 않는다.
    계약 테스트가 자격 증명에 의존하면 계약 준수가 아니라 환경 준비 여부를 검사하게 된다.
    """
    if request.param == "dummy-gcs":
        return DummyGcsStorage(tmp_path / "buckets")

    import contextlib

    from api_gateway.services.media.storage import _client
    from api_gateway.services.stacks.storage_adapters.ncp import NcpStorage
    from tests import _storage

    # 기존 클라이언트를 그대로 쓴다.
    # 여기서 boto3 클라이언트를 새로 만들면 NCP가 요구하는 체크섬 설정
    # (`request_checksum_calculation`)이 빠진다.
    # 그러면 테스트는 moto에서 통과하고 운영에서만 AccessDenied가 난다.
    c = _client(_storage.store())
    with contextlib.suppress(Exception):   # 이미 있으면 그대로 쓴다
        c.create_bucket(Bucket="test-bucket")
    st = NcpStorage(c)
    # 앞 테스트가 남긴 키를 지운다. 버킷을 공유하므로 목록 테스트가 남은 키까지 센다.
    for r in st.list_objects("test-bucket"):
        st.delete(r)
    return st


def _ref(key: str = "a/b.mp4") -> ObjectRef:
    """테스트 버킷의 객체 참조를 만든다."""
    return ObjectRef(bucket="test-bucket", key=key)


# 소개


def test_declares_its_upload_kind_when_it_claims_resumable():
    """`RESUMABLE_UPLOAD`를 선언했으면 업로드 방식도 선언하는지 확인한다.

    방식 없이 능력만 선언하면 FE가 어느 방식으로 분기할지 정할 수 없다.
    그러면 FE가 값의 모양으로 방식을 추측하게 되고, 결국 FE가 백엔드 구현에 의존하게 된다.
    """
    from api_gateway.services.stacks.storage_adapters.dummy_gcs import INFO

    if INFO.can(StorageCapability.RESUMABLE_UPLOAD):
        assert INFO.upload_kind is not None


def test_the_dummy_is_shaped_differently_from_boto3():
    """더미가 NCP와 다른 모양인지 확인한다. 같으면 계약을 검사한 효과가 없다.

    NCP 하나만 있으면 계약은 boto3의 모양 그대로라서 항상 만족된다.
    그래서 더미는 방식, 스킴, 빠진 능력 셋을 일부러 다르게 둔다.
    """
    from api_gateway.services.stacks.storage_adapters.dummy_gcs import INFO

    assert INFO.upload_kind == "gcs-resumable", "S3와 같은 방식이면 시험한 게 없다"
    assert INFO.uri_scheme == "gs"
    assert not INFO.can(StorageCapability.SERVER_SIDE_COPY), "능력이 빠진 경우가 하나는 있어야 한다"


# 읽기·쓰기


def test_missing_object_is_none_not_an_empty_one(store):
    """없는 객체가 `None`이고 빈 객체(`size_bytes=0`)와 구분되는지 확인한다.

    둘을 합치면 화면이 빈 파일과 읽지 못한 파일을 같게 그린다.
    """
    assert store.head(_ref()) is None
    store.put_bytes(_ref(), b"")
    info = store.head(_ref())
    assert info is not None and info.size_bytes == 0


def test_round_trip_preserves_bytes(store):
    """쓴 바이트를 그대로 읽고 크기도 맞게 돌려주는지 확인한다."""
    store.put_bytes(_ref(), b"\x00\x01hello")
    assert store.get_bytes(_ref()) == b"\x00\x01hello"
    assert store.head(_ref()).size_bytes == 7


def test_delete_is_idempotent(store):
    """없는 객체를 지워도 오류가 나지 않는지 확인한다. 삭제가 멱등이 아니면 재시도가 안전하지 않다."""
    store.delete(_ref())
    store.put_bytes(_ref(), b"x")
    store.delete(_ref())
    store.delete(_ref())
    assert not store.exists(_ref())


def test_uri_uses_the_backend_scheme(store):
    """URI 스킴을 백엔드가 정하는지 확인한다. 도메인이 `s3://`를 고정하면 GCS에서 틀린 값이 된다."""
    assert store.uri(_ref()) == f"{store.info.uri_scheme}://test-bucket/a/b.mp4"


def test_presigned_url_is_something_a_worker_can_fetch(store):
    """presigned URL이 무DB 워커가 그대로 받을 수 있는 형식인지 확인한다.

    워커는 이 URL 문자열 하나로 일하므로 전용 형식을 만들면 워커가 백엔드를 알아야 한다.
    더미는 `file://`, NCP는 `http(s)://`를 준다.
    둘 다 ffmpeg·httpx가 그대로 받는 형식이어야 한다.
    """
    store.put_bytes(_ref(), b"x")
    url = store.presign_get(_ref())
    assert url.startswith(("file://", "http://", "https://"))
    assert "a/b.mp4" in url


def test_keys_cannot_escape_the_bucket(store):
    """`..`로 저장소 밖을 가리킬 수 없는지 확인한다. 더미는 로컬 디스크를 쓰므로 실제 위험이다.

    S3 키는 문자열일 뿐이라 저장소 밖이라는 개념이 없어서 NCP에는 해당하지 않는다.
    해당하지 않는 위험을 검사하지 않도록 더미에서만 확인한다.
    """
    if store.info.key != "dummy-gcs":
        pytest.skip("S3 키에는 '밖'이 없다 — 더미(로컬 디스크)만의 위험이다")
    with pytest.raises(ValueError):
        store.head(ObjectRef(bucket="test-bucket", key="../../etc/passwd"))


# 재개 가능한 업로드: S3와 다른 방식도 계약을 지키는지 확인한다


def test_session_says_its_own_kind(store):
    """업로드 세션이 자기 방식(`kind`)을 돌려주는지 확인한다. FE는 그 값으로 분기한다.

    `session_url`과 `upload_id` 중 한쪽만 값이 있어야 한다.
    둘 다 있으면 FE가 값의 모양으로 방식을 추측하게 된다.
    """
    s = store.begin_upload(_ref())
    assert s.kind == store.info.upload_kind
    assert s.part_size_bytes, "파트 크기는 백엔드가 정한다 — FE가 짐작하면 안 된다"
    if s.kind == "gcs-resumable":
        assert s.session_url and s.upload_id is None
    else:
        assert s.upload_id and s.session_url is None


def test_resumable_upload_round_trips(store):
    """각 백엔드가 자기 방식으로 이어 올리고 끝내는지 확인한다.

    두 방식을 하나로 합치지 않았으므로 각 방식의 절차를 모두 거친다.
    GCS는 오프셋으로 이어 붙이고, 끝까지 올리면 완료된다.
    S3는 파트 번호로 올리고, 파트 목록을 돌려줘야 끝난다.
    이 테스트는 계약이 그 차이를 수용하는지 확인한다.
    """
    s = store.begin_upload(_ref())
    if s.kind == "gcs-resumable":
        n = store.append_chunk(s, 0, b"abc")
        n = store.append_chunk(s, n, b"de")
        assert n == 5
        assert store.finish_upload(s).size_bytes == 5
    else:
        # 5MiB 미만의 단일 파트다. S3는 마지막 파트에 최소 크기 제한을 두지 않는다.
        url = store.presign_parts(s, [1])[1]
        import httpx
        r = httpx.put(url, content=b"abcde")
        assert r.status_code == 200, r.text
        etag = r.headers["ETag"]
        assert store.finish_upload(s, [{"ETag": etag, "PartNumber": 1}]).size_bytes == 5
    assert store.get_bytes(_ref()) == b"abcde"


def test_only_the_declared_copy_capability_exists(store):
    """선언한 복사 능력만 있는지 확인한다. 선언하지 않은 능력은 없어도 정상이다.

    NCP는 서버 쪽 복사를 제공하고 더미는 제공하지 않으며, 계약 테스트가 그 둘을 구분한다.
    선언했으면 동작해야 하고, 선언하지 않았으면 없어도 된다.
    """
    store.put_bytes(_ref(), b"xyz")
    if store.info.can(StorageCapability.SERVER_SIDE_COPY):
        store.copy(_ref(), _ref("copied.mp4"))
        assert store.get_bytes(_ref("copied.mp4")) == b"xyz"
    else:
        assert not hasattr(store, "copy"), "선언 안 했는데 있다 — 선언이 거짓이다"


# 운영 화면


def test_listing_is_sorted_and_prefix_filtered(store):
    """목록을 정렬하고 prefix로 거르는지 확인한다. 순서가 실행마다 달라지면 화면 표시가 흔들린다."""
    for k in ("b/2.mp4", "a/1.mp4", "a/3.mp4"):
        store.put_bytes(_ref(k), b"x")
    keys = [r.key for r in store.list_objects("test-bucket", prefix="a/")]
    assert keys == ["a/1.mp4", "a/3.mp4"]
    assert store.list_buckets() == ["test-bucket"]


def test_listing_a_missing_bucket_is_empty_not_an_error(store):
    """없는 버킷의 목록이 오류가 아니라 빈 목록인지 확인한다. 운영 화면이 오류로 멈출 이유가 없다.

    버킷 이름은 S3가 받을 수 있는 것으로 쓴다.
    한글 이름은 더미는 받지만 boto3가 요청을 보내기 전에 거부하므로 두 구현의 결과가 달라진다.
    """
    assert store.list_objects("no-such-bucket-9f3a") == []
