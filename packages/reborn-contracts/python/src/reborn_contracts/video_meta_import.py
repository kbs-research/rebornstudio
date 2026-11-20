"""등록 묶음의 영상 메타를 표 파일 하나로 일괄 입력하는 요청·응답 스키마.

흐름은 세 단계다. 양식 내려받기(`GET /ingest-batches/{id}/meta-template`)로 묶음의 영상 목록을
받는다. 미리 보기(`POST …/meta-import/preview`)로 채운 파일을 검사한다.
반영(`POST …/meta-import/apply`)으로 미리 보기에서 확인한 변경을 저장한다.
반영 요청의 값은 미리 보기 결과와 별도로 서버가 다시 검사한다.
"""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from reborn_contracts.video import VideoUpdateRequest

#: 일괄 입력이 바꾸는 영상 필드. 키는 `VideoUpdateRequest`의 필드 이름과 같다.
BulkMetaField = Literal["title", "broadcaster", "program_name", "genre", "episode", "aired_at"]

#: 한 파일에서 읽는 최대 데이터 행 수. 반영 요청 한 번에 담는 최대 영상 수도 같은 값이다.
BULK_META_MAX_ROWS = 5000

#: 행 하나의 판정.
#:   `changed`   영상을 찾았고 바뀌는 값이 있다. 반영 대상이다.
#:   `unchanged` 영상을 찾았고 모든 값이 지금 값과 같거나 비어 있다.
#:   `invalid`   영상을 찾았지만 값이 규칙에 맞지 않거나 같은 영상을 가리키는 행이 여럿이다.
#:   `unmatched` 묶음에서 영상을 찾지 못했다.
#:   `ambiguous` 파일이나 제목이 묶음의 영상 여러 개와 같다. 어느 영상인지 정하지 않는다.
BulkMetaRowStatus = Literal["changed", "unchanged", "invalid", "unmatched", "ambiguous"]

#: 행이 영상을 찾은 열. 영상 ID, 파일, 제목 순서로 찾는다.
BulkMetaMatchedBy = Literal["video_id", "file", "title"]


class BulkMetaFieldChange(BaseModel):
    """행 하나에서 바뀌는 필드 하나. 방영일은 ISO 시각 문자열이다."""
    field: BulkMetaField
    before: str | None = None
    after: str


class BulkMetaRowError(BaseModel):
    """행 하나의 오류 하나. 특정 열의 오류가 아니면 `field`가 `None`이다."""
    field: BulkMetaField | None = None
    message: str


class BulkMetaPreviewRow(BaseModel):
    """올린 파일의 데이터 행 하나를 판정한 결과."""
    #: 파일 안의 행 번호. 스프레드시트 프로그램에 보이는 번호(머리글 행 포함 1부터)와 같다.
    row: int
    status: BulkMetaRowStatus
    video_id: UUID | None = None
    #: 찾은 영상의 지금 제목. 찾지 못했으면 파일에 적힌 제목이다.
    title: str | None = None
    #: 파일 열에 적힌 값.
    file: str | None = None
    matched_by: BulkMetaMatchedBy | None = None
    changes: list[BulkMetaFieldChange] = Field(default_factory=list)
    errors: list[BulkMetaRowError] = Field(default_factory=list)


class BulkMetaPreviewCounts(BaseModel):
    """판정별 행 수. 다섯 값의 합은 데이터 행 수다."""
    changed: int = 0
    unchanged: int = 0
    invalid: int = 0
    unmatched: int = 0
    ambiguous: int = 0


class BulkMetaPreview(BaseModel):
    """메타 일괄 입력 미리 보기 결과.

    빈 셀은 바꾸지 않는다. 값을 지우는 일은 영상 상세의 메타 편집에서 한다.
    """
    #: 읽은 시트 이름. CSV이면 `None`이다.
    sheet: str | None = None
    #: 알아본 열의 필드 키. 파일에 있는 순서다.
    columns: list[str]
    #: 알아보지 못해 읽지 않은 열의 머리글.
    ignored_columns: list[str] = Field(default_factory=list)
    #: 이 묶음에서 삭제되지 않은 영상 수. 일치한 행 수의 분모다.
    batch_video_count: int
    #: 파일의 행이 하나 이상 가리킨 영상 수.
    matched_video_count: int
    counts: BulkMetaPreviewCounts
    rows: list[BulkMetaPreviewRow]


class BulkMetaApplyItem(BaseModel):
    """반영할 영상 하나와 바꿀 필드.

    `fields`는 영상 메타 편집과 같은 규칙으로 검사한다. 일괄 입력은 값을 지우지 않으므로
    `null`을 보낸 필드는 422다.
    """
    video_id: UUID
    fields: VideoUpdateRequest

    @model_validator(mode="after")
    def _no_clear(self) -> "BulkMetaApplyItem":
        """값을 지우는 필드(`null`)와 빈 변경을 막는다."""
        sent = self.fields.model_fields_set
        if not sent:
            raise ValueError("바꿀 필드가 없습니다")
        if any(getattr(self.fields, k) is None for k in sent):
            raise ValueError("일괄 입력으로는 값을 지울 수 없습니다")
        return self


class BulkMetaApplyRequest(BaseModel):
    """메타 일괄 입력 반영 요청.

    모든 영상이 이 묶음에 있어야 한다. 하나라도 실패하면 아무것도 바꾸지 않는다.
    """
    items: list[BulkMetaApplyItem] = Field(min_length=1, max_length=BULK_META_MAX_ROWS)

    @model_validator(mode="after")
    def _unique_videos(self) -> "BulkMetaApplyRequest":
        """같은 영상이 두 번 들어간 요청을 막는다. 어느 값이 남을지 순서에 맡기지 않는다."""
        ids = [i.video_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 영상이 두 번 들어 있습니다")
        return self


class BulkMetaApplyResult(BaseModel):
    """메타 일괄 입력 반영 결과."""
    #: 값이 실제로 바뀐 영상 수.
    updated: int
    #: 요청에 있었지만 저장된 값과 모두 같아 바뀌지 않은 영상 수.
    unchanged: int
    #: 바뀐 필드 수의 합.
    changed_fields: int
