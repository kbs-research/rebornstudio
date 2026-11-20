"""저장소 도메인 스키마. 입력한 NCP Object Storage 키와 폴더 경로의 일괄 등록을 다룬다."""
from uuid import UUID

from pydantic import BaseModel, Field

from reborn_contracts.video import CopyrightPolicy, TranscodeProfile


class StorageRegisterRequest(BaseModel):
    """NCP 키 목록의 영상 일괄 등록 요청. 반입 시트의 NCP 탭이 입력한 키를 보낸다.

    메타 값은 반입 시트의 공통 영역에서 입력받는다.
    NCP 키 등록이 기본 반입 경로라서 pre_cleared 등록의 감사 근거도 여기서 받는다.
    """
    keys: list[str] = Field(min_length=1, max_length=200)
    bucket: str | None = None   # 프로젝트 저장소의 버킷만 받는다. 없으면 그 버킷을 쓴다
    # 권리 정책 값은 pre_cleared 하나다. RebornStudio Pro와 같은 스키마를 유지하려고 필드를 남긴다.
    # 화면은 이 값을 보내지 않으므로 기본값이 곧 저장값이다.
    copyright_policy: CopyrightPolicy = "pre_cleared"
    broadcaster: str | None = Field(default=None, max_length=100)
    program_name: str | None = Field(default=None, max_length=200)
    # 반입 배치. 이 요청으로 등록한 영상을 다음 단계에서 한 묶음으로 이어받는 데 쓴다.
    upload_batch_id: UUID | None = None
    # 트랜스코딩 프로파일.
    # 등록 시점에는 코덱·해상도 정보가 없으므로 기본값은 auto다.
    transcode_profile: TranscodeProfile = "auto"


class StorageRegisterResult(BaseModel):
    """일괄 등록 결과.

    `invalid`는 영상이 아닌 확장자라 제외한 키 수다.
    `skipped`는 영상 키 중 등록하지 않은 수다.
    이미 등록된 오브젝트도 다시 등록한다. 그래서 한 요청 안에서 반복된 키만 여기에 센다.
    다시 등록한 수는 `duplicates`에 센다.
    """
    registered: int
    skipped: int
    # 다시 등록한 건수. 같은 오브젝트가 이미 있어도 막지 않는다. 중복 관리는 올리는 쪽이 한다.
    duplicates: int = 0
    invalid: int = 0
    work_items_created: int
    video_ids: list[UUID] = []
    # 이 등록이 속한 Job.
    # 요청에 `upload_batch_id`가 없으면 서버가 만든 값이다.
    # 응답에 없으면 호출한 쪽이 자기가 만든 Job을 찾을 방법이 없다.
    # 기본값을 두지 않는다. 기본값이 있으면 Job 없이 등록되는 경우가 드러나지 않는다.
    batch_id: UUID


class StoragePrefixImportRequest(BaseModel):
    """폴더 전체 등록 요청. 실제 등록은 worker 작업이 한다.

    이 요청은 묶음 행에 작업 사양만 남기고 바로 돌아온다.
    진행 상황은 묶음 행(video_count·import_status)에서 확인한다.
    브라우저가 키를 모아 여러 번 보내면 큰 폴더에서 수백 번 왕복한다.
    그동안 탭을 닫을 수 없고, 중간에 실패하면 처음부터 다시 해야 한다.
    """
    prefix: str = Field(min_length=1, max_length=1024)
    bucket: str | None = None   # 프로젝트 저장소의 버킷만 받는다. 없으면 그 버킷을 쓴다
    # 값은 pre_cleared 하나다.
    # 화면은 이 값을 보내지 않으므로 기본값이 곧 저장값이다.
    copyright_policy: CopyrightPolicy = "pre_cleared"
    broadcaster: str | None = Field(default=None, max_length=100)
    program_name: str | None = Field(default=None, max_length=200)
    # 묶음 id. 업로드 흐름과 같이 클라이언트가 만든다.
    upload_batch_id: UUID
    # 트랜스코딩 프로파일.
    # 등록 시점에는 코덱·해상도 정보가 없으므로 기본값은 auto다.
    transcode_profile: TranscodeProfile = "auto"


class StoragePrefixImportResult(BaseModel):
    """폴더 등록을 큐에 넣은 결과. 진행 상황은 `batch_id`의 묶음 행에서 본다."""
    batch_id: UUID
    import_status: str  # 늘 'pending'. worker beat(10초 주기)가 가져간다

