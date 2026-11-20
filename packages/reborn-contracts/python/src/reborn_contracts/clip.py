"""클립 도메인 스키마. 클립은 MAIU 분석 결과의 segment 단위다."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from reborn_contracts.video import ClipProgress, VideoMetaDTO
from reborn_contracts.work import WorkStatus

ClipRightsStatus = Literal[
    "auto_cleared", "cleared", "cleared_with_edit", "rejected", "legal_review",
]


class ClipDeidentReport(BaseModel):
    """라벨러나 검수자가 남긴 비식별화 미흡 신고. 관리자가 재개·제외를 정하는 근거다."""
    reason: str | None = None
    timestamp_ms: int | None = Field(default=None, description="신고 당시 재생 위치(클립 기준 ms)")
    reported_at: datetime
    #: 신고한 사람의 이름. 계정이 없어졌으면 None.
    reporter_name: str | None = None


class DeidentReportRow(BaseModel):
    """비식별화 미흡 신고로 작업이 멈춘 클립 한 건(`rights_status=legal_review`)."""
    clip_id: UUID
    video_id: UUID
    video_title: str | None = None
    start_seconds: float
    end_seconds: float
    #: 판단용 장면 대표 프레임. 새로 발급한 presign 주소다.
    scene_thumbnail_url: str | None = None
    #: 가장 최근 신고. 신고 이벤트가 정리되어 없으면 None.
    report: ClipDeidentReport | None = None


class DeidentReportPage(BaseModel):
    """비식별화 신고 목록. `total`은 조회 상한과 무관한 전체 수다."""
    total: int
    items: list[DeidentReportRow]


class DeidentReportResolveRequest(BaseModel):
    """비식별화 신고 처리 요청.

    `resume`은 작업을 대기열로 되돌리고 `exclude`는 클립을 납품에서 뺀다.
    """
    action: Literal["resume", "exclude"]
    #: 처리 사유. 제외할 때는 필수다.
    note: str | None = Field(default=None, max_length=1000)


class DeidentReportResolveResult(BaseModel):
    """비식별화 신고 처리 결과. 클립의 새 상태와 그 처리로 바뀐 작업 수를 담는다."""
    clip_id: UUID
    rights_status: ClipRightsStatus
    #: 재개로 대기열에 돌아간 작업 수.
    resumed_work_items: int = 0
    #: 제외로 닫힌 작업 수.
    closed_work_items: int = 0


#: 클립 길이 하한을 정하지 않은 프로젝트에서 사람이 넣거나 고치는 구간의 최소 길이(초).
#: 클립 길이 설정의 하한(`scene_options.SceneOptions.min_seconds`)이 받는 가장 작은 값과 같다.
#: 더 짧은 구간은 라벨러가 볼 수 없는 조각이 되고, 만든 뒤 지울 경로도 없다.
#: 하한을 정한 프로젝트는 그 값을 쓴다. 판정은 서버 `scene_options.clip_min_seconds` 하나다.
CLIP_MIN_SECONDS = 10


class ClipCreateRequest(BaseModel):
    """사람이 직접 씬 구간을 만드는 요청.

    MI가 씬을 주는 스택에서는 보정 기능이다.
    구간 능력이 없는 스택에서는 씬을 만드는 유일한 방법이라 필수 경로다.

    구간만 받는다. 캡션과 요약은 라벨링에서 채운다.
    여기서 함께 받으면 구간을 만들며 쓴 캡션과 라벨링 작업으로 쓴 캡션이 섞인다.
    그러면 나중에 누가 쓴 캡션인지 알 수 없다.
    최소 길이는 프로젝트마다 달라 여기서 보지 않고 서버가 프로젝트 하한으로 판정한다
    (`ProjectDTO.clip_min_seconds`).
    """

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ClipCreateRequest":
        """끝이 시작보다 뒤인지 검사한다."""
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds는 start_seconds보다 커야 합니다")
        return self


#: 라벨링 티켓이 생기는 클립 상태. 정상 상태를 정하는 단일 정의.
#: `work.service`가 티켓을 만들 때와 `export_package`가 납품에 넣을 때 같은 결과를 내야 한다.
RIGHTS_CLEARED: tuple[ClipRightsStatus, ...] = ("auto_cleared", "cleared", "cleared_with_edit")




class ClipCountDTO(BaseModel):
    """프로젝트 클립 목록과 같은 조건으로 센 클립 수. 커서 목록의 분모다.

    목록은 커서로 나눠 받아서 화면에는 받은 행만 있다. 분모가 없으면
    "불러온 240개 · 더 있음"처럼 전체 수를 표시하지 못한다.
    """

    total: int


class ClipDTO(BaseModel):
    """클립 한 건을 외부에 노출하는 DTO. `clips` 테이블에서 만든다.

    `auto_caption_brief`는 NaverMAIU summary의 `brief`에서 뽑은 1줄이다.
    전체 caption은 너무 커서 싣지 않는다.
    """
    clip_id: UUID
    video_id: UUID
    project_id: UUID
    video_title: str | None = None  # 프로젝트 단위 목록에서만 채운다
    #: 이 클립이 영상에서 물려받는 메타 네 가지. 단건 조회(`get_one`)에서만 채운다.
    #: 라벨링·검수 워크벤치가 읽기 전용으로 보여 준다.
    #: 목록에서는 행마다 영상을 읽지 않으므로 `None`이다.
    video_meta: VideoMetaDTO | None = None
    start_seconds: float
    end_seconds: float
    rights_status: ClipRightsStatus
    auto_caption_brief: str | None = None
    scene_summary: str | None = None  # MAIU 장면 요약(VLM) 자연어 description
    scene: int | None = None  # MAIU scene 번호 (clip = 1 scene)
    speakers: list[dict] = []  # [{pid, name}]. 화자 이름 편집에 쓴다
    keywords: list[dict] = []  # MAIU keywords [{text, group, category, score}]
    speeches: list[dict] = []  # MAIU STT [{startMs, endMs, text, pid, name}]. 미리보기 메타 토글
    scene_thumbnail_url: str | None = None  # MAIU 장면 대표 이미지
    # 썸네일 생성이 시도 상한에 걸려 멈춘 사유(화면 문구). 멈추지 않았으면 None이다.
    # 화면은 이 값으로 빈 썸네일이 생성 대기인지, 더는 생성되지 않는지 구분한다.
    # 재생성은 영상 단위 액션이다.
    thumbnail_error: str | None = None

    # 라벨링 작업의 현재 상태. 분석 결과와는 별개의 값이다.
    # 클립 구간을 보는 사람은 다음으로 작업 진행을 확인하므로 함께 준다.
    # `None`은 아직 티켓이 없다는 뜻이다(비식별화 신고로 멈췄거나 제외됨). `available`과 다르다.
    work_status: WorkStatus | None = None
    # 라벨링 작업 id. 승인된 작업이면 완료 상세로 이동할 수 있다.
    # 완료 상세에 최종 캡션·구간·이력·납품 가능 여부가 있다.
    work_item_id: UUID | None = None
    #: 납품까지의 진척 요약(`ClipProgress`). 제외된 클립은 None이다.
    #: 판정은 `services/media/progress.py` 한 곳에서 한다.
    progress: ClipProgress | None = None

    #: 이 구간을 만든 주체. `maiu`, `scenedetect`, `manual` 중 하나다.
    #: 분석 스택은 프로젝트 프리셋마다 다르다.
    #: 화면이 서비스 이름을 추정해 쓰면 다른 프리셋에서 잘못 표시한다.
    #: 이 값을 기록하기 전의 옛 클립은 `None`이다. 출처를 추정해 채우지 않는다.
    source: str | None = None

    created_at: datetime
