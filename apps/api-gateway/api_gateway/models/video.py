"""영상 메타데이터와 준비 단계 상태를 담는 `videos` 테이블."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from api_gateway.models.base import Base, TimestampMixin

# 영상 권리 정책(`copyright_policy`). `pre_cleared` 하나만 쓴다.
CopyrightPolicy = Literal["pre_cleared"]
#: 영상 권리 상태(`copyright_status`). `cleared` 하나만 쓴다.
#: 두 컬럼은 RebornStudio Pro와 같은 스키마를 유지하려고 남긴다.
CopyrightStatus = Literal["cleared"]
#: 새 영상의 권리 상태. ORM·DB 컬럼 기본값도 이 값이다.
INITIAL_COPYRIGHT_STATUS: CopyrightStatus = "cleared"
# 업로드 상태. 변환 실패는 `transcode_status='failed'`로 나타낸다.
# 한 컬럼에 업로드와 변환 상태를 합치면 변환 실패가 "업로드가 실패했다"로 읽힌다.
VideoUploadStatus = Literal["pending", "uploaded", "failed"]
# MI 요청 전 ffprobe 규격 게이트의 상태.
# `pending`은 미검증, `passed`는 규격 충족, `rejected`는 규격 위반(용량·길이·코덱), `error`는 probe 실패다.
# `error`는 운영자에게 알리는 표시다. MI가 처리할 수 있는 파일일 수 있어서 분석 요청을 막지 않는다.
VideoProbeStatus = Literal["pending", "passed", "rejected", "error"]
# 트랜스코딩 파이프라인 상태. `skipped`는 파이프라인 밖(기본), `pending`은 대기,
# `queued`는 task 투입, `running`은 워커가 리스를 보유한 상태다. 끝나면 `done` 또는 `failed`다.
# `skipped` → `pending` 전이는 probe 통과 뒤 `media/pipeline.open_next`가 변환 판정(`plan_outputs`)에
# 따라 올린다.
VideoTranscodeStatus = Literal["skipped", "pending", "queued", "running", "done", "failed"]
# 트랜스코딩 프로필. `auto`는 판정에 따름(기본), `always`는 항상 재인코딩, `never`는 원본 그대로 분석.
VideoTranscodeProfile = Literal["auto", "always", "never"]


class Video(Base, TimestampMixin):
    """영상 한 편.

    메타데이터, 준비 단계(probe·변환·비식별화·publish) 상태를 담는다.
    """

    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint(
            "copyright_policy IN ('pre_cleared')",
            name="ck_video_copyright_policy",
        ),
        CheckConstraint(
            "copyright_status IN ('cleared')",
            name="ck_video_copyright_status",
        ),
        CheckConstraint(
            "upload_status IN ('pending','uploaded','failed')",
            name="ck_video_upload_status",
        ),
        CheckConstraint(
            "probe_status IN ('pending','passed','rejected','error')",
            name="ck_video_probe_status",
        ),
        CheckConstraint(
            "transcode_status IN ('skipped','pending','queued','running','done','failed')",
            name="ck_video_transcode_status",
        ),
        CheckConstraint(
            "publish_status IN ('skipped','pending','queued','running','done','failed')",
            name="ck_video_publish_status",
        ),
        CheckConstraint(
            "deident_status IN ('skipped','pending','queued','running','done','failed')",
            name="ck_video_deident_status",
        ),
        CheckConstraint(
            "transcode_profile IN ('auto','always','never')",
            name="ck_video_transcode_profile",
        ),
    )

    video_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    copyright_policy: Mapped[CopyrightPolicy] = mapped_column(String(30), nullable=False)
    copyright_status: Mapped[CopyrightStatus] = mapped_column(
        String(20), nullable=False,
        default=INITIAL_COPYRIGHT_STATUS, server_default=INITIAL_COPYRIGHT_STATUS,
    )
    upload_status: Mapped[VideoUploadStatus] = mapped_column(String(20), nullable=False, default="pending")
    # 큐 조회용 probe 상태. 상세 결과(추출 메타·위반 사유)는 extra["probe"]에 둔다.
    probe_status: Mapped[VideoProbeStatus] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    # 트랜스코딩 파이프라인 상태.
    transcode_status: Mapped[VideoTranscodeStatus] = mapped_column(
        String(20), nullable=False, default="skipped", server_default="skipped"
    )
    transcode_profile: Mapped[VideoTranscodeProfile] = mapped_column(
        String(10), nullable=False, default="auto", server_default="auto"
    )
    # 원본 파일이 아직 스토리지에 있으면 True.
    # 원본을 지워도 `source_uri`는 남긴다. `source_uri`는 출처 기록이자 감사의 근거다.
    # 그래서 원본이 있던 위치는 URI가, 원본이 지금 남아 있는지는 이 값이 나타낸다.
    source_retained: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))

    # 분석 입력 사본의 URI. source_uri는 출처 기록이자 감사의 근거라 덮어쓰지 않는다.
    # MAIU 등록 입력은 media/resolver.media_uri(analysis_input) 한 곳에서 고른다.
    analysis_uri: Mapped[str | None] = mapped_column(Text)
    # 리스와 펜싱. 완료 보고는 토큰이 현재 시도와 같을 때만 반영한다.
    # 만료된 리스는 beat가 회수한다.
    transcode_token: Mapped[str | None] = mapped_column(Text)
    transcode_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 인코딩한 미디어 시간(초). 하트비트가 갱신한다.
    # `duration_seconds`로 나누면 진행률이 된다. 벽시계 경과 시간과 달리 하드웨어가 바뀌어도
    # (GPU 전환 포함) 같은 기준이다.
    transcode_done_seconds: Mapped[float | None] = mapped_column(Float)
    transcode_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # 비식별화 상태. GPU 모델이 처리한다.
    # 프로젝트 스위치(`Project.deident_enabled`)가 꺼져 있으면 skipped다.
    # 켜져 있는데 처리하지 못하면 failed로 파이프라인을 멈춘다.
    # 건너뛰면 원본(raw)의 얼굴이 클라우드 분석기로 전송된다.
    deident_status: Mapped[VideoTranscodeStatus] = mapped_column(
        String(20), nullable=False, default="skipped", server_default="skipped"
    )
    deident_token: Mapped[str | None] = mapped_column(Text)
    deident_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deident_done_seconds: Mapped[float | None] = mapped_column(Float)
    deident_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # publish 단계 상태. 재생본 생성, 최종 버킷 반영, 스테이징 정리를 한다.
    # 변환과 publish 사이에 비식별화가 들어가므로 변환 단계와 나눈다.
    # 비식별화가 꺼져 있어도 재생본 생성과 최종화는 해야 하므로 비식별화 단계에도 넣지 않는다.
    # 리스·펜싱·시도 상한을 똑같이 쓰므로 필드 구성이 transcode·deident와 같다.
    publish_status: Mapped[VideoTranscodeStatus] = mapped_column(
        String(20), nullable=False, default="skipped", server_default="skipped"
    )
    publish_token: Mapped[str | None] = mapped_column(Text)
    publish_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_done_seconds: Mapped[float | None] = mapped_column(Float)
    publish_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # 한 번에 반입(업로드/NCP 등록)한 묶음의 id. 다음 단계에서 방금 올린 영상만 걸러 볼 때 쓴다.
    upload_batch_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # 영상 단위 메타 4개(방송사·프로그램명·장르·방영일). 운영자가 영상에서 한 번 입력하면
    # 그 영상의 클립이 물려받는다. 납품·품질검수·라벨링 화면은 모두 이 값을
    # 읽는다(`services/video_meta`). 라벨러 입력(`payload.meta`)은 읽지 않는다.
    broadcaster: Mapped[str | None] = mapped_column(String(100))
    program_name: Mapped[str | None] = mapped_column(String(200))
    episode: Mapped[str | None] = mapped_column(String(50))
    aired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 장르. 영상 단위 메타 네 값을 한곳에 두려고 영상 컬럼으로 둔다.
    genre: Mapped[str | None] = mapped_column(String(100))

    # 권리 정보. 저작권자를 읽거나 쓰지 않는다.
    # RebornStudio Pro와 같은 스키마를 유지하려고 컬럼을 남긴다.
    copyright_holder: Mapped[str | None] = mapped_column(Text)
    clearance_document_url: Mapped[str | None] = mapped_column(Text)
    # pre_cleared(검수 생략)로 등록한 근거.
    # 검수를 모두 건너뛰므로 사전 허가의 이유를 기록해 감사에 쓴다.
    precleared_reason: Mapped[str | None] = mapped_column(Text)

    # SQLAlchemy 예약어를 피하려고 속성 이름을 extra로 둔다. DB 컬럼 이름은 metadata다.
    extra: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    # 원본 sha256(`sha256:<hex>`). 납품에는 미디어를 넣지 않으므로, 받는 쪽이 가진 파일이
    # 분석한 파일과 같은지 대조할 유일한 수단이다. 등록을 빠르게 하려고 beat가 나중에 채운다.
    source_sha256: Mapped[str | None] = mapped_column(Text)
    # soft delete 시각.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
