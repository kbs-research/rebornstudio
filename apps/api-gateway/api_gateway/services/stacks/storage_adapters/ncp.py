"""NCP(S3 호환) 저장소 어댑터. 운영에서 쓰는 저장소 계약 구현이다.

더미(`dummy_gcs`)는 GCS 방식(오프셋 재개·`gs://`·서버 복사 없음)을 쓰고 이 어댑터는
S3 방식(파트 번호 재개·`s3://`·서버 복사)을 쓴다. 운영 백엔드와 형태가 반대인 더미가
같은 계약 테스트를 통과해야 계약이 유효하다.

`media/storage.py`가 단일 진입점이며 내부의 `storage._adapter`가 이 어댑터를 만든다.
호출부는 어댑터를 직접 import하지 않으므로 백엔드를 바꿀 때는 `storage._adapter` 하나만 고친다.

판정은 도메인에 둔다. 버킷 선택(`derived_bucket`)과 매체 선택(`resolver`)이 도메인의 판정이다.
어댑터에 판정을 두면 백엔드별 분기가 도메인으로 퍼져, 클라우드를 더할 때마다 여러 곳을 고쳐야 한다.
"""
from __future__ import annotations

from datetime import UTC, datetime

from reborn_contracts.storage_backend import (
    ObjectInfo,
    ObjectRef,
    StorageCapability,
    StorageInfo,
    UploadSession,
)

INFO = StorageInfo(
    key="ncp",
    label="NCP Object Storage (S3 호환)",
    capabilities=frozenset({
        StorageCapability.PRESIGNED_GET,
        StorageCapability.PRESIGNED_PUT,
        StorageCapability.RESUMABLE_UPLOAD,
        # 더미에 없는 능력. S3는 서버 쪽에서 복사하므로 수 GB를 API 서버로 왕복시키지 않는다.
        StorageCapability.SERVER_SIDE_COPY,
        StorageCapability.BUCKET_BROWSE,
    }),
    upload_kind="s3-multipart",
    uri_scheme="s3",
)

#: 멀티파트 파트 크기. 백엔드가 정하는 값이라 화면은 세션의 `part_size_bytes`로 받는다.
#: S3 최소값인 5MiB다. 3GB 영상이면 640개 파트로 나뉜다.
PART_SIZE_BYTES = 5 * 1024 * 1024

#: 이 크기 이상인 객체는 멀티파트 복사(UploadPartCopy)로 나눠 복사하는 기준.
#: S3의 단일 CopyObject는 5 GiB까지만 복사하고 규격 검사는 그보다 큰 원본도 받는다.
#: 상한보다 낮게 잡아 상한 근처의 객체도 나눠 복사한다.
COPY_MULTIPART_THRESHOLD = 1024 ** 3
#: 멀티파트 복사의 파트 크기. 10 GiB 원본이면 40개 파트로 나뉜다.
COPY_PART_SIZE = 256 * 1024 * 1024


class NcpStorage:
    """저장소 계약으로 감싼 boto3 클라이언트.

    Args:
        client: 주입받는 boto3 S3 클라이언트. 설정을 여기서 읽지 않아 계약 테스트가 환경에
            의존하지 않는다.
        ttl_seconds: presign 기본 만료 시간(초).
    """

    info = INFO

    def __init__(self, client, *, ttl_seconds: int = 3600) -> None:
        """클라이언트와 기본 만료 시간을 받는다."""
        self._c = client
        self._ttl = ttl_seconds

    def uri(self, ref: ObjectRef) -> str:
        """도메인이 저장하는 URI 문자열을 만든다. 스킴은 백엔드가 정한다(`s3://`)."""
        return f"{self.info.uri_scheme}://{ref.bucket}/{ref.key}"

    # 읽기.

    def head(self, ref: ObjectRef) -> ObjectInfo | None:
        """객체 정보를 돌려준다. 객체가 없으면 `None`이다(빈 객체 `size_bytes=0`과 구분한다).

        `ClientError` 중 404만 `None`으로 바꾸고 나머지는 다시 던진다.
        403은 객체를 읽을 권한이 없다는 뜻이다.
        403을 없음으로 처리하면 자격증명 문제가 빈 목록으로 보인다.
        """
        from botocore.exceptions import ClientError

        try:
            r = self._c.head_object(Bucket=ref.bucket, Key=ref.key)
        except ClientError as e:
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise
        return ObjectInfo(
            size_bytes=r.get("ContentLength"),
            content_type=r.get("ContentType"),
            etag=(r.get("ETag") or "").strip('"') or None,
            last_modified=r.get("LastModified") or datetime.now(UTC),
        )

    def exists(self, ref: ObjectRef) -> bool:
        """객체가 있으면 True를 돌려준다. `head`를 쓰므로 403은 예외로 올라간다."""
        return self.head(ref) is not None

    def get_bytes(self, ref: ObjectRef) -> bytes:
        """객체 내용을 읽는다."""
        return self._c.get_object(Bucket=ref.bucket, Key=ref.key)["Body"].read()

    def presign_get(self, ref: ObjectRef, *, ttl_seconds: int | None = None) -> str:
        """서명된 읽기 URL을 만든다. `ttl_seconds`가 없으면 기본 만료 시간을 쓴다."""
        return self._c.generate_presigned_url(
            "get_object", Params={"Bucket": ref.bucket, "Key": ref.key},
            ExpiresIn=ttl_seconds or self._ttl,
        )

    # 쓰기.

    def put_bytes(self, ref: ObjectRef, body: bytes,
                  *, content_type: str = "application/octet-stream") -> None:
        """객체를 쓴다. `content_type`은 객체의 ContentType으로 저장된다."""
        self._c.put_object(
            Bucket=ref.bucket, Key=ref.key, Body=body, ContentType=content_type)

    def presign_put(self, ref: ObjectRef, *, ttl_seconds: int | None = None,
                    content_type: str | None = None) -> str:
        """서명된 쓰기 URL을 만든다.

        `content_type`을 주면 서명에 포함된다. 그러면 워커는 같은 Content-Type 헤더로 PUT해야 한다.
        주지 않으면 어떤 타입으로도 올릴 수 있다.
        산출물은 타입을 강제하고 임시 파일은 강제하지 않으므로 선택 인자로 둔다.
        """
        params = {"Bucket": ref.bucket, "Key": ref.key}
        if content_type is not None:
            params["ContentType"] = content_type
        return self._c.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=ttl_seconds or self._ttl,
        )

    def delete(self, ref: ObjectRef) -> None:
        """객체를 지운다. S3의 DELETE는 멱등이라 없어도 오류를 내지 않는다."""
        self._c.delete_object(Bucket=ref.bucket, Key=ref.key)

    def copy(self, src: ObjectRef, dst: ObjectRef) -> None:
        """서버 쪽에서 객체를 복사한다. 바이트가 API 서버를 거치지 않는다.

        더미에는 없는 능력이다(`SERVER_SIDE_COPY`). 능력이 없는 백엔드는 다운로드 후 업로드로
        대신하면 수 GB를 왕복시키므로, 능력으로 선언해 호출부가 두 경우를 구분하게 한다.

        boto3 관리형 복사를 쓴다. `COPY_MULTIPART_THRESHOLD` 미만은 CopyObject 한 번,
        그 이상은 UploadPartCopy로 나눠 복사하므로 5 GiB를 넘는 객체도 복사한다.
        멀티파트 복사는 원본의 ContentType과 사용자 메타데이터를 옮기지 않는다.
        재생본의 ContentType이 빠지면 브라우저가 재생 형식을 알 수 없으므로 원본 값을 함께 넘긴다.
        """
        from boto3.s3.transfer import TransferConfig

        head = self._c.head_object(Bucket=src.bucket, Key=src.key)
        extra: dict = {}
        if (head.get("ContentLength") or 0) >= COPY_MULTIPART_THRESHOLD:
            if head.get("ContentType"):
                extra["ContentType"] = head["ContentType"]
            if head.get("Metadata"):
                extra["Metadata"] = head["Metadata"]
        self._c.copy(
            CopySource={"Bucket": src.bucket, "Key": src.key},
            Bucket=dst.bucket, Key=dst.key, ExtraArgs=extra or None,
            Config=TransferConfig(
                multipart_threshold=COPY_MULTIPART_THRESHOLD,
                multipart_chunksize=COPY_PART_SIZE,
            ),
        )

    # 재개 가능한 업로드: 파트 번호 방식.

    def begin_upload(self, ref: ObjectRef, *, content_type: str = "video/mp4") -> UploadSession:
        """업로드 세션을 연다. 세션의 `kind`에 `s3-multipart`를 명시한다.

        `upload_id`를 채우고 `session_url`은 비운다. 더미(GCS)는 `session_url`을 채운다.
        두 구현으로 계약이 이 차이를 견디는지 확인한다.
        """
        r = self._c.create_multipart_upload(
            Bucket=ref.bucket, Key=ref.key, ContentType=content_type)
        return UploadSession(
            kind="s3-multipart", ref=ref,
            upload_id=r["UploadId"], part_size_bytes=PART_SIZE_BYTES,
        )

    def presign_parts(self, session: UploadSession, part_numbers: list[int]) -> dict[int, str]:
        """요청받은 파트의 업로드 URL만 서명한다.

        640개를 한 번에 서명하면 느린 회선에서는 마지막 파트를 올리기 전에 서명이 만료된다.
        """
        return {
            n: self._c.generate_presigned_url(
                "upload_part",
                Params={"Bucket": session.ref.bucket, "Key": session.ref.key,
                        "UploadId": session.upload_id, "PartNumber": n},
                ExpiresIn=self._ttl,
            )
            for n in part_numbers
        }

    def finish_upload(self, session: UploadSession, parts: list[dict]) -> ObjectInfo:
        """파트 목록을 다시 보내 업로드를 끝내고 객체 정보를 돌려준다.

        더미의 `finish_upload`는 마지막 청크를 받으면 끝나므로 파트 목록을 받지 않는다.
        이 메서드로 계약이 두 방식의 차이를 견디는지 확인한다.
        """
        self._c.complete_multipart_upload(
            Bucket=session.ref.bucket, Key=session.ref.key,
            UploadId=session.upload_id, MultipartUpload={"Parts": parts},
        )
        info = self.head(session.ref)
        if info is None:
            raise RuntimeError("완료했는데 객체가 없다")
        return info

    def abort_upload(self, session: UploadSession) -> None:
        """멀티파트 업로드를 중단한다.

        중단해야 올라간 파트가 지워진다.
        남은 파트는 버킷에 쌓여 과금된다(`AbortIncompleteMultipartUpload`).
        """
        self._c.abort_multipart_upload(
            Bucket=session.ref.bucket, Key=session.ref.key, UploadId=session.upload_id)

    # 운영 화면.

    def list_buckets(self) -> list[str]:
        """버킷 이름 목록을 정렬해 돌려준다."""
        return sorted(b["Name"] for b in self._c.list_buckets().get("Buckets", []))

    def list_objects(self, bucket: str, prefix: str = "") -> list[ObjectRef]:
        """접두어로 객체를 찾는다. 결과는 키 순으로 정렬한다.

        정렬하지 않으면 실행마다 순서가 달라져 화면의 목록 순서가 바뀐다.
        버킷이 없으면 빈 목록을 돌려줘 운영 화면이 오류로 멈추지 않게 한다.
        권한 오류(`403`)는 버킷 없음과 다르므로 다시 던진다.
        """
        from botocore.exceptions import ClientError

        out: list[ObjectRef] = []
        try:
            pages = self._c.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=prefix)
            for page in pages:
                out += [ObjectRef(bucket=bucket, key=o["Key"])
                        for o in page.get("Contents", [])]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("NoSuchBucket", "404"):
                return []
            raise
        return sorted(out, key=lambda r: r.key)
