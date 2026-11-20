"""납품 클립 메타데이터 모델.

클립 메타데이터 JSON Schema 정의서와 같은 내용을 코드로 둔다.
검증기와 스키마를 따로 두면 한쪽만 고쳐 둘이 달라진다.
그래서 이 모델 하나로 검증하고, 패키지에 넣는 `schema/clip-metadata-v1.json`도
이 모델에서 만든다(`model_json_schema()`, Draft 2020-12).

내용 필드 중 필수는 캡션뿐이고 나머지는 있으면 넣는다.
시스템이 늘 채우는 식별·시간 필드(`clip_id`, `source_video_id`, `start_time`, `end_time`,
`duration`)도 필수다. 이 필드들이 레코드가 가리키는 대상을 정한다.

- `description`·`keywords`·`speakers`·`review`와 출처 메타(`broadcaster`·`program_name`·
  `genre`·`aired_at`·`source_duration`)는 선택이다.
- `genre`는 자유 문자열이다. 워크벤치가 자유 입력이기 때문이다.
- `media_path`는 쓰지 않는다. 패키지에 미디어를 싣지 않는다.

`start_time`·`end_time`은 고객이 가진 원본 파일 기준 시각이다.
분석이 트랜스코딩한 마스터를 읽었다면 그 시간축은 원본과 다를 수 있다.
그래서 조립할 때 오프셋을 더한다.
받는 쪽이 검증할 수 있게 `source_start_offset`에 그 값을 싣는다.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClipDescription(BaseModel):
    """세부 묘사. 입력란이 없어 늘 빈 값으로 싣는다.

    납품 형식을 유지하려고 키를 둔다. AI 결과는 스택마다 모양이 달라 이 값으로 싣지 않는다.
    """

    persons: list[str] = []
    location: str = ""
    actions: list[str] = []
    events: list[str] = []
    on_screen_text: list[str] = []


class ClipSpeaker(BaseModel):
    """화자 구간. 사람이 검토·편집한 것만 싣는다. 편집 기능이 없어 늘 빈 목록이다."""

    speaker_id: str
    start_time: float
    end_time: float
    role: str | None = None
    text: str | None = None


class ClipDeidentification(BaseModel):
    """비식별화 정보. 라벨링에 쓴 작업 사본에 적용한 비식별화를 적는다.

    패키지는 미디어를 담지 않고 클립은 원본을 가리키므로, 이 값은 원본 파일이 아니라 작업
    사본의 처리 기록이다. 처리하지 않은 영상은 기본값(`processed: false`, `none`)으로 나간다.
    필수 필드라서 빼지 않는다. 기본값은 처리하지 않았다는 뜻이다.
    처리 여부를 확인하지 못했다는 뜻과 구분한다.
    `method`는 작업 사본에서 영역을 가린 방식이다. 처리했지만 가릴 얼굴을 찾지 못해 가린 영역이
    없으면 `processed: true`, `none`이고 `suspected_miss`가 참이다.
    값을 채우는 코드는 `export_package.deidentification_of`에 있다.
    """

    processed: bool = False
    method: Literal["blur", "mosaic", "none"] = "none"
    suspected_miss: bool = False


class ClipReview(BaseModel):
    """검수 정보. 승인(`approved`)된 클립만 나가므로 상태는 상수다.

    시각은 마지막 승인 판정에서 가져온다.
    `revision_count`는 검수에서 고친 횟수다. 반려되어 다시 낸 횟수와 검수자가 고쳐서 승인한
    횟수의 합이다. 한 번에 승인된 클립은 0이다.
    `reviewer_id`는 비워서 내보낸다.
    내부 사용자 ID는 개인정보 성격이고 받는 쪽이 쓸 곳이 없다.
    옛 패키지와 받는 쪽 코드가 이 키를 쓰므로 필드는 지우지 않는다.
    값을 채우는 코드는 `export_package`에 있다.
    """

    status: Literal["approved"] = "approved"
    reviewer_id: UUID | None = None
    reviewed_at: datetime
    revision_count: int = 0


class ClipResolution(BaseModel):
    """영상 해상도."""
    width: int
    height: int


class ClipMetadata(BaseModel):
    """클립 하나의 납품 메타데이터. `metadata/{clip_id}.json`으로 내보낸다.

    정의되지 않은 필드는 거절한다(필드명 정합성).
    pydantic 기본값(`extra="ignore"`)은 정의되지 않은 키를 오류 없이 버린다.
    그러면 조립 코드(`export_package.build_metadata`)에서 `on_screen_txt=`처럼 필드명을
    한 글자만 틀려도 예외 없이 그 값이 빠진 채 나간다.
    거절하면 그 행이 `정의되지 않은 항목` 사유로 제외 목록에 뜬다.
    그래서 누락이 제외 목록에 드러난다.

    최상위 필드만 막는다. 납품 정의서의 스키마가 최상위에만
    `additionalProperties: false`를 두고 하위 객체는 열어 두었다.
    `deidentification.regions`처럼 문서에만 있는 하위 키도 있다.
    하위까지 막으면 코드가 문서보다 엄격해진다.
    그러면 문서만 보고 레코드를 만든 쪽을 이 검증기가 거절한다.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"

    clip_id: UUID
    source_video_id: UUID

    # 원본 기준 시각. 아래 offset을 이미 더한 값이다.
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    duration: float = Field(gt=0)
    # 분석이 원본이 아닌 파일을 읽었을 때 더한 값(초). 0이면 분석이 원본을 그대로 읽었다는 뜻이다.
    source_start_offset: float = 0.0

    # 출처 메타데이터. 값이 없으면 없는 대로 내보낸다(계약상 필수는 캡션뿐이다).
    # 등록이나 라벨링에서 채워지면 싣고, 채워지지 않았다고 클립을 빼지 않는다.
    broadcaster: str | None = None
    program_name: str | None = None
    episode: str | None = None
    genre: str | None = None
    aired_at: datetime | None = None
    source_duration: float | None = None
    # 원본 파일의 sha256. 받는 쪽이 자기 파일이 분석한 파일과 같은지 대조할 때 쓴다.
    # 패키지에 미디어를 싣지 않으므로 이 값이 유일한 확인 수단이다.
    # 아직 계산하지 않았으면 `null`이다. 없다고 내보내기를 막지 않는다.
    # 검증을 돕는 값이며 정합성 조건에 넣지 않는다.
    source_checksum: str | None = None

    # 캡션. 내용 필드 중 유일한 필수 값이고 학습 데이터의 본체다.
    # 상한은 긴 보도 클립의 설명이 들어가는 길이다. 장면이 많은 클립은 설명이 4,000자를 넘는다.
    caption: str = Field(min_length=10, max_length=5000)
    description: ClipDescription = ClipDescription()
    keywords: list[str] = []
    speakers: list[ClipSpeaker] = []

    transcript: str | None = None
    scene_summary: str | None = None

    deidentification: ClipDeidentification = ClipDeidentification()
    # 판정 레코드가 없는 옛 데이터가 있어 선택 값이다.
    # 값이 없으면 기록이 빠진 경우이고 클립 결함으로 보지 않는다.
    review: ClipReview | None = None

    resolution: ClipResolution | None = None
    fps: float | None = None


#: 납품 스키마에 싣는 모델 설명. 받는 쪽이 읽는 글이다.
#: 클래스 docstring은 개발자용 설명이라 그대로 두면 내부 사정이 납품 파일에 실린다.
#: 모델을 더하면 여기에도 더한다. 빠지면 `json_schema()`가 KeyError를 낸다.
PUBLIC_DESCRIPTIONS: dict[str, str] = {
    "ClipMetadata": "클립 하나의 납품 메타데이터. metadata/{clip_id}.json으로 내보낸다.",
    "ClipDescription": "세부 묘사. 형식을 유지하려고 두는 항목이며 값은 비어 있다.",
    "ClipSpeaker": "화자 구간. 형식을 유지하려고 두는 항목이며 목록은 비어 있다.",
    "ClipDeidentification": (
        "비식별화 정보. 라벨링에 쓴 작업 사본의 처리 기록이다. method는 작업 사본에서 영역을 가린"
        " 방식이다. 처리했지만 가릴 얼굴을 찾지 못해 가린 영역이 없으면 processed는 true,"
        " method는 none, suspected_miss는 true다."
    ),
    "ClipReview": "검수 정보. 승인된 클립만 납품하므로 상태는 approved다.",
    "ClipResolution": "영상 해상도.",
}


def json_schema() -> dict:
    """패키지에 넣을 `schema/clip-metadata-v1.json` 내용을 만든다. 검증기와 같은 모델을 쓴다.

    모델 설명은 클래스 docstring 대신 `PUBLIC_DESCRIPTIONS`로 바꿔 싣는다.

    Raises:
        KeyError: 스키마에 `PUBLIC_DESCRIPTIONS`에 없는 모델이 있는 경우.
    """
    s = ClipMetadata.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = "https://rebornstudio.kr/schema/clip-metadata-v1.json"
    s["title"] = "RebornStudio Clip Metadata"
    s["description"] = PUBLIC_DESCRIPTIONS["ClipMetadata"]
    for name, sub in s.get("$defs", {}).items():
        sub["description"] = PUBLIC_DESCRIPTIONS[name]
    return s
