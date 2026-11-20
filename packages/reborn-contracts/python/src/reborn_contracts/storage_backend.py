"""저장소 백엔드 경계. SDK 응답 대신 이 모델이 기준이다.

`storage.py`는 화면이 보내고 읽는 DTO(일괄 등록 요청과 결과)다.
이 파일은 백엔드가 지켜야 할 모양이다. 둘을 한 파일에 두면 화면 스키마를 고치다가
백엔드 계약을 건드리게 되므로 파일을 나눈다.

S3 호환 저장소 사이의 차이는 boto3가 흡수한다.
AWS S3·MinIO·Wasabi·NCP는 엔드포인트만 바꾸면 같은 코드로 동작한다.
그래서 `services/media/storage.py`는 boto3 호출을 계층 없이 나열한 채로도 동작한다.

계약은 S3와 호환되지 않는 저장소를 위해 둔다.
- `head`·`delete`·`copy`·`list`: 거의 같고 호출 이름만 다르다.
- presigned GET/PUT: 조금 다르다. 개념은 같고 서명 방식만 다르다.
- 재개 가능한 업로드: 크게 다르다. S3 multipart는 파트 번호를 쓰고,
  GCS resumable은 세션 URI와 오프셋을 쓴다.

업로드 방식은 하나로 합치지 않는다.
개념이 다른 둘을 공통 인터페이스로 합치면 어느 쪽도 자기 방식대로 쓸 수 없다.
대신 세션에 종류를 싣고(`UploadSession.kind`) FE가 그 값으로 분기한다.

어댑터는 형식 변환만 한다.
판정은 이미 한 곳씩 모여 있다(`plan_outputs`·`resolver`·`derived_bucket`·`pipeline.open_next`).
어댑터가 늘어도 판정하는 곳은 늘리지 않는다.
어댑터가 판정을 가지면 저장소별 분기가 도메인으로 퍼진다.
그러면 클라우드를 늘릴 때마다 판정을 여러 곳에서 고치게 된다.
"""
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class StorageCapability(StrEnum):
    """백엔드가 선언하는 능력. 계약 테스트는 선언한 능력만 검사한다."""

    #: presigned GET. 브라우저·워커가 자격증명 없이 읽는다.
    #: 이 능력이 없으면 DB 없이 동작하는 워커 프로토콜이 성립하지 않는다.
    PRESIGNED_GET = "presigned_get"
    #: presigned PUT. 워커가 산출물을 올릴 때 쓴다.
    PRESIGNED_PUT = "presigned_put"
    #: 브라우저가 직접 올리는 재개 가능한 업로드 세션. 방식은 `UploadSession.kind`가 알려 준다.
    RESUMABLE_UPLOAD = "resumable_upload"
    #: 서버 쪽 복사. 데이터를 이 시스템으로 내려받지 않는다.
    #: 없으면 다운로드 후 업로드로 대신할 수 있다. 다만 수 GB를 왕복시키므로 비용이 크다.
    SERVER_SIDE_COPY = "server_side_copy"
    #: 버킷 목록·용량 집계. 운영 화면(스토리지 브라우저)이 쓴다.
    BUCKET_BROWSE = "bucket_browse"


#: 재개 가능한 업로드의 방식. 두 방식은 개념이 달라 하나로 합치지 않는다.
UploadKind = Literal["s3-multipart", "gcs-resumable"]


class ObjectRef(BaseModel):
    """객체 하나를 가리키는 `(버킷, 키)` 쌍.

    도메인 안에서 문자열 URI(`s3://…`)를 매번 파싱하지 않으려고 둔다.
    스킴은 백엔드마다 다르다(`s3://`·`gs://`).
    그 차이가 도메인까지 퍼지면 클라우드를 늘릴 때마다 파서를 고쳐야 한다.
    """

    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)


class ObjectInfo(BaseModel):
    """`head`가 돌려주는 객체 정보. 값이 없으면 `None`이다(0과 구분한다).

    `size_bytes=0`은 빈 객체이고, `None`은 세지 않았다는 뜻이다.
    둘을 합치면 화면이 빈 파일과 읽지 못한 파일을 같게 그린다.
    """

    size_bytes: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: datetime | None = None


class UploadSession(BaseModel):
    """재개 가능한 업로드 세션. 세션이 자기 종류를 밝힌다.

    S3는 파트 번호로 재개하고, GCS는 세션 URI와 오프셋으로 재개한다.
    같은 필드로 합치면 어느 쪽도 자기 방식대로 쓸 수 없다. 그러면 FE가 값의 모양을 보고
    백엔드를 추측하게 된다. 그래서 공통 필드로 합치지 않는다.

    `s3-multipart`: `upload_id`를 채우고, 파트 URL은 필요할 때마다 서명해 받는다.
    `gcs-resumable`: `session_url`을 채우고, 그 URL 하나에 오프셋을 붙여 이어 올린다.
    """

    kind: UploadKind
    ref: ObjectRef
    #: `s3-multipart`에서만 채운다.
    upload_id: str | None = None
    #: `gcs-resumable`에서만 채운다.
    session_url: str | None = None
    #: 한 파트의 크기(바이트). 백엔드가 정하고 FE가 짐작하지 않는다.
    part_size_bytes: int | None = None


class StorageInfo(BaseModel):
    """저장소 백엔드의 능력 선언. 화면·계약 테스트·프로젝트 생성이 읽는다.

    `analysis.AdapterInfo`와 같은 역할이고, 능력을 값으로 갖는 이유도 같다.
    이름으로 분기하면(`if key == "ncp"`) 백엔드가 늘 때마다 그 분기를 찾아 고쳐야 한다.
    """

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    capabilities: frozenset[StorageCapability]
    #: 이 백엔드의 업로드 방식. `RESUMABLE_UPLOAD`를 선언했으면 반드시 있다.
    upload_kind: UploadKind | None = None
    #: URI 스킴(`s3`·`gs`). 도메인은 이 값을 통해서만 URI를 만든다.
    uri_scheme: str = "s3"

    def can(self, capability: StorageCapability) -> bool:
        """`capability`를 선언했으면 True. 화면·파이프라인은 이름 대신 이 함수로 분기한다."""
        return capability in self.capabilities
