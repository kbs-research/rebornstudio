"""영상 도메인 스키마."""
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# 영상 권리 정책. RebornStudio는 모든 영상이 `pre_cleared`다.
# 등록 즉시 통과하고, 분석 뒤 클립도 `auto_cleared`로 라벨링에 간다.
CopyrightPolicy = Literal["pre_cleared"]
# 업로드 상태. 변환 실패는 여기 두지 않고 `transcode_status`로 나타낸다.
# 한 컬럼에 업로드와 변환 상태를 합치면 변환 실패가 업로드 실패로 읽힌다.
# 값을 뺄 때는 모든 곳에서 함께 뺀다. 계약에만 없는 값이 DB에 있으면 `VideoDTO` 직렬화가 500을 낸다.
UploadStatus = Literal["pending", "uploaded", "failed"]
# MI 요청 전 ffprobe 규격 검사 결과.
#   `pending` 검사 대기, `passed` 규격 충족, `rejected` 규격 위반.
#   `error` 검사 실패(ffprobe가 읽지 못함). 분석 요청을 막는다. 읽지 못한 파일은 변환·재생본
#   단계가 열리지 않아, 막지 않으면 원본이 그대로 나간다. 일시 문제였으면 재검사로 풀린다.
#   위반 사유와 추출한 메타는 VideoDTO.metadata["probe"]에 있다.
ProbeStatus = Literal["pending", "passed", "rejected", "error"]


#: 방영일로 받는 가장 이른 날. 정규 방송이 시작되기 전 날짜는 방영일이 될 수 없다.
AIRED_AT_EARLIEST = datetime(1920, 1, 1, tzinfo=UTC)


def plausible_aired_at(value: datetime | None) -> datetime | None:
    """방영일이 있을 수 있는 범위인지 검사한다. `None`은 그대로 둔다.

    1920년 이전과 내일 이후는 받지 않는다. 오타(2099·1900)가 그대로 저장되면
    그 영상의 모든 클립이 물려받아 납품 메타에 실린다.
    시간대 없는 값은 UTC로 읽는다.
    시간대 차이로 오늘 방송이 막히지 않게 내일까지 받는다.

    Raises:
        ValueError: 범위를 벗어난 경우.
    """
    if value is None:
        return None
    at = value if value.tzinfo else value.replace(tzinfo=UTC)
    if at < AIRED_AT_EARLIEST or at > datetime.now(UTC) + timedelta(days=1):
        raise ValueError("방영일은 1920년부터 오늘까지만 넣을 수 있습니다")
    return value


class VideoUpdateRequest(BaseModel):
    """영상 메타 편집 요청(관리자).

    모든 필드가 선택이다. 보내지 않은 필드는 바꾸지 않는다.
    `null`을 보낸 필드는 지운다. 제목만은 지울 수 없어 `null`이면 422다.
    빈 문자열은 `min_length=1`로 막는다. 지우려면 `null`을 보낸다.
    "보내지 않음"과 "`null`"은 `model_fields_set`으로 구분한다.
    둘을 같게 읽으면 방영일을 비운 저장이 성공 응답과 함께 무시된다.

    방송사·프로그램명·장르·방영일은 영상 단위 메타 네 가지다.
    여기서 한 번 넣으면 그 영상의 모든 클립이 물려받는다.
    라벨러는 이 값을 고치지 않는다.
    """
    title: str | None = Field(default=None, min_length=1, max_length=255)
    broadcaster: str | None = Field(default=None, min_length=1, max_length=100)
    program_name: str | None = Field(default=None, min_length=1, max_length=200)
    genre: str | None = Field(default=None, min_length=1, max_length=100)
    episode: str | None = Field(default=None, min_length=1, max_length=50)
    aired_at: datetime | None = None

    @field_validator("aired_at")
    @classmethod
    def _aired_at_range(cls, value: datetime | None) -> datetime | None:
        """방영일 범위를 검사한다(`plausible_aired_at`)."""
        return plausible_aired_at(value)

    @model_validator(mode="after")
    def _title_not_cleared(self) -> "VideoUpdateRequest":
        """제목을 `null`로 보내면 막는다. 제목이 없는 영상은 목록에서 가리킬 수 없다."""
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("제목은 지울 수 없습니다")
        return self


class VideoMetaDTO(BaseModel):
    """영상 단위 메타 네 가지. 클립이 영상에서 물려받는 값.

    납품 메타(`ClipMetadata`), 품질검수의 메타 검사, 라벨링 화면의 읽기 전용 필드가 모두
    이 네 값을 영상에서 읽는다. 키 이름은 납품 스키마와 같다(`program_name`·`aired_at`).
    비어 있으면 `None`이고 빈 문자열로 채우지 않는다. 없는 것과 빈 것은 다르다.
    """
    broadcaster: str | None = None
    program_name: str | None = None
    genre: str | None = None
    aired_at: datetime | None = None


# 트랜스코딩 프로파일.
# auto는 판정(needs_transcode)에 맡기고, always·never는 판정 없이 정한다.
TranscodeProfile = Literal["auto", "always", "never"]


class VideoCreateRequest(BaseModel):
    """영상 등록 요청."""
    title: str = Field(min_length=1, max_length=255)
    #: 영상 권리 정책. RebornStudio는 모든 영상이 `pre_cleared`다.
    #: RebornStudio Pro와 같은 스키마를 유지하려고 필드를 남긴다.
    copyright_policy: CopyrightPolicy = "pre_cleared"
    duration_seconds: float | None = Field(default=None, ge=0)
    file_size_bytes: int | None = Field(default=None, ge=0)
    broadcaster: str | None = Field(default=None, max_length=100)
    program_name: str | None = Field(default=None, max_length=200)
    genre: str | None = Field(default=None, max_length=100)
    episode: str | None = Field(default=None, max_length=50)
    aired_at: datetime | None = None

    @field_validator("aired_at")
    @classmethod
    def _aired_at_range(cls, value: datetime | None) -> datetime | None:
        """방영일 범위를 검사한다(`plausible_aired_at`)."""
        return plausible_aired_at(value)

    # 대량 반입 1회분 묶음 id. 업로드 화면이 만들어 같은 배치의 영상에 같은 값을 넣는다.
    upload_batch_id: UUID | None = None
    # 등록 요청은 `extra`(응답의 `metadata`)를 받지 않는다. `extra`에는 포스터 키·저장소 고정
    # 기록처럼 서버가 서명할 객체를 정하는 값이 있어서, 받으면 다른 버킷의 객체 주소가 서명된다.
    # 모르는 필드는 무시하므로 `metadata`를 보내도 등록은 그대로 성공한다.
    # 트랜스코딩 프로파일. 기본값 auto는 probe가 판정한다.
    # 등록 시점에는 코덱 정보가 없기 때문이다.
    transcode_profile: TranscodeProfile = "auto"
    #: 브라우저가 올린 파일의 원래 이름.
    #: 납품 패키지의 `sources.csv`가 원본 파일을 가리키는 참고 정보다.
    #: 업로드 키(`intake/{id}/source`)와 편집할 수 있는 제목에는 파일 이름이 남지 않아 따로 받는다.
    #: 경로 부분은 떼고 이름만 저장한다. 제어 문자가 있으면 422다.
    original_filename: str | None = Field(default=None, max_length=255)

    @field_validator("original_filename")
    @classmethod
    def _original_filename_clean(cls, value: str | None) -> str | None:
        """원래 파일 이름을 검사한다(`clean_original_filename`)."""
        return clean_original_filename(value)


#: 원래 파일 이름을 담는 `VideoDTO.metadata`(`Video.extra`) 키.
#: 등록 경로가 쓰고 납품 패키지와 화면이 읽는다.
ORIGINAL_FILENAME_KEY = "original_filename"


def clean_original_filename(value: str | None) -> str | None:
    """원래 파일 이름에서 경로 부분을 떼고 이름만 돌려준다. 이름이 남지 않으면 `None`이다.

    브라우저에 따라 `C:\\fakepath\\a.mp4`처럼 경로가 붙어 온다. 경로는 올린 사람의 PC 구조라
    받는 쪽에 쓸모가 없고 저장하면 개인 폴더 이름이 납품 파일에 실린다.
    `.`·`..`만 남으면 파일 이름이 아니므로 `None`으로 돌려준다.

    Raises:
        ValueError: 제어 문자(줄바꿈·탭 포함)가 있는 경우. CSV 한 행이 여러 줄로 갈라진다.
    """
    if value is None:
        return None
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ValueError("파일 이름에 제어 문자를 넣을 수 없습니다")
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name if name not in ("", ".", "..") else None


# 준비 여정 단계.
# 영상이 등록부터 분석까지의 과정에서 어디에 있는지 나타낸다.
# 조치가 필요한 일을 나누는 bucket과는 목적이 다른 별도 분류다.
# 판정은 BE SQL CASE 한 곳(`services/media/video._prep_stage_case`)에서만 한다.
# FE는 문구와 묶음만 정한다(`lib/prepStage.ts`).
# 비식별화 단계(`deidentifying`)는 아직 이 목록에 없다. 넣을 때는 probing과 transcoding 사이에 둔다.
PrepStage = Literal[
    "uploading",          # 원본 업로드 진행 중(presign 발급 후 complete 전)
    "probing",            # 규격 검증·메타 추출 대기/진행
    "transcoding",        # h264 분석 사본 인코딩 중(대기·큐·실행)
    "awaiting_analysis",  # 준비 끝, MI 요청 대기(비용 결정. 대기 이유는 bucket 축)
    "registering",        # MI 등록(요청됨~import 완료)
    "analyzing",          # MI 분석 중
    "summarizing",        # 장면 요약 중
    "ready_for_work",     # 준비 여정 종료: 클립 생성. 이후는 사람 단계(파이프라인 축)
    "blocked",            # 어느 단계에서 막힘. 위치는 prep_blocked_stage
]

#: 막힌 위치(prep_stage=blocked일 때만). 화면이 "변환에서 막힘"처럼 위치를 함께 표시한다.
#: `analysis`(실패)는 다시 실행할 수 있고 `no_clips`(성공했지만 결과 0)는 다시 실행해도 같다.
#: `deident`는 시도했다 실패한 경우다.
#: `deident_unmasked`는 얼굴 가리기를 켜기 전에 처리돼 시도하지 않은 경우다.
#: `summary`는 클립은 있지만 장면 요약을 받지 못한 경우라 요약만 다시 요청한다.
#: 서로 합치면 화면이 효과 없는 재분석이나 시도한 적 없는 영상의 "다시 시도"를 권한다.
PrepBlockedStage = Literal["upload", "probe", "transcode", "deident", "deident_unmasked",
                          "publish", "analysis", "no_clips", "summary", "ticket"]

# 클립 하나가 납품까지 어디쯤 왔는지를 세 값으로 줄인 요약.
# `approved`는 검수 승인, `in_progress`는 가져간 뒤 승인 전(라벨링 중·검수 대기·재작업),
# `not_started`는 아직 아무도 가져가지 않은 클립(라벨링 대기)이다.
# 비식별화 신고로 제외된 클립은 승인될 수 없어 세 값 어디에도 넣지 않는다(클립은 `None`).
# 판정은 `services/media/progress.py` 한 곳에서 한다.
ClipProgress = Literal["approved", "in_progress", "not_started"]


class PrepStageCounts(BaseModel):
    """준비 여정의 단계별 건수. 여정 스트립과 배치 세그먼트가 쓴다.

    페이지와 관계없이 전체를 센다.
    모든 단계의 합은 `total`과 같다. 화면은 이 분모를 밝힌다.
    """
    uploading: int = 0
    probing: int = 0
    transcoding: int = 0
    awaiting_analysis: int = 0
    registering: int = 0
    analyzing: int = 0
    summarizing: int = 0
    ready_for_work: int = 0
    blocked: int = 0
    total: int = 0



class IngestBatchDTO(BaseModel):
    """반입 묶음 1건과 그 묶음의 진행 상황.

    묶음은 반입 단계에서만 쓴다. 분석이 끝나면 클립이 라벨링·검수로 흩어지므로
    묶음 단위로 진행 상황을 보여 주지 않는다.
    그래서 여기 싣는 분포는 `videos` 기준(반입 상태)이다.
    """
    batch_id: UUID
    project_id: UUID
    source: Literal["upload", "ncp"]
    label: str | None = None        # 비어 있으면 화면이 시각·출처로 만들어 보여 준다
    note: str | None = None
    created_by: UUID | None = None
    created_at: datetime

    video_count: int = 0
    total_duration_seconds: float = 0.0   # 비용 추정에 쓴다(단가는 maiu cost-stats)
    # 반입 상태 분포. 목록 필터와 같은 SQL CASE(`_ingest_bucket_case`)로 센다.
    ready: int = 0
    analyzing: int = 0
    done: int = 0
    blocked: int = 0
    # 준비 여정 분포. 배치 세그먼트 막대가 쓰고, 목록 필터와 같은 `_prep_stage_case`로 센다.
    prep: PrepStageCounts | None = None
    # 폴더 등록 작업(서버 worker)이 만든 묶음에만 싣는다. 일반 묶음은 셋 다 None이다.
    # pending/running이면 영상 수가 아직 늘고 있다는 뜻이고, 화면은 '등록 중'을 표시한다.
    import_prefix: str | None = None
    import_status: Literal["pending", "running", "done", "failed"] | None = None
    import_error: str | None = None
    #: 폴더를 다시 등록할 때 이미 등록된 파일이라 건너뛴 수(`import_spec.skipped_existing`).
    #: 폴더 등록 묶음이 아니면 0이다.
    import_skipped_existing: int = 0
    # 소프트 삭제된 영상 수. 위 분포는 삭제되지 않은 영상만 센다.
    # 이 값이 없으면 모두 삭제된 묶음이 '0건 · 반입 완료'로 잘못 보인다.
    deleted_videos: int = 0


class IngestJobIssue(BaseModel):
    """이슈 분해의 1행. 어느 단계에서 몇 건이 멈췄는지 담는다.

    "이슈 4건"만으로는 조치할 수 없다.
    "검증 실패 3 · 변환 실패 1"이어야 무엇부터 볼지 정할 수 있다.
    `stage`는 서버의 `prep_blocked_stage` 값 그대로이고, 문구는 화면이 만든다.

    Job 상세와 프로젝트 전체 집계(`VideoBucketCounts.issues`)가 같은 모양을 쓴다.
    화면은 한 함수로 둘 다 읽고, 어느 화면에서든 버튼 이름의 건수가 같은 뜻이 된다.
    """
    stage: PrepBlockedStage
    count: int


class IngestJobDetailDTO(BaseModel):
    """등록 Job의 상세 정보. 목록 DTO보다 많은 정보를 담는다.

    목록은 간결하게, 상세는 자세하게 준다.
    목록은 50행이라 행마다 join을 붙이면 비용이 크다.
    상세는 1건이므로 수행자 이름, 이슈 분해, 클립 수까지 싣는다.
    운영자가 이 Job에서 확인할 정보를 한 화면에 모두 담는다.
    """
    batch_id: UUID
    project_id: UUID
    source: Literal["upload", "ncp"]
    label: str | None = None
    note: str | None = None
    created_at: datetime
    created_by: UUID | None = None
    # 등록한 사람의 이름. 목록에는 없고 상세에서만 join한다.
    created_by_name: str | None = None

    # 폴더 등록 작업(서버 worker)의 상태. 진행 중이면 영상 수가 아직 늘고 있다.
    import_prefix: str | None = None
    import_status: Literal["pending", "running", "done", "failed"] | None = None
    import_error: str | None = None
    #: 폴더를 다시 등록할 때 이미 등록된 파일이라 건너뛴 수(`import_spec.skipped_existing`).
    #: 폴더 등록 묶음이 아니면 0이다.
    import_skipped_existing: int = 0

    # 규모
    video_count: int = 0
    deleted_videos: int = 0
    total_duration_seconds: float = 0.0

    # 준비 여정 분포. 세그먼트와 칩의 분모가 된다.
    prep: PrepStageCounts
    # 이슈 분해. 배너가 무엇부터 볼지 알려 줄 때 쓴다.
    issues: list[IngestJobIssue] = Field(default_factory=list)

    # 이 Job의 영상에서 나온 클립 수.
    clip_count: int = 0


class IngestBatchUpdate(BaseModel):
    """묶음 이름과 메모 수정 요청.

    운영자가 어떤 반입이었는지 나중에 알아볼 수 있게 한다.
    """
    label: str | None = Field(default=None, max_length=200)
    note: str | None = None


class VideoBucketCounts(BaseModel):
    """반입 작업 공간의 상태별 건수와 다음 단계 대기량.

    목록은 커서 페이지로 오므로, 화면에 실린 것만 세면 필터 배지가 잘못 표시된다.
    그래서 서버가 페이지와 관계없이 전체를 센다. `batch`를 주면 그 묶음으로 좁힌 값이다.
    """
    preparing: int   # 변환 중. 자동으로 진행되어 조치할 일은 없지만 수는 밝힌다
    ready: int
    analyzing: int
    done: int
    blocked: int
    total: int
    # 막힌 위치별 분해. Job 상세의 `issues`와 같은 CASE, 같은 모양이다.
    # 화면의 일괄 버튼이 `검증 재검사 N건`처럼 대상 수를 이름에 적으려면,
    # 커서 페이지에 실린 행이 아니라 이 수를 써야 한다.
    # 버튼이 실제로 처리하는 대상도 같은 조건의 `/videos/select`라서
    # 라벨과 대상이 어긋나지 않는다.
    issues: list[IngestJobIssue] = Field(default_factory=list)
    #: 분류별 영상 길이 합과 가장 이른 분석 요청 시각. 여섯 분류가 늘 모두 온다.
    #: 목록은 커서 페이지라 실린 행으로 길이를 더하면 분모가 틀린다.
    spans: list["IngestBucketSpan"] = Field(default_factory=list)


class VideoUploadLimits(BaseModel):
    """로컬 업로드 전에 알아야 할 제한. 크기 상한과 허용 확장자를 담는다.

    등록 시트가 열릴 때 한 번 조회한다.
    파일을 고르기 전에 알려 주어, 큰 파일이 영상 레코드를 만든 뒤에야 걸리지 않게 한다.
    """
    max_bytes: int
    # 화면이 목록 사본을 따로 갖지 않게 서버가 준다. 목록이 서로 다르면 화면이 받은 파일을
    # 서버가 거절하거나 그 반대가 된다.
    video_extensions: list[str]


class VideoSourceURLResponse(BaseModel):
    """브라우저 플레이어(Vidstack 등)가 직접 GET할 수 있는 presigned URL.

    유효 시간은 `expires_in`초이고 기본값은 1시간이다.
    """
    url: str
    expires_in: int
    # 내려주는 파일의 종류.
    # playback_480p(대역폭을 줄인 변환 재생본) 또는 original이다.
    # 화면이나 디버깅에서 화질이 다른 이유를 확인할 수 있어야 한다.
    variant: str = "original"


class VideoUploadCompleteRequest(BaseModel):
    """업로드 완료 요청.

    클라이언트 쪽 확인 값은 선택이다. 서버는 S3 HEAD 결과를 기준으로 판단한다.
    """
    expected_size_bytes: int | None = Field(default=None, ge=1)
    # 멀티파트 업로드의 id. 서버가 파트를 모아 업로드를 완료한다.
    upload_id: str | None = None
    # 중복 감지용 내용 해시. 멀티파트 ETag는 파트 해시들의 해시라 파트 크기가 다르면
    # 같은 파일도 값이 달라져 내용 대조에 쓸 수 없다. 대신 클라이언트가 파트마다 sha256을 구하고
    # 그 값들을 이어 붙여 다시 sha256한 값을 보낸다.
    # 파트 크기가 고정이면 같은 파일은 늘 같은 값이다.
    # 중복 표시에만 쓰고 판단은 사람이 하므로 값이 틀려도 등록을 막지 않는다.
    content_hash: str | None = Field(default=None, max_length=128)


class VideoMultipartInitiateResponse(BaseModel):
    """멀티파트 업로드 시작 응답. 파트 URL은 여기 없고, 필요할 때 따로 서명받는다."""
    upload_id: str
    s3_key: str
    part_size: int
    max_bytes: int
    # 이미 올라간 파트 번호. 이어 올리기에 쓴다. 새 업로드면 빈 목록이다.
    uploaded_parts: list[int] = Field(default_factory=list)


class VideoPartUrlsRequest(BaseModel):
    """서명받을 파트 번호 목록.

    느린 회선에서는 URL이 만료되므로 한 번에 모두 받지 않는다.
    """
    upload_id: str
    part_numbers: list[int] = Field(min_length=1, max_length=64)


class VideoPartUrlsResponse(BaseModel):
    """파트 번호별 presigned PUT URL."""
    urls: dict[int, str]
    expires_in: int


# 영상 반입 작업 공간의 상태 다섯 가지. 목록 필터·집계·행 표시가 모두 이 규칙을 쓴다.
# 판정은 BE의 SQL CASE 하나(`services/media/video._ingest_bucket_case`)에서만 한다.
# FE가 같은 규칙을 다시 구현하면 서로 달라진다.
IngestBucket = Literal[
    "preparing",  # 변환 중: 분석 입력 사본(h264) 준비. 자동으로 진행된다
    "ready",      # 분석 대기: MI 요청 가능
    "analyzing",  # 분석 중
    "done",       # 완료: 클립이 생성됐다
    "blocked",    # 막힘: 규격·업로드·티켓 누락·변환 실패
]


class IngestBucketSpan(BaseModel):
    """반입 상태 한 분류의 영상 길이 합과 가장 이른 분석 요청 시각.

    분석 요청 대기의 총 길이와 예상 비용, 분석 중의 가장 오래된 요청 경과가 이 값을 쓴다.
    """
    bucket: IngestBucket
    #: 길이 값이 있는 영상의 길이 합(초).
    total_seconds: float = 0.0
    #: 길이 값이 없는 영상 수. 0이 아니면 `total_seconds`는 하한이다.
    unmeasured: int = 0
    #: 이 분류 영상의 MI 분석 요청 중 가장 이른 요청 시각. 요청한 영상이 없으면 `None`.
    oldest_requested_at: datetime | None = None


VideoBucketCounts.model_rebuild()

# 준비 타임라인의 단계. 영상 상세의 단계별 현재 상태다.
# 판정과 조립은 서버(`video.prep_timeline`) 한 곳에서 한다.
# `scene_split`은 구간을 자르는 단계다. MI가 대신 처리하면 클립이 분석의 산출물이 된다.
# 이 단계를 드러내면 화면이 누가 구간을 자르는지 보여 줄 수 있다.
# 자르는 주체는 외부 분석 서비스, 샷 검출, 고정 길이, 사람(TC 입력) 중 하나다.
PrepRunStage = Literal["upload", "probe", "transcode", "deident", "publish", "register",
                       "scene_split", "analyze", "summarize"]


class PrepTimelineEntry(BaseModel):
    """준비 단계 1행. 지금 상태와 실패·건너뜀 사유, 재시도·건너뛰기 가능 여부를 담는다.

    `needed`는 사람이 실행해야 다음으로 넘어가는 단계다. 비식별화를 켜기 전에 처리된 영상의
    비식별화 단계가 이 값이고, 사유는 `note`에 있다. `skipped`는 진행을 막지 않는다.
    """
    stage: PrepRunStage
    status: Literal["ok", "running", "failed", "skipped", "pending", "needed"]
    # 진행률(0~1). 변환과 재생본 변환 단계만 채운다.
    # 분자(인코딩된 미디어 시간)나 분모(영상 길이)가 없으면 None이다.
    # 화면이 진행률을 지어내지 않게 한다.
    progress: float | None = None
    error: str | None = None
    skipped_reason: str | None = None
    # 이 영상을 그 단계로 다시 넣을 수 있으면 True. 판정은 서버만 한다.
    # 화면은 재시도 버튼을 이 값으로 정한다.
    # 비식별화를 켜기 전에 처리된 영상의 비식별화 단계(`needed`)는
    # `deident_status`가 `skipped`여도 실행할 수 있다.
    # 장면 요약 실패 줄은 요약 재요청이나 분석 재요청을 받을 수 있을 때만 True다.
    retryable: bool = False
    # 이 영상의 이 단계를 건너뛰고 다음 단계로 넘길 수 있으면 True.
    # 비식별화를 끈 프로젝트에서 실패로 확정된 비식별화 단계만 해당한다.
    # 그 영상은 재시도를 받지 않으므로 화면은 재시도 대신 건너뛰기를 둔다.
    skippable: bool = False
    # 비식별화 결과를 사람이 확인해야 하면 True. 비식별화 단계만 채운다.
    # 가릴 얼굴을 찾지 못하면 산출이 원본과 같아 얼굴이 그대로 나갈 수 있다.
    # 비식별화가 끝났어도 이 값이 참이면 화면은 경고로 표시한다. 사유 문장은 `review_note`에 있다.
    review_needed: bool = False
    review_note: str | None = None
    note: str | None = None                # 자동 재시도 대기, 처리 서버 무응답, 앞 단계 대기 안내


class PrepTimelineDTO(BaseModel):
    """영상 하나의 준비 타임라인."""
    video_id: UUID
    entries: list[PrepTimelineEntry]
    generated_at: datetime


class VideoSelectRequest(BaseModel):
    """조건 선택 요청. 조건에 맞는 영상 전체를 id 목록으로 바꾼다.

    선택 단위를 행이 아니라 조건으로 둔다.
    화면에 실린 행을 담으면 정렬이나 페이지가 바뀔 때 선택이 틀어지고,
    큰 Job에서 한 상태의 영상 전부를 고르려면 페이지를 넘겨 가며 눌러야 한다.

    `exclude_ids`는 조건에서 개별로 뺀 영상이다. 사용자가 전체를 선택한 뒤 두 건만 뺄 수 있다.
    """
    batch: UUID | None = None
    prep: PrepStage | None = None
    # 이슈 위치. 레일이 "변환 실패 3건만 재시도"처럼 정확한 대상을 고를 때 쓴다.
    blocked_stage: PrepBlockedStage | None = None
    bucket: IngestBucket | None = None
    q: str | None = Field(default=None, max_length=200)
    exclude_ids: list[UUID] = Field(default_factory=list, max_length=500)


class VideoSelectResult(BaseModel):
    """조건을 해석한 결과. 분모(`total`)는 서버가 정한다.

    `truncated`면 상한(`_SELECT_CAP`)에 걸렸다는 뜻이다. 화면은 이 사실을 밝힌다.
    "88건 전부"라고 표시하고 일부만 처리하면 잘못된 표시가 된다.
    """
    video_ids: list[UUID]
    total: int
    truncated: bool = False


# MI 라이브러리의 수명 주기 단계. 배지 표시와 '분석 대기'(선택 가능) 판정에 쓴다.
MediaPipelineStage = Literal[
    "registered",         # 등록 완료
    "awaiting_analysis",  # 분석 대기: MI 요청 가능(선택 대상)
    "analyzing",          # MI 분석 진행 중
    "analyzed",           # 분석 완료(클립 생성)
    "failed",             # 분석 실패
]


class DispatchDeferred(BaseModel):
    """일시 오류로 보류된 분석 요청. 서버가 잠시 뒤 다시 요청한다.

    짧은 이름(`label`)은 서버가 상태 코드로 정한 종류의 이름이다. 화면은 사유 문장(`reason`)을
    나눠 종류를 다시 정하지 않는다.
    """
    #: 종류(`throttled`·`timeout`·`server_error`·`unreachable`·`other`). 옛 표시는 `None`이다.
    kind: str | None = None
    #: 요청 대기 행에 붙이는 짧은 사유. 예: `분석 서비스 요청량 제한`.
    label: str
    #: 분석 서비스가 돌려준 사유 전체. 마우스를 올리면 보인다.
    reason: str | None = None
    #: 보류된 시각.
    at: datetime | None = None
    #: 다음 자동 재요청이 늦어도 시작되는 시각. 옛 표시는 `None`이다.
    retry_by: datetime | None = None


class VideoDTO(BaseModel):
    """영상 한 건의 정보."""
    video_id: UUID
    project_id: UUID
    title: str
    source_uri: str | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    copyright_policy: CopyrightPolicy
    upload_status: UploadStatus
    broadcaster: str | None = None
    program_name: str | None = None
    genre: str | None = None
    episode: str | None = None
    aired_at: datetime | None = None
    upload_batch_id: UUID | None = None     # 반입 배치. 다음 단계에서 이 묶음만 걸러 볼 때 쓴다
    probe_status: ProbeStatus = "pending"   # 규격 검사 상태. 상세는 metadata["probe"]에 있다
    # 트랜스코딩 파이프라인 상태의 열 값. 상세(사유·산출 키)는 metadata["transcode"]에 있다.
    # 규격 검증 전에는 열 기본값 `skipped`라서 변환 여부가 정해지지 않은 영상도 `skipped`다.
    # 진행 위치는 `prep_stage`와 준비 타임라인이 판정한다.
    transcode_status: str = "skipped"
    metadata: dict
    #: 분석본 사본(`analysis_uri`)이 기록되어 있으면 참.
    #: 관리자 화면이 분석본 정리 버튼을 보일지 정한다.
    #: 정리할 수 있는지는 정리 API가 판정하고, 거절하면 사유 문장을 돌려준다.
    has_analysis_copy: bool = False
    #: 일시 오류로 보류된 분석 요청. 보류가 아니면 `None`이다.
    dispatch_deferred: DispatchDeferred | None = None
    # 대표 썸네일(첫 클립의 씬 프레임, 새로 발급한 presign). 목록 표시에 쓴다.
    thumbnail_url: str | None = None
    # MI 라이브러리 정보. 수명 주기 단계와 클립 수를 담는다.
    # 목록에서는 한 번에 계산하고, 단건 등록 직후에는 기본값이다.
    clip_count: int = 0
    pipeline_stage: MediaPipelineStage = "registered"
    # 반입 작업 공간 상태. 서버가 목록 필터·집계와 같은 SQL CASE로 판정한다.
    # 목록에서만 채운다.
    ingest_bucket: IngestBucket = "ready"
    # 준비 여정 단계. 목록에서만 채우고, 단건 조회에서는 계산하지 않아 None이다.
    prep_stage: PrepStage | None = None
    prep_blocked_stage: PrepBlockedStage | None = None
    # 분석이 끝난 뒤 이 영상의 클립이 지금 있는 단계별 수.
    # 화면이 '완료' 행의 다음 단계를 정할 때 쓴다.
    labeling_clips: int = 0     # available + claimed
    reviewing_clips: int = 0    # submitted
    approved_clips: int = 0
    #: 삭제하면 닫힐 라벨링·검수 작업 수.
    #: 삭제 확인 창이 무엇이 사라지는지 알려 줄 때 쓴다.
    #: 비가역 액션의 판단 근거는 같은 화면에 있어야 한다.
    #: 위 네 값의 합과 다르다. `rejected`(반려돼 재작업 중)는 더하고 `approved`는 뺀다.
    #: 승인분은 납품을 위해 남긴다. 판정은 `work.service._CLOSABLE` 한 곳에서 한다.
    open_work_items: int = 0
    created_at: datetime

