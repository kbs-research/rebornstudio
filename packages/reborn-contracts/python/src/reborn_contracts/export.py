"""내보내기(납품 패키지) 도메인 스키마."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from reborn_contracts.review import RejectReasonRef
from reborn_contracts.video import VideoMetaDTO

ExportStatus = Literal["pending", "running", "completed", "failed"]


class ExportDTO(BaseModel):
    """내보내기 한 건. 이력의 한 행이 된다."""

    export_id: UUID
    # 검수용 샘플에 담기로 한 건수. 전량이면 None이다.
    # 화면은 둘을 다른 이름으로 표시해 샘플을 납품본으로 오해하지 않게 한다.
    sample_size: int | None = None
    #: 참이면 납품 메타데이터에서 값이 늘 비어 있는 선택 항목(세부 묘사·키워드·화자 구간·음성 인식·
    #: 장면 요약) 키를 뺀 패키지다. 화면은 이력 행에 표시해 같은 프로젝트의 두 패키지가
    #: 왜 키 구성이 다른지 알 수 있게 한다.
    omit_optional: bool = False
    project_id: UUID
    created_by: UUID
    #: 만든 사람의 이름. UUID만 주면 화면이 사람을 표시할 수 없다.
    #: 같은 날 두 번 나간 패키지를 누가 만들었는지 아는 것이 이력의 목적이다.
    #: 계정이 삭제됐으면 `None`이다. 없는 값을 빈 문자열로 채우지 않는다.
    created_by_name: str | None = None
    #: `pending`(워커 대기) · `running`(워커가 만드는 중) · `completed` · `failed`.
    status: ExportStatus
    manifest_s3_key: str | None = None
    item_count: int
    #: 실패·재시도 사유 코드(`export_upload_retrying` · `export_upload_failed` ·
    #: `export_build_failed` · `export_integrity_failed` · `export_worker_lost` ·
    #: `export_failed`). 화면은 오류 사전(`errorText`)에서 코드에 맞는 문장을 찾아 표시한다.
    #: 파이썬 예외 원문은 운영 로그에만 남긴다. 원문을 실으면 boto3 영문이 이력 행에 뜬다.
    #: 완료된 내보내기는 `None`이다.
    error: str | None = None
    #: 완료된 내보내기에서 납품 관문(비식별화 신고·축 보정·스키마)에 걸려 뺀 건수.
    #: 화면이 zip을 풀어 보지 않고도 뺀 건수를 보여 줄 수 있게 따로 싣는다.
    #: 완료 목록의 `납품 불가`와 같은 정의다. 품질검수 보류분은 `qc_held_count`에 따로 싣는다.
    #: 둘의 합이 `report.json`의 `excluded`다.
    excluded_count: int | None = None
    #: 완료된 내보내기에서 캡션 품질검수 보류로 뺀 승인분 수. 샘플은 늘 0이다.
    #: 두 수를 나누기 전에 만든 내보내기는 `None`이다.
    #: 그 행의 `excluded_count`에는 보류분이 섞여 있다.
    qc_held_count: int | None = None
    #: 워커가 이 내보내기를 시도한 횟수(재시도·회수 포함)와 그 상한.
    #: 화면은 `재시도 2/5`처럼 표시한다.
    #: 대기가 길어질 때 처음 시도 중인 것과 세 번째 실패 뒤 대기 중인 것은 상황이 다르다.
    attempts: int = 0
    max_attempts: int = 0
    #: 내려받을 때 쓰는 파일 이름(`{slug}-{YYYYMMDD-HHMM}-{ID 앞 8자리}[-sample{N}].zip`, KST).
    #: 다운로드 링크도 이 이름으로 서명한다.
    #: 이름이 같으면 전량본·샘플·재전달분을 다운로드 폴더에서 구별할 수 없다.
    filename: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    #: 승인 검사를 기다리느라 시작을 미루고 있는 대기 행이면 미루기가 끝나는 시각
    #: (요청 시각 + `export_service._QC_WAIT`). 미루지 않는 행은 `None`이다.
    #: 화면은 이 값이 있는 동안 "처리 지연"으로 보지 않고 대기 사유를 적는다. 요청 시각만으로 재면
    #: 서버가 일부러 미루는 동안에도 처리 서버가 멈췄다고 알리고 추가 버튼을 다시 연다.
    qc_wait_until: datetime | None = None


class ExportDownloadResponse(BaseModel):
    """내보내기 패키지의 다운로드 링크. 링크는 `expires_in`초 뒤 만료된다."""
    download_url: str
    expires_in: int
    item_count: int


# ── 승인본 캡션 ─────────────────────────────────────────────────────────────────
# 완료 목록·상세가 싣는 캡션 모양이다. `work_items.payload`를 통째로 싣지 않는다.
# 워크벤치가 payload 키를 바꿀 때마다 화면이 받는 형식이 예고 없이 바뀌기 때문이다.


class ExportCaption(BaseModel):
    """최종 승인본 캡션.

    문장 배열이 원본이고, 문자열은 문장 배열에서 만든다.

    캡션은 하나다. 라벨링 화면에 상세 캡션 입력란이 없고
    납품 클립 메타데이터 스키마에도 대응 항목이 없다.
    """

    brief: str = ""
    brief_sentences: list[dict[str, Any]] = []


# ── 완료 목록 ──────────────────────────────────────────────────────────────────
#
# 완료 탭은 내보내기에 실리는 것을 보여 준다.
# 그래서 서버는 `approved_rows_stmt` 하나를 두 곳에서 쓴다.
# 화면이 따로 세면 화면과 실제로 나가는 것이 달라진다.
# 그 차이는 내보내기를 받아 보기 전에는 드러나지 않는다.


class CompletedRow(BaseModel):
    """완료된 작업 한 줄. 클립 하나 또는 클립이 없는 영상 단위 작업 하나."""

    work_item_id: UUID
    video_id: UUID
    clip_id: UUID | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None

    video_title: str | None = None
    program_name: str | None = None
    broadcaster: str | None = None
    aired_at: datetime | None = None

    caption_brief: str = ""
    # 삭제하지 않은 캡션 문장 수.
    sentence_count: int = 0

    # 클립 상태. 완료 탭은 이 값으로 거르지 않고 보여 준다.
    # 거르면 화면과 내보내기가 달라진다.
    # 나가면 안 되는 것이 여기 보이면 내보내기 쪽의 결함이다.
    rights_status: str | None = None

    # 승인 직후 검사의 방식.
    # `model`은 모델까지 실행한 경우, `rules`는 모델을 부르지 못한 경우,
    # `None`은 검사가 실행되지 않은 경우다.
    # 셋을 합치지 않는다. `rules`는 검사를 했지만 규칙만 적용했다는 뜻이다.
    # `None`은 승인 당시 검사 기능이 없었다는 뜻이다.
    # 하나로 세면 검사 기능이 생기기 전의 승인분이 모두 경고가 된다.
    qc_checked_by: str | None = None
    # 승인본 검사가 아직 끝나지 않았으면 True.
    # 승인은 검사를 기다리지 않고 커밋되고 검사는 워커가 한다.
    # 이 값이 없으면 승인 직후 몇 초 동안 `qc_checked_by`가 `None`이라
    # 검사 기능이 없던 승인분처럼 보인다.
    # 판정은 서버 한 곳(`caption_qc.backlog`)이 한다.
    qc_running: bool = False

    # 지금 상태로 패키지에 실리면 True. 목록에서 먼저 알린다.
    # 판정은 패키지가 쓰는 검사를 그대로 쓴다(`export_package.check_row`).
    # 이 값이 없으면 패키지를 받아 보기 전에는 실리지 않는 건을 알 수 없다.
    deliverable: bool = True
    # 실리지 않는 사유를 필드별로 담는다. 사유가 없으면 무엇을 고쳐야 할지 알 수 없다.
    blockers: list[str] = []

    submitted_at: datetime | None = None
    approved_at: datetime | None = None


#: 완료 목록의 필터 기준. 서버가 거르고 서버가 센다.
#: 화면이 거르면 페이지에 실린 50건 안에서만 걸러져 `없음`이 잘못 표시된다.
#: 판정은 서버의 기존 정의를 그대로 쓴다. `deliverable`·`excluded`는 패키지가 쓰는 검사
#: (`export_package.check_row`)를, `qc_rules`는 `qc_unreviewed_total`과 같은 정의를 쓴다.
CompletedFilter = Literal["all", "deliverable", "excluded", "qc_rules"]


class CompletedPage(BaseModel):
    """완료 목록 한 페이지와 전체 수. 페이지에 실린 것만 세면 '없음'이 잘못 표시된다.

    아래 전체 집계 네 개는 `summary=false`로 부르면 채우지 않는다.
    화면은 그 값을 `GET …/completed/summary`(`CompletedSummary`)에서 한 번 받는다.
    그래야 `더 보기`와 검색어 입력마다 행 검증과 집계가 다시 실행되지 않는다.
    """

    #: 필터를 적용한 뒤의 전체 수. `filter`를 주면 그 필터에 해당하는 전체 수가 된다.
    total: int
    items: list[CompletedRow]

    #: 납품 검사(`check_row`)를 실제로 실행한 행 수. `filter=deliverable|excluded`일 때만 채운다.
    #: 나머지 필터는 SQL이 걸러 상한에서 끊기지 않으므로 `None`이다.
    #: 이 수가 `filter_candidates`보다 작으면 상한에서 끊겼다는 뜻이다.
    #: 그때 화면은 분모가 바뀌었다고 알린다.
    #: 검사는 행마다 pydantic 검증이라 물량에 독립이 되도록 상한을 둔다.
    #: 규칙을 SQL로 한 번 더 쓰면 같은 판정이 두 곳에 생겨 서로 달라진다.
    filter_scanned: int | None = None
    #: 필터를 적용하기 전의 후보 전체 수(검색 조건은 적용). `filter_scanned`의 분모다.
    filter_candidates: int | None = None

    # 전체 기준 납품 가능 건수. `None`은 세지 않았다는 뜻이다(0과 구분한다).
    # 행마다 pydantic 검증을 실행하므로, 물량이 크면 목록을 한 번 여는 비용이 물량에 비례해
    # 커진다. 도구는 물량에 독립이어야 하므로 상한을 둔다. 상한을 넘으면 세지 않는다.
    # 그때 화면은 이 페이지의 수를 분모와 함께 표시한다("이 페이지 50건 중 4건 제외").
    deliverable_total: int | None = None
    #: 캡션 품질검수가 보류한 건수(0074). 완료 목록은 이 건수를 빼고 센다.
    #: 이 수를 함께 보여 주지 않으면 운영자는 완료 수가 줄어든 이유를 알 수 없다.
    #: 0이면 화면에 표시하지 않는다.
    qc_held_total: int = 0
    # 화면이 세지 않은 이유를 알릴 수 있게 상한을 함께 준다.
    # 값도 이유도 주지 않으면 읽는 쪽이 `None`을 0으로 오해한다.
    deliverable_scan_limit: int = 0
    #: 승인분 중 모델 검사를 받지 못한 것(`rules`로 판정된 것)의 전체 수.
    #: 비가역 액션의 판단 근거라 납품 버튼 옆에 표시한다.
    #:
    #: `deliverable_total`과 달리 `None`이 없다.
    #: `deliverable_total`은 행마다 pydantic 검증이라 상한이 있다.
    #: 이 값은 열 두 개를 보는 `COUNT`라 물량과 관계없이 정확하다.
    qc_unreviewed_total: int = 0


class CompletedSummary(BaseModel):
    """완료 목록 상단의 전체 집계. 검색·페이지와 관계없이 한 번 센다.

    값의 뜻은 `CompletedPage`의 같은 이름 필드와 같다.
    이 수는 검색어에 따라 바뀌지 않아야 하므로 목록 페이지와 따로 부른다.
    검색 결과 안에서 세면 납품할 수 있는 건수가 입력한 글자마다 달라진다.
    """

    #: 완료(승인, 품질검수 보류 제외)의 전체 수. 검색 전 분모다.
    total: int
    deliverable_total: int | None = None
    #: 납품 가능 건의 클립 길이 합(초). `deliverable_total`과 같은 검사에서 센다.
    #: 상한을 넘어 세지 않았으면 `None`이다. 클립이 없는 작업은 길이에 더하지 않는다.
    deliverable_seconds: float | None = None
    deliverable_scan_limit: int = 0
    qc_held_total: int = 0
    qc_unreviewed_total: int = 0
    #: 완료 작업의 마지막 승인 판정 시각. 승인 판정 기록이 하나도 없으면 `None`이다.
    last_approved_at: datetime | None = None


class QcFinding(BaseModel):
    """품질검수 지적 하나. 저장된 값(`caption_qc_runs.findings`)에 읽을 때 두 값을 붙인다.

    품질검수는 여러 단계로 이뤄진다(캡션·개인정보·비속어·길이·비식별화·납품 규격 ·
    맞춤법·문체·부적절·용어집).
    메시지만 보여 주면 어느 단계에서 걸렸는지 화면이 알려 주지 못한다.
    그러면 사용자가 문장을 읽고 짐작해야 한다. `check_label`·`source`가 그 정보를 채운다.

    두 값은 저장하지 않는다. 저장하면 옛 행이 옛 이름을 가진 채 남는다.
    그러면 이름을 고쳐도 이력의 일부만 바뀐다.
    이름 사전은 `caption_qc.rules` 한 곳에만 두고 읽을 때 붙인다.
    """

    check: str
    severity: str
    message: str
    #: 문장 지적일 때만 있다. 없는 것을 빈 문자열로 채우지 않는다.
    sentence_id: str | None = None
    #: 화면에 표시할 단계 이름(`개인정보`·`문체` 등). 사전에 없으면 `check` 값을 그대로 쓴다.
    check_label: str = ""
    #: `rules` | `model`. 지적 하나의 출처이고, 실행 단위의 출처(`adapter_key`)와 다를 수 있다.
    source: str = "rules"


class QcHeldRow(BaseModel):
    """보류된 완료 건 하나. 보류 근거를 함께 싣는다.

    목록만 주면 운영자가 보류 이유를 확인하려고 다시 조회해야 한다.
    """

    work_item_id: UUID
    video_id: UUID
    clip_id: UUID | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    video_title: str | None = None
    caption_brief: str = ""
    verdict: str | None = None
    findings: list[QcFinding] = Field(default_factory=list)
    #: 판정한 주체. 규칙만 실행했으면 `rules`, 모델이 실행됐으면 그 어댑터·모델 이름이다.
    adapter_key: str | None = None
    model: str | None = None
    checked_at: datetime | None = None
    #: 표시한 판정(`checked_at`)보다 뒤에 모델 실패로 끝난 마지막 다시 검사의 시각.
    #: 실패한 다시 검사는 실행 기록을 남기지 않는다.
    #: 그래서 이 값이 있으면 판정·지적은 그 전 실행의 결과다.
    #: 다시 검사가 실패한 적이 없거나 그 뒤에 새 판정이 있으면 `None`이다.
    recheck_failed_at: datetime | None = None
    #: 보류를 건 실행의 단계별 결과. 무엇을 검사했는지 보류 상세에도 보여 준다.
    #: `None`이면 단계 기록(0077)을 남기기 전의 실행이거나 실행 기록이 없는 경우다.
    stages: list["QcStage"] | None = None
    #: 지금 승인본의 캡션 문장(삭제 표시한 문장은 뺀다). `캡션 고쳐 다시 승인` 창이 고친다.
    #: id는 지적의 `sentence_id`와 같은 축이라 어느 문장에 지적이 걸렸는지 창에서 짝지을 수 있다.
    sentences: list["QcCaptionSentence"] = Field(default_factory=list)


class QcCaptionSentence(BaseModel):
    """보류 행에 싣는 캡션 문장 하나. 지금 승인본의 값 그대로다."""

    #: 문장 id. 지적의 `sentence_id`와 같다.
    id: str
    text: str


class QcSentenceEdit(BaseModel):
    """`캡션 고쳐 다시 승인` 요청의 고친 문장 하나."""

    #: 고칠 문장의 id. 승인본에 없는 id는 거절한다.
    id: str = Field(min_length=1, max_length=100)
    #: 고친 본문. 앞뒤 공백을 떼고, 남는 것이 없으면 거절한다.
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """앞뒤 공백을 떼고, 남는 것이 없으면 거절한다."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("문장을 입력해 주세요")
        return stripped


class QcHoldReviseRequest(BaseModel):
    """관리자가 보류된 승인본의 캡션을 고쳐 다시 승인하는 요청.

    고칠 문장만 보낸다. 서버가 지금 승인본의 같은 id 문장 본문을 바꾸고, 구간·삭제 표시 같은
    나머지 값은 그대로 둔다. 화면이 승인본 전체를 다시 보내면 그 사이 바뀐 다른 라벨을 덮는다.
    """

    sentences: list[QcSentenceEdit] = Field(min_length=1, max_length=200)


class QcStage(BaseModel):
    """검사 단계 하나의 결과. 실행했는지, 걸렸는지, 건너뛰었는지 구분한다.

    체크리스트가 정확하려면 이 셋을 나눠야 한다.
    `findings`만으로는 "통과"와 "건너뜀"이 둘 다 지적 0건이라 구분되지 않는다
    (없는 것과 빈 것은 다르다).
    """

    key: str
    #: 화면에 표시할 단계 이름. 이름 사전은 `caption_qc.rules` 한 곳에만 있다.
    label: str = ""
    #: `passed` | `flagged` | `noted` | `skipped`
    #:
    #: `noted`는 걸렸지만 판정에 영향을 주지 않는 경우다.
    #: 지적이 모두 `info`(선택 항목이 비어 있음)일 때 쓴다.
    #: `flagged`로 합치면 화면이 경고와 같은 `!`를 표시한다.
    status: str
    #: `skipped`일 때만 있는 건너뛴 이유. 없으면 통과와 구분되지 않는다.
    reason: str | None = None
    #: `rules` | `model`
    source: str = "rules"


class QcRunDTO(BaseModel):
    """작업 한 건의 최신 품질검수 실행. 검수자 화면이 판단 근거를 그릴 때 쓴다.

    목록 DTO(`WorkItemDTO`)에는 싣지 않는다.
    큐 한 페이지가 수십 건인데 건마다 지적 배열을 실으면 목록이 무거워진다.
    워크벤치가 열린 작업 하나만 따로 조회한다.
    """

    trigger: str
    verdict: str
    findings: list[QcFinding] = Field(default_factory=list)
    adapter_key: str
    #: 판정한 모델 이름. 규칙만 실행했으면 `None`이고, 부르지 않은 모델을 빈 문자열로 적지 않는다.
    model: str | None = None
    checked_at: datetime
    #: 이 실행이 실행한 단계(0077).
    #: `None`은 이 컬럼을 추가하기 전의 실행이라 기록이 없다는 뜻이다.
    #: 빈 목록(단계를 하나도 실행하지 않음)과 다르므로 화면은 둘을 구분해 표시한다.
    stages: list[QcStage] | None = None


class WorkQcDTO(BaseModel):
    """작업 한 건의 품질검수 상태와 최신 실행. 검수자 화면이 판단 근거를 그릴 때 쓴다.

    제출과 승인은 검사를 기다리지 않고 커밋되고, 검사는 워커가 커밋 뒤에 한다.
    그래서 실행 기록이 없는 경우가 둘로 나뉜다. 지금 검사하는 중인 경우와 검사 대상이 아닌 경우다.
    화면이 둘을 다시 계산하지 않도록 서버가 `state`에 담아 준다.
    """

    #: `running`은 지금 본(마지막 제출이나 승인)의 검사가 아직 끝나지 않은 경우다.
    #: `failed`는 지금 본의 검사가 실행 기록을 남기지 못한 채 재시도 상한에 이른 경우다.
    #: `recorded`는 `run`이 지금 본의 최신 결과인 경우다.
    #: `none`은 실행 기록이 없는 경우다. 품질검수를 켜지 않았거나 검사 기능이 생기기 전의 작업이다.
    state: Literal["running", "failed", "recorded", "none"]
    #: 최신 실행. `running`·`failed`이면 이전 본을 검사한 기록이라 싣지 않는다.
    run: QcRunDTO | None = None


class QcHeldPage(BaseModel):
    """보류함 한 페이지. `total`은 이 페이지의 수가 아니라 전체 수다."""

    total: int
    items: list[QcHeldRow]


class QcHoldReleaseRequest(BaseModel):
    """관리자의 품질검수 보류 해제 요청. 사유가 필수다.

    보류 해제는 납품 검사에 예외를 만드는 일이다.
    시스템이 건 보류를 사람이 넘기는 일이다. 이유가 없으면 나중에 그 건이 납품에
    들어간 이유를 설명할 수 없다. 사유는 당시 지적 목록과 함께 감사 기록에 남는다.

    공백만 입력한 사유는 입력하지 않은 것으로 본다.
    `min_length`만 걸면 공백 한 글자가 통과해 사유 필수 규칙이 형식만 남는다.
    그러면 화면이 막아도 API로는 빈 사유를 보낼 수 있다.
    """

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """앞뒤 공백을 떼고, 남는 것이 없으면 거절한다."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("사유를 입력해 주세요")
        return stripped


class QcHoldActionResult(BaseModel):
    """보류 해제나 다시 검사의 결과. 액션 뒤의 현재 상태를 그대로 담는다.

    화면이 결과를 짐작하지 않게 한다. `다시 검사`는 통과할 수도, 계속 보류될 수도 있다.
    화면이 미리 "풀렸습니다"라고 쓰면 틀린 안내가 되는 경우가 생긴다.
    """

    work_item_id: UUID
    #: `pending` | `passed` | `held`. 이 액션 뒤의 값이다.
    qc_status: str
    #: `다시 검사`일 때의 실행 판정. 보류 해제는 검사를 다시 실행하지 않으므로 `None`이다.
    verdict: str | None = None
    #: 그 실행에서 걸린 지적 수. 보류 해제일 때는 해제 시점의 지적 수다.
    findings: int = 0
    #: 다시 검사에서 AI 검사를 부르지 못해 기록 없이 보류를 그대로 둔 경우 참.
    #: 화면은 이 값으로 "AI 검사를 실행하지 못했습니다"를 알린다. 규칙만으로 풀지 않는다.
    model_failed: bool = False


class CompletedRevision(BaseModel):
    """제출 이력 한 줄. 몇 번째 제출을 누가 언제, 얼마나 고쳐 냈는지 담는다."""

    label_id: UUID
    revision: int
    submitted_by: UUID
    #: 제출한 사람의 이름. 서버가 싣는다. 계정이 없으면 None이다.
    #: UUID만 주면 화면이 제출자를 표시할 수 없고, 납품 검토자가 감사 로그를 찾아봐야 한다.
    submitted_by_name: str | None = None
    submitted_at: datetime
    diff_chars_count: int = 0
    #: 라벨러가 받은 AI 초안과, 초안 대비 편집 거리·초안 글자 수.
    #: 작업의 첫 제출에만 있다. 개정 체인의 첫 단계다.
    #: 비어 있으면 재지 않았다는 뜻이다(0과 구분한다).
    ai_draft: str | None = None
    edit_distance: int | None = None
    draft_chars: int | None = None


class CompletedReview(BaseModel):
    """검수 판정 한 줄. 반려 사유 칩은 `reasons`, 자유 입력 문장은 `comment`에 담는다."""

    verdict: str
    comment: str | None = None
    #: 반려 사유의 코드와 현재 목록상의 이름. 꺼진 사유도 이름과 함께 온다.
    reasons: list[RejectReasonRef] = []
    reviewer_id: UUID
    #: 판정한 사람의 이름. `submitted_by_name`과 같은 이유로 서버가 싣는다.
    reviewer_name: str | None = None
    reviewed_at: datetime


class CompletedDetail(BaseModel):
    """완료된 클립 하나의 전체 정보. 읽기 전용 확인 화면이 쓴다.

    한 번에 모두 준다. 요약·이력·판정을 세 번 나눠 받으면 서로 다른 시점의 값이 섞인다.

    납품 가능 여부는 `row`에 있다.
    목록과 상세가 같은 값을 같은 필드에서 읽어야 둘이 달라지지 않는다.
    """

    row: CompletedRow

    # 원본 기준 정보. 납품에 미디어를 싣지 않으므로 이 세 값이 원본의 위치를 알려 준다.
    source_start_offset: float = 0.0
    source_checksum: str | None = None
    source_duration: float | None = None

    caption: ExportCaption = ExportCaption()
    #: 이 클립이 영상에서 물려받는 메타 네 가지. 납품 메타에 실리는 값과 같다.
    #: 라벨러가 클립마다 넣던 `meta`는 납품이 읽지 않으므로 싣지 않는다.
    video_meta: VideoMetaDTO = VideoMetaDTO()

    revisions: list[CompletedRevision] = []
    reviews: list[CompletedReview] = []

    # 서버가 채우지 않는 필드라 늘 기본값(`False`, 빈 목록)이다.
    # 납품 가능 여부와 사유는 `row.deliverable`·`row.blockers`를 읽는다.
    deliverable: bool = False
    blockers: list[str] = []
