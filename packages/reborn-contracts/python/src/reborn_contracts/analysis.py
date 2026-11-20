"""분석 결과 도메인 모델. 벤더 응답 대신 이 모델이 분석 결과의 기준이다.

계약을 먼저 두어 첫 어댑터 구현의 모양이 그대로 계약이 되지 않게 한다.
어댑터는 형식 변환만 한다. 각 벤더 응답을 이 모양으로 옮긴다.
벤더 응답에 없는 값은 비워 둔다. 0으로 채우면 화면이 값이 있는 것으로 잘못 표시한다.

어댑터는 자기 능력을 선언하고, 네 곳이 그 선언을 읽는다.
화면, 프로젝트 생성, 계약 테스트, 파이프라인이다.
선언한 능력의 결과가 비어 있으면 결함이다. 선언하지 않은 능력의 결과가 비어 있는 것은 정상이다.
계약 테스트가 이 둘을 구분한다.

샷 경계와 의미 단위는 다른 능력이다.
보도영상은 컷이 2~5초마다 바뀐다. MI 클립은 이보다 훨씬 길다.
MI는 컷 경계 대신 의미 단위로 자른다.
컷 검출로 바꾸면 같은 영상이 수백 개로 나뉘어 라벨링 작업량과 분석 비용이 10배 이상이 된다.
컷 검출로 바꾸는 것은 작업 단위를 바꾸는 결정이므로 두 능력을 한 이름으로 부르지 않는다.
"""
from enum import StrEnum

from pydantic import BaseModel, Field


class Capability(StrEnum):
    """어댑터가 선언하는 능력. 계약 테스트는 선언한 능력만 검사한다."""

    # 컷(샷) 경계. 자체 구현(PySceneDetect)으로도 만들 수 있다.
    SHOT_BOUNDARIES = "shot_boundaries"
    # 의미 단위 세그먼트(기사·리포트). 모델이 필요하고 MI가 이 능력을 제공한다.
    SEMANTIC_SEGMENTS = "semantic_segments"
    SCENE_SUMMARY = "scene_summary"
    SPEECH_TO_TEXT = "speech_to_text"
    # 화자 분리. STT가 있어도 화자를 구분하지 않는 스택이 있다.
    SPEAKER_DIARIZATION = "speaker_diarization"
    KEYWORDS = "keywords"
    # 캡션 텍스트 품질 검토. 맞춤법·종결·부적절 표현·용어 일관성을 본다.
    # 위 능력들은 영상에서 뽑아낸다. 이 능력은 사람이 쓴 글을 읽는다.
    # 그래서 영상 어댑터는 이 능력을 갖지 않고, 캡션 검토 어댑터는 영상 능력을 갖지 않는다.
    # 영상과 캡션의 유사도 능력은 없다.
    CAPTION_QC = "caption_qc"


#: 분석 스택이 하는 일, 곧 영상에서 뽑는 능력의 집합. `CAPTION_QC`만 뺀다.
#:
#: 캡션 QC는 영상 대신 사람이 쓴 글을 나중에 읽는다. 분석 능력 목록에 캡션 QC를 두면
#: 어떤 어댑터도 채울 수 없는 능력이 남아 분석이 빠진 것처럼 보인다.
#: 새 분석 능력을 더하면 여기에도 넣는다. 새 QC 능력은 넣지 않는다.
ANALYSIS_CAPABILITIES: frozenset[Capability] = frozenset(Capability) - {Capability.CAPTION_QC}


class Segment(BaseModel):
    """시간 구간 하나. 씬(의미 단위)과 샷이 같은 모양을 쓴다.

    `summary`는 선택이다. 구간은 주지만 요약은 주지 않는 스택이 있다.
    그때 빈 문자열을 넣으면 화면이 요약이 있는 것으로 표시한다.
    """

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    summary: str | None = None


class Speech(BaseModel):
    """발화 하나.

    `speaker_id`가 없으면 화자를 구분하지 않았다는 뜻이다. 화자가 한 명이라는 뜻으로 읽지 않는다.
    """

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str | None = None
    speaker_id: str | None = None


class Keyword(BaseModel):
    """키워드 하나. `category`는 분류를 주는 스택에서만 채워진다."""

    text: str = Field(min_length=1)
    category: str | None = None


class SegmentBasis(BaseModel):
    """구간을 만든 근거. 화면이 구간 수의 근거를 보여 줄 때 쓴다.

    예: 샷 404개를 78개 구간으로 묶었다(샷 경계 · 최대 40초 · 무음 구간 우선).

    개수만 보여 주면 그 수가 많은지 적은지 판단할 수 없다.
    묶기 전 개수와 규칙을 함께 줘야 운영자가 임계값을 낮출지 판단할 수 있다.
    """

    #: 묶기 전 개수. 여기서는 샷 수다.
    raw_count: int
    #: 묶은 뒤 개수. `segments` 길이와 같다.
    merged_count: int
    #: 무엇으로 끊었는지 사람이 읽는 한 줄.
    rule: str


class AnalysisResult(BaseModel):
    """한 영상의 분석 결과. 이 모양이 기준이고, 어댑터가 벤더 응답을 이 모양으로 옮긴다.

    세 목록이 모두 비어 있어도 정상인 스택이 있다(더미의 빈 결과 모드).
    화면은 무엇이 비었는지를 능력 선언과 대조해서 보여 준다.
    """

    segments: list[Segment] = []
    speeches: list[Speech] = []
    keywords: list[Keyword] = []
    #: 구간을 만든 근거. 벤더가 자르면 묶기 전 개수가 없어 `None`이다.
    #: `None`은 세지 않았다는 뜻이다. 화면은 0과 다르게 표시한다.
    basis: SegmentBasis | None = None


class AdapterInfo(BaseModel):
    """어댑터의 능력 선언. 화면·프로젝트 생성·계약 테스트·파이프라인이 읽는다.

    `segment_kind`는 `segments`가 어떤 종류의 구간인지 밝힌다.
    샷과 의미 단위는 같은 배열에 담기지만 작업량이 10배 다르다.
    이름이 같으면 화면이 그 차이를 보여 줄 수 없다.
    """

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    capabilities: frozenset[Capability]

    @property
    def segment_kind(self) -> str | None:
        """구간 종류를 돌려준다.

        `"shot"`, `"semantic"` 중 하나다. 둘 다면 `"both"`, 구간을 주지 않으면 `None`이다.
        """
        shot = Capability.SHOT_BOUNDARIES in self.capabilities
        sem = Capability.SEMANTIC_SEGMENTS in self.capabilities
        if shot and sem:
            return "both"
        if shot:
            return "shot"
        if sem:
            return "semantic"
        return None

    def can(self, cap: Capability) -> bool:
        """이 어댑터가 `cap` 능력을 선언했는지 돌려준다."""
        return cap in self.capabilities
