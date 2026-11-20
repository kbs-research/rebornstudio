"""라벨링 워크벤치의 개발 시드. 가짜 JSONB 데이터로 로컬 DB를 채운다.

저장소는 `--storage-connection <이름>`으로 고른 NCP 저장소 연결(자격증명 연결에 등록한 것)을 쓴다.
그 연결과 연결 설정의 버킷을 시드 프로젝트의 저장소 대상으로 기록하고 시드 영상에 고정 기록한다.
연결을 주지 않으면 저장소를 건드리지 않는다. 샘플 영상 업로드와 MI 결과 적재를 건너뛰고,
프로젝트는 분석 연동에서 저장소를 고르기 전까지 영상 등록이 막힌다.

장면 영상은 저장소에 이미 있는 실제 장면 클립을 가리킨다. 클립의 버킷·객체 키와
MI 결과 아티팩트 키는 환경변수나 저장소 루트 `.env`에서 읽는다(`SEED_SCENE_BUCKET`·
`SEED_SCENE_CLIPS`·`SEED_MAIU_ARTIFACT_KEY`). 버킷이나 클립 목록이 비면 장면 영상을
만들지 않고 나머지 메타·작업 시드만 넣는다.

    make seed-dev STORAGE_CONNECTION=ncp-dev
"""
# ruff: noqa: I001  — env vars를 설정한 뒤 import해야 하므로 isort 순서 제어 불가
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

# DB URL의 기본값은 로컬 postgres다.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio",
)
os.environ.setdefault("ENVIRONMENT", "local")

# 서버 모듈을 import하기 전에 로컬 DB와 개발 환경인지 확인한다(`_local_only.py`).
from _local_only import refuse_unless_local  # noqa: E402

refuse_unless_local()

# noqa: E402 — env must be set before these imports
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from dotenv import dotenv_values  # noqa: E402

from api_gateway.config import _env_file  # noqa: E402
from api_gateway.database import SessionLocal  # noqa: E402
from api_gateway.models import AnalysisJob, Connection  # noqa: E402
from api_gateway.models.clip import Clip  # noqa: E402
from api_gateway.models.label import Label, Review  # noqa: E402
from api_gateway.models.org import Organization, Project, ProjectMember  # noqa: E402
from api_gateway.models.user import User  # noqa: E402
from api_gateway.models.video import Video  # noqa: E402
from api_gateway.models.work import WorkItem  # noqa: E402
from api_gateway.security import hash_password  # noqa: E402
from api_gateway.services import platform_credentials as pc  # noqa: E402
from api_gateway.services.analysis import job_state  # noqa: E402
from api_gateway.services.analysis.registry import MAIU_KEY  # noqa: E402
from api_gateway.services.maiu import reconciler as maiu_reconciler  # noqa: E402
from api_gateway.services.maiu.naver_client import _to_summary  # noqa: E402

# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────

ADMIN_EMAIL = "admin@example.com"


PROJECT_SLUG = "dev-seed"
PROJECT_NAME = "Dev Seed (라벨링 미리보기)"

VIDEO_TITLE = "9시 뉴스 샘플"

# 메인 데모 영상(9시 뉴스 샘플)의 객체 키. 버킷은 고른 저장소 연결의 버킷이다.
# 색상바 MP4이고 `ensure_seed_sample_video`가 만들어 올린다.
VIDEO_SOURCE_KEY = "seed/news9-demo.mp4"
#: 저장소 연결을 고르지 않았을 때 영상 행에 남기는 버킷 이름. 이 버킷은 읽지 않는다.
_NO_STORAGE_BUCKET = "no-storage-connection"

# ──────────────────────────────────────────────────────────────
# 시드 데이터: 한국어 뉴스 장면 2개
# ──────────────────────────────────────────────────────────────

CLIP_1_AUTO_CAPTION = {
    "brief": "안녕하세요, 9시 뉴스입니다. 오늘 주요 소식 전해드립니다.",
    "summary": (
        "앵커가 스튜디오에서 9시 뉴스 오프닝 인사를 전하며"
        " 오늘의 주요 뉴스 헤드라인을 소개한다."
    ),
    "speeches": [
        {
            "id": "s1-1",
            "startMs": 0,
            "endMs": 2400,
            "text": "안녕하세요, 9시 뉴스입니다.",
            "pid": "anchor",
            "name": "앵커",
        },
        {
            "id": "s1-2",
            "startMs": 2600,
            "endMs": 5900,
            "text": "오늘 주요 소식 전해드립니다.",
            "pid": "anchor",
            "name": "앵커",
        },
    ],
}

CLIP_1_SPEAKERS = [{"pid": "anchor", "name": "앵커"}]

CLIP_1_EXTRA = {
    "scene": 1,
    "scene_thumbnail_url": "https://picsum.photos/seed/news1/320/180",
    "keywords": [
        {"text": "뉴스", "group": "방송", "category": "행동", "score": 90},
        {"text": "정치", "group": "시사", "category": "주제", "score": 75},
        {"text": "앵커", "group": "인물", "category": "직업", "score": 88},
        {"text": "스튜디오", "group": "장소", "category": "공간", "score": 60},
    ],
    "people": [],
    "shots": [
        {"shot_id": "sh-1-1", "startMs": 0, "endMs": 2400, "type": "close_up"},
        {"shot_id": "sh-1-2", "startMs": 2400, "endMs": 6000, "type": "medium"},
    ],
}

CLIP_2_AUTO_CAPTION = {
    "brief": "현장 취재 기자입니다. 오늘 오후 서울 시청 앞에서 대규모 집회가 열렸습니다.",
    "summary": (
        "기자가 서울 시청 앞 현장에서 시민 집회 상황을 생중계로 전달하며"
        " 집회 참가 인원과 주요 요구 사항을 설명한다."
    ),
    "speeches": [
        {
            "id": "s2-1",
            "startMs": 6000,
            "endMs": 7800,
            "text": "현장 취재 기자입니다.",
            "pid": "reporter",
            "name": "기자",
        },
        {
            "id": "s2-2",
            "startMs": 8000,
            "endMs": 11800,
            "text": "오늘 오후 서울 시청 앞에서 대규모 집회가 열렸습니다.",
            "pid": "reporter",
            "name": "기자",
        },
    ],
}

CLIP_2_SPEAKERS = [{"pid": "reporter", "name": "기자"}]

CLIP_2_EXTRA = {
    "scene": 2,
    "scene_thumbnail_url": "https://picsum.photos/seed/news2/320/180",
    "keywords": [
        {"text": "집회", "group": "사회", "category": "사건", "score": 95},
        {"text": "서울", "group": "장소", "category": "지역", "score": 82},
        {"text": "시청", "group": "장소", "category": "공간", "score": 78},
        {"text": "시민", "group": "인물", "category": "집단", "score": 70},
    ],
    "people": [],
    "shots": [
        {"shot_id": "sh-2-1", "startMs": 6000, "endMs": 8500, "type": "wide"},
        {"shot_id": "sh-2-2", "startMs": 8500, "endMs": 12000, "type": "medium"},
    ],
}


# ──────────────────────────────────────────────────────────────
# Find-or-create 헬퍼
# ──────────────────────────────────────────────────────────────


async def _get_super_admin(db: AsyncSession) -> tuple[str, str]:
    """super_admin의 user_id와 email을 돌려준다.

    Raises:
        RuntimeError: super_admin 계정이 없는 경우.
    """
    row = (await db.execute(
        text("SELECT user_id::text, email FROM users WHERE global_role='super_admin' LIMIT 1")
    )).first()
    if not row:
        raise RuntimeError(
            "super_admin 계정이 없습니다. DB 초기 시드(alembic seed)를 먼저 실행하세요."
        )
    return row[0], row[1]


async def _default_org(db: AsyncSession) -> Organization:
    """기본 조직을 읽는다. 마이그레이션이 한 행만 만들고 두 번째 행은 유일 인덱스가 막는다."""
    org = await db.scalar(select(Organization).limit(1))
    if org is None:
        raise SystemExit("기본 조직이 없습니다. alembic upgrade head를 먼저 실행하세요.")
    return org


async def _find_or_create_project(db: AsyncSession, org: Organization) -> Project:
    """(org_id, slug)='dev-seed' 프로젝트를 find-or-create한다."""
    existing = await db.scalar(
        select(Project).where(Project.org_id == org.org_id, Project.slug == PROJECT_SLUG)
    )
    if existing:
        return existing
    project = Project(
        org_id=org.org_id,
        slug=PROJECT_SLUG,
        name=PROJECT_NAME,
        status="active",
        conversations_enabled=False,
        caption_min_tokens=50,
    )
    db.add(project)
    await db.flush()
    return project


async def _ensure_membership(db: AsyncSession, project: Project, admin_id: str) -> None:
    """super_admin을 project_admin 멤버로 등록한다. 다시 실행해도 안전하다."""
    from uuid import UUID
    uid = UUID(admin_id)
    existing = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.project_id,
            ProjectMember.user_id == uid,
        )
    )
    if existing:
        return
    member = ProjectMember(
        project_id=project.project_id,
        user_id=uid,
        role="project_admin",
        status="active",
    )
    db.add(member)
    await db.flush()


async def _find_or_create_video(
    db: AsyncSession, project: Project, admin_id: str, store: pc.StorageCredentials | None,
) -> Video:
    """title=VIDEO_TITLE인 영상을 find-or-create한다. 저장소를 골랐으면 그 버킷을 원본으로 둔다."""
    from uuid import UUID
    existing = await db.scalar(
        select(Video).where(
            Video.project_id == project.project_id,
            Video.title == VIDEO_TITLE,
        )
    )
    if existing:
        return existing
    video = Video(
        project_id=project.project_id,
        uploaded_by=UUID(admin_id),
        title=VIDEO_TITLE,
        source_uri=f"s3://{store.bucket if store else _NO_STORAGE_BUCKET}/{VIDEO_SOURCE_KEY}",
        duration_seconds=12.0,
        copyright_policy="pre_cleared",
        copyright_status="cleared",
        upload_status="uploaded",
        broadcaster="샘플 방송",
        program_name="9시 뉴스 샘플",
    )
    db.add(video)
    await db.flush()
    return video


async def _find_or_create_clip(
    db: AsyncSession,
    video: Video,
    project: Project,
    *,
    start: float,
    end: float,
    auto_caption: dict,
    speakers: list,
    extra: dict,
) -> tuple[Clip, bool]:
    """start_seconds/end_seconds 기준으로 메인 데모 영상의 clip을 find-or-create한다.

    Returns:
        (clip, created). 새로 넣었으면 created가 True.
    """
    existing = await db.scalar(
        select(Clip).where(
            Clip.video_id == video.video_id,
            Clip.start_seconds == Decimal(str(start)),
        )
    )
    if existing:
        return existing, False
    clip = Clip(
        video_id=video.video_id,
        project_id=project.project_id,
        start_seconds=start,
        end_seconds=end,
        rights_status="auto_cleared",
        auto_caption=auto_caption,
        speakers=speakers,
        extra=extra,
    )
    db.add(clip)
    await db.flush()
    return clip, True


async def _ensure_work_item(db: AsyncSession, clip: Clip) -> WorkItem:
    """clip의 work_item을 `ensure_for_clip`과 같은 방식으로 find-or-create한다."""
    existing = await db.scalar(select(WorkItem).where(WorkItem.clip_id == clip.clip_id))
    if existing:
        return existing
    item = WorkItem(
        video_id=clip.video_id,
        clip_id=clip.clip_id,
        project_id=clip.project_id,
        status="available",
        payload={},
    )
    db.add(item)
    await db.flush()
    return item


# ──────────────────────────────────────────────────────────────
# 시드 샘플 영상 보장
# ──────────────────────────────────────────────────────────────


def ensure_seed_sample_video(store: pc.StorageCredentials | None, uri: str) -> None:
    """고른 저장소에 seed 샘플 MP4가 없으면 ffmpeg로 만들어 올린다. 다시 실행해도 안전하다.

    앱과 같은 클라이언트(`storage._client`)를 고른 연결의 값으로 쓴다.
    저장소를 고르지 않았거나 접근할 수 없거나 ffmpeg가 없으면 NOTE를 찍고 정상 반환한다.
    DB 시드는 그대로 이어진다.
    """
    from botocore.exceptions import ClientError, EndpointResolutionError

    from api_gateway.services.media import storage

    if store is None:
        print("  [스토리지] NOTE: 저장소 연결을 고르지 않아 샘플 영상 업로드를 건너뜀")
        return
    # source_uri 파싱: s3://bucket/key
    without_scheme = uri[len("s3://"):]
    bucket, key = without_scheme.split("/", 1)

    try:
        s3 = storage._client(store)

        # 1. 버킷 ensure
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as exc:
            status = int(exc.response["ResponseMetadata"]["HTTPStatusCode"])
            if status == 404:
                s3.create_bucket(Bucket=bucket)
                print(f"  [스토리지] 버킷 생성: {bucket}")
            else:
                raise

        # 2. 객체가 이미 있으면 건너뛴다.
        try:
            s3.head_object(Bucket=bucket, Key=key)
            print(f"  [스토리지] 샘플 영상 이미 존재 — 건너뜀 ({bucket}/{key})")
            return
        except ClientError as exc:
            if int(exc.response["ResponseMetadata"]["HTTPStatusCode"]) != 404:
                raise

        # 3. ffmpeg로 테스트 패턴 MP4 생성 후 업로드
        if shutil.which("ffmpeg") is None:
            print(
                "  [스토리지] NOTE: ffmpeg 미설치 — 샘플 MP4 업로드 건너뜀.\n"
                f"          영상 재생: 임의 MP4를 {bucket}/{key} 에 직접 올리세요."
            )
            return

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=12:size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-movflags", "+faststart",
                    tmp_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with open(tmp_path, "rb") as f:
                s3.put_object(Bucket=bucket, Key=key, Body=f, ContentType="video/mp4")
            print(f"  [스토리지] 샘플 MP4 업로드 완료 → {bucket}/{key}")
        finally:
            os.unlink(tmp_path)

    except (EndpointResolutionError, OSError, Exception) as exc:  # noqa: BLE001
        print(
            f"  [스토리지] NOTE: 연결 실패 — 샘플 영상 업로드 건너뜀"
            f" ({exc.__class__.__name__}: {exc})"
        )


# ──────────────────────────────────────────────────────────────
# 화면 확인용 데모 데이터셋: 작업자·제출/검수·반복 반려·MAIU
# 모두 find-or-create라 다시 실행해도 안전하다. 실제 모델로 넣으므로 실제 API 쿼리로 화면에 보인다.
# ──────────────────────────────────────────────────────────────

#: 데모 작업자 비밀번호. `SEED_DEMO_PASSWORD`가 없으면 무작위 값이고 출력하지 않는다.
#: 워커 화면 확인용 개발 전용 계정이고, 로그인은 `make dev-token EMAIL=<계정>`으로 한다.
DEMO_PASSWORD = os.getenv("SEED_DEMO_PASSWORD") or secrets.token_urlsafe(18)

# (email, display_name, role)
#
# 품질 등급은 넣지 않는다. 도구는 사람을 좋다·나쁘다로 나누지 않는다.
DEMO_WORKERS = [
    ("demo.labeler1@example.com", "김라벨", "annotator"),
    ("demo.labeler2@example.com", "이작업", "annotator"),
    ("demo.labeler3@example.com", "정신입", "annotator"),
    ("demo.reviewer1@example.com", "박검수", "reviewer"),
]

def _seed_env(name: str) -> str:
    """시드 설정값을 환경변수, 저장소 루트 `.env` 순서로 읽는다. 없으면 빈 문자열이다.

    config와 같은 `.env`(`REBORN_ENV_FILE` 규칙)를 읽는다. `make seed-dev`는 셸 환경만 넘기므로
    `os.environ`만 보면 `.env`에 적은 값이 시드에 닿지 않는다.
    """
    value = os.environ.get(name)
    if value is None:
        path = _env_file()
        if path is not None and path.is_file():
            value = dotenv_values(path).get(name)
    return (value or "").strip()


def _parse_scene_clips(raw: str) -> list[tuple[int, str]]:
    """`SEED_SCENE_CLIPS` 값을 (장면 번호, 객체 키) 목록으로 읽는다.

    값은 `장면:키`를 쉼표로 이은 문자열이다. 키 파일명은 `…_{시작ms}_{끝ms}_raw.mp4` 꼴이어야
    `_parse_clip_range`가 원본 기준 구간을 읽는다.

    Raises:
        SystemExit: 항목이 `장면:키` 꼴이 아니거나 장면 번호가 정수가 아닌 경우.
    """
    out: list[tuple[int, str]] = []
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        scene, sep, key = item.partition(":")
        if not sep or not key.strip() or not scene.strip().isdigit():
            raise SystemExit(f"SEED_SCENE_CLIPS 항목은 '장면:키' 꼴이어야 합니다: {item!r}")
        out.append((int(scene), key.strip()))
    return out


# 장면 영상이 가리키는 장면 클립의 버킷. 영상 행의 source_uri가 이 버킷을 가리킨다.
# 버킷 이름과 객체 키는 비밀이 아니지만 실제 자산이라 저장소에 적지 않는다.
# 이 버킷을 읽으려면 이 버킷을 설정에 적은 저장소 연결이 있어야 한다.
SCENE_BUCKET = _seed_env("SEED_SCENE_BUCKET")

# 장면 영상의 (장면 번호, 객체 키) 목록. 영상 1편은 clip 1개(MI 장면 1개)다.
SCENE_CLIPS = _parse_scene_clips(_seed_env("SEED_SCENE_CLIPS"))

# 장면 클립을 자른 원본의 MI 결과 아티팩트(JSON) 키. 고른 저장소 연결의 버킷에서 읽는다.
# MI REST API는 VPC 전용이라 저장소에 저장된 결과 아티팩트를 S3로 받아 reconciler 정식 경로
# (`_to_summary` → `_create_clips_from_summary` → `_attach_scene_summaries`)로 적재한다.
MAIU_ARTIFACT_KEY = _seed_env("SEED_MAIU_ARTIFACT_KEY")


def _scene_uri(key: str) -> str:
    """장면 클립의 객체 키를 s3://bucket/key URI로 바꾼다. `presign_get_for`가 서명한다."""
    return f"s3://{SCENE_BUCKET}/{key}"

DEMO_LABEL_DATA = {
    "caption": {
        "brief": "앵커가 스튜디오에서 오늘의 주요 뉴스를 전한다. 화면 하단에 자막이 표시된다.",
        "detailed": "",
    },
    "meta": {
        "air_date": "2024-04-12", "program": "9시 뉴스 샘플",
        "reporter": "김상철", "location": "서울",
    },
    "tags": {
        "scene_type": ["스튜디오"], "entities": ["국회", "예산안"],
        "events": [], "sentiment": "중립",
    },
}


async def _ensure_demo_user(db: AsyncSession, email: str, name: str) -> UUID:
    """데모 작업자 user를 email로 find-or-create한다. `DEMO_PASSWORD`로 로그인할 수 있다."""
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        if existing.password_hash is None:  # 비밀번호가 없는 기존 데모 계정에 비밀번호를 준다
            existing.password_hash = hash_password(DEMO_PASSWORD)
            existing.email_verified = True
        return existing.user_id
    # 개발용 계정이라 동의 시각을 시드가 채운다. 비우면 업무 API가 403 `privacy_consent_required`다.
    user = User(
        email=email,
        display_name=name,
        global_role="user",
        approval_status="active",
        email_verified=True,
        password_hash=hash_password(DEMO_PASSWORD),
        privacy_consented_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user.user_id


async def _ensure_demo_member(
    db: AsyncSession, project: Project, user_id: UUID, role: str
) -> None:
    """데모 작업자를 프로젝트 멤버로 등록한다. 다시 실행해도 안전하다."""
    existing = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if existing:
        return
    db.add(ProjectMember(
        project_id=project.project_id,
        user_id=user_id,
        role=role,
        status="active",
    ))
    await db.flush()


def _parse_clip_range(source_key: str) -> tuple[int, int]:
    """`…_{start}_{end}_raw.mp4` 꼴 객체 키에서 원본 기준 구간 (start_ms, end_ms)를 읽는다."""
    name = source_key.rsplit("/", 1)[-1].removesuffix("_raw.mp4")
    parts = name.split("_")  # 마지막 두 조각이 시작·끝 밀리초다
    return int(parts[-2]), int(parts[-1])


def _fetch_maiu_artifact(store: pc.StorageCredentials | None) -> dict | None:
    """고른 저장소의 완료된 MAIU 결과 아티팩트(JSON)를 받아 돌려준다. 실패하면 None이다.

    MAIU REST API는 VPC 전용이라 로컬에서 못 부르므로, NCP에 저장된 결과 아티팩트를 S3로 받는다.
    """
    from api_gateway.services.media import storage
    if store is None:
        print("  [MAIU] NOTE: 저장소 연결을 고르지 않아 결과 아티팩트 적재를 건너뜀")
        return None
    if not MAIU_ARTIFACT_KEY:
        print("  [MAIU] NOTE: SEED_MAIU_ARTIFACT_KEY가 비어 결과 아티팩트 적재를 건너뜀")
        return None
    try:
        obj = storage._client(store).get_object(
            Bucket=store.bucket, Key=MAIU_ARTIFACT_KEY
        )
        return json.loads(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001
        print(f"  [MAIU] NOTE: 결과 아티팩트 로드 실패 — 시드 건너뜀 ({exc.__class__.__name__})")
        return None


def _scene_description(scene_obj: dict) -> str | None:
    """장면 요약 content(JSON bg/fg/camera)를 자연어 description으로 바꾼다.

    입력은 scene_summary.scenes[].sceneSummary.content다.
    """
    raw = (scene_obj.get("sceneSummary") or {}).get("content")
    if not raw:
        return None
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
        return " ".join(t for t in (p.get("bg"), p.get("fg"), p.get("camera")) if t) or None
    except (ValueError, AttributeError):
        return str(raw) or None


async def _purge_video_clips(db: AsyncSession, video: Video) -> None:
    """영상의 기존 시드 clip을 정리한다. work_item도 함께 지우되 실제 라벨·리뷰가 있으면 남긴다."""
    for o in (await db.scalars(select(Clip).where(Clip.video_id == video.video_id))).all():
        wis = (await db.scalars(
            select(WorkItem).where(WorkItem.clip_id == o.clip_id)
        )).all()
        keep = False
        for wi in wis:
            # 사람 손이 닿은 것은 지우지 않는다.
            if await db.scalar(
                select(Label).where(Label.work_item_id == wi.work_item_id).limit(1)
            ) or await db.scalar(
                select(Review).where(Review.work_item_id == wi.work_item_id).limit(1)
            ):
                keep = True
                break
        if keep:
            continue
        for wi in wis:
            await db.delete(wi)
        await db.delete(o)
    await db.flush()


async def _ensure_scene_video(
    db: AsyncSession, project: Project, admin_id: str,
    *, title: str, source_key: str, duration: float,
) -> Video:
    """장면 영상을 source_uri(객체 키, 불변)로 find-or-update한다.

    duration은 clip 길이로 미리 채운다. reconciler 적재가 원본 전체 길이로 덮어쓰지 않게 한다.
    """
    source_uri = _scene_uri(source_key)
    meta = dict(
        title=title, duration_seconds=duration, copyright_policy="pre_cleared",
        broadcaster="샘플 방송", program_name="장면 데모", episode=None,
    )
    existing = await db.scalar(
        select(Video).where(
            Video.project_id == project.project_id, Video.source_uri == source_uri
        )
    )
    if existing:
        for k, v in meta.items():
            setattr(existing, k, v)
        await db.flush()
        return existing
    video = Video(
        project_id=project.project_id, uploaded_by=UUID(admin_id),
        source_uri=source_uri, copyright_status="cleared", upload_status="uploaded", **meta,
    )
    db.add(video)
    await db.flush()
    return video


async def _ingest_scene_clip(
    db: AsyncSession, video: Video, project: Project, artifact: dict,
    *, scene: int, source_start_ms: int,
) -> bool:
    """아티팩트의 한 scene을 reconciler 정식 경로로 적재한다. shot 시각은 clip 기준으로 옮긴다.

    영상이 원본 전체가 아닌 단일 scene clip mp4이므로 shot 시각을 clip 기준(0~)으로 옮긴다.
    shot의 sceneThumbnailUrl은 만료된 presigned URL이라 지운다(깨진 이미지를 막는다).
    video.extra의 'maiu_seed_ingested' 표시가 있으면 다시 적재하지 않는다.
    그래야 clip_id가 바뀌지 않는다.

    Returns:
        이번에 새로 적재했으면 True.
    """
    if (video.extra or {}).get("maiu_seed_ingested") == scene:
        return False  # 이미 적재됐다

    idx = artifact.get("maiu_analysis", {}).get("indexDetails", {})
    shots: list[dict] = []
    for seg in idx.get("segments", []):
        if seg.get("scene") != scene:
            continue
        for sh in seg.get("shots", []):
            sh = dict(sh)
            sh["startMs"] = max(0, (sh.get("startMs") or 0) - source_start_ms)
            sh["endMs"] = max(0, (sh.get("endMs") or 0) - source_start_ms)
            # start/end(프레임 번호)는 원본 절대값을 둔다.
            # 메타데이터일 뿐 재생 시각에는 쓰지 않는다.
            sh["sceneThumbnailUrl"] = None  # 만료 presigned URL 제거
            shots.append(sh)
    if not shots:
        return False

    summary = _to_summary({"result": {
        "indexId": idx.get("indexId") or artifact.get("processing_info", {}).get("maiu_video_id"),
        "analysisSetting": (artifact.get("maiu_analysis") or {}).get("analysisSetting") or {},
        "indexDetails": {
            "segments": shots, "durationMs": idx.get("durationMs"),
            "fps": idx.get("fps"), "frameCount": idx.get("frameCount"),
            "videoUrl": idx.get("videoUrl"),
        },
    }})

    await _purge_video_clips(db, video)  # 손으로 만든 clip을 지우고 canonical로 적재한다
    # job은 세션에 넣지 않는다(db.add 하지 않음). reconciler 함수는 영상·프로젝트 id와
    # 에셋 번호만 읽는다. db.add 하면 6단계의 영구 분석 행(같은 영상·어댑터)과 UNIQUE가 충돌한다.
    job = AnalysisJob(
        video_id=video.video_id, project_id=project.project_id, clip_id=None,
        adapter_key=MAIU_KEY, payload={},
    )
    job_state.advance(job, "analyzed")
    await maiu_reconciler._create_clips_from_summary(db, job, summary)

    # 장면 요약(VLM)을 붙인다. `_attach_scene_summaries`는 clip.extra['scene']로 맞춘다.
    scenes = artifact.get("scene_summary", {}).get("scenes", [])
    sc = next((s for s in scenes if s.get("sceneId") == scene), None)
    desc = _scene_description(sc) if sc else None
    if desc:
        await maiu_reconciler._attach_scene_summaries(
            db, job, [{"scene": scene, "startMs": 0, "endMs": 0, "description": desc}]
        )

    video.extra = {**(video.extra or {}), "maiu_seed_ingested": scene}
    await db.flush()
    return True


async def _seed_demo(
    db: AsyncSession, project: Project, admin_id: str, video: Video,
    store: pc.StorageCredentials | None,
) -> dict:
    """화면 확인용 데모 데이터를 한꺼번에 시드하고 건수 dict를 돌려준다. 다시 실행해도 안전하다."""
    now = datetime.now(UTC)
    counts = {
        "workers": 0, "scene_videos": 0, "scene_clips": 0, "available": 0,
        "submitted": 0, "repeated": 0, "maiu": 0,
    }

    # 1. 데모 작업자 (workforce / review 라벨러 표시)
    worker_ids: dict[str, UUID] = {}
    for email, name, role in DEMO_WORKERS:
        uid = await _ensure_demo_user(db, email, name)
        await _ensure_demo_member(db, project, uid, role)
        worker_ids[email] = uid
        counts["workers"] += 1
    labeler1 = worker_ids["demo.labeler1@example.com"]
    reviewer1 = worker_ids["demo.reviewer1@example.com"]

    # 2. 장면 영상(저장소의 실제 장면 클립)과 MI 결과를 reconciler 정식 경로로 적재한다.
    # 버킷이나 클립 목록이 비면 이 단계를 건너뛴다. 아티팩트를 받지 못해도 멈추지 않는다.
    if SCENE_BUCKET and SCENE_CLIPS:
        artifact = _fetch_maiu_artifact(store)
    else:
        print("  [장면] NOTE: SEED_SCENE_BUCKET·SEED_SCENE_CLIPS가 비어 장면 영상 건너뜀")
        artifact = None
    scenes = SCENE_CLIPS if SCENE_BUCKET else []
    for n, (scene, src_key) in enumerate(scenes, start=1):
        title = f"장면 영상 {n} · 장면 {scene}"
        start_ms, end_ms = _parse_clip_range(src_key)
        rv = await _ensure_scene_video(
            db, project, admin_id,
            title=title, source_key=src_key, duration=(end_ms - start_ms) / 1000.0,
        )
        counts["scene_videos"] += 1
        if artifact and await _ingest_scene_clip(
            db, rv, project, artifact, scene=scene, source_start_ms=start_ms
        ):
            counts["scene_clips"] += 1

    # 3. 메인 영상에 제출/승인/반복 반려 상태 클립 + work_item + label/review
    # start_seconds는 기존 시드(20~42초)와 겹치지 않게 잡는다.
    # available 3건은 라벨링에서 가져갈 수 있는 clip(MAIU 요약 완료)이다.
    # 나머지는 검수·반복 반려 등의 상태다.
    states = [
        (74.0, 78.0, "available"), (80.0, 84.0, "available"), (86.0, 90.0, "available"),
        (44.0, 48.0, "claimed"), (50.0, 54.0, "submitted"), (56.0, 60.0, "submitted"),
        (62.0, 66.0, "approved"), (68.0, 72.0, "repeated"),
    ]
    for start, end, status in states:
        clip, _ = await _find_or_create_clip(
            db, video, project, start=start, end=end,
            auto_caption=CLIP_1_AUTO_CAPTION, speakers=CLIP_1_SPEAKERS, extra=CLIP_1_EXTRA,
        )
        wi = await db.scalar(select(WorkItem).where(WorkItem.clip_id == clip.clip_id))
        if wi is not None:
            continue  # 이미 시드했다
        wi = WorkItem(
            video_id=clip.video_id, clip_id=clip.clip_id, project_id=project.project_id,
            status="claimed" if status == "repeated" else status, payload={},
        )
        if status == "available":  # 다음 작업으로 받을 수 있다
            counts["available"] += 1
        elif status == "claimed":  # 라벨러가 진행 중이라 /work 큐에 보인다
            wi.claimed_by = labeler1
            wi.claimed_at = now - timedelta(minutes=10)
        elif status in ("submitted", "approved"):
            wi.claimed_by = labeler1
            wi.claimed_at = now - timedelta(minutes=20)
            wi.submitted_at = now - timedelta(minutes=5)
            counts["submitted"] += 1
        elif status == "repeated":  # 반려 3회 뒤 만든 사람이 재작업 중이라 반려 누적 목록에 보인다
            wi.claimed_by = labeler1
            wi.claimed_at = now - timedelta(hours=2)
            wi.rejection_count = 3
            counts["repeated"] += 1
        db.add(wi)
        await db.flush()
        if status in ("submitted", "approved"):
            lbl = Label(
                work_item_id=wi.work_item_id, submitted_by=labeler1,
                label_data=DEMO_LABEL_DATA, diff_chars_count=0,
                submitted_at=now - timedelta(minutes=5),
            )
            db.add(lbl)
            await db.flush()
            if status == "approved":
                db.add(Review(
                    work_item_id=wi.work_item_id, label_id=lbl.label_id, reviewer_id=reviewer1,
                    verdict="approve", comment=None, reviewed_at=now - timedelta(minutes=2),
                ))

    # 4. 진행 중인 작업. 메인 clip1(0.0초)을 admin이 가져간다(/work 큐 확인용).
    clip1 = await db.scalar(
        select(Clip).where(Clip.video_id == video.video_id, Clip.start_seconds == Decimal("0.0"))
    )
    if clip1 is not None:
        wi1 = await db.scalar(select(WorkItem).where(WorkItem.clip_id == clip1.clip_id))
        if wi1 is not None and wi1.status == "available":
            wi1.status = "claimed"
            wi1.claimed_by = UUID(admin_id)
            wi1.claimed_at = now

    # 6. 영상 단위 MI 분석 행(분석 진척 표시). 클립이 있는 영상은 MI가 끝난 영상이라 done을,
    # 나머지 영상에는 진행 단계를 돌려 가며 준다. 클립이 있는 영상에 진행 단계를 주면 화면 판정
    # (`media/video/classify.py`)이 워크벤치 영상을 '분석 중'으로 분류한다.
    # 진행 중인 행은 리컨실러가 처리하지 않도록 백오프 시각을 먼 미래로 둔다
    # (`reconciler.stages._lock_batch`). 기록한 분석 대상이 없어 처리하면 실패로 넘어간다.
    maiu_stages = ["analyzed", "analyzing", "registered", "pending"]
    vids = (await db.execute(
        select(Video).where(Video.project_id == project.project_id).order_by(Video.created_at)
    )).scalars().all()
    pending_i = 0
    for v in vids:
        existing = await db.scalar(select(AnalysisJob).where(
            AnalysisJob.video_id == v.video_id, AnalysisJob.clip_id.is_(None),
            AnalysisJob.adapter_key == MAIU_KEY,
        ))
        if existing is not None:
            continue
        job = AnalysisJob(
            video_id=v.video_id, project_id=project.project_id, clip_id=None,
            adapter_key=MAIU_KEY, payload={},
        )
        has_clips = await db.scalar(
            select(Clip.clip_id).where(Clip.video_id == v.video_id).limit(1)
        )
        if has_clips is not None:
            job_state.advance(job, "done")
        else:
            job_state.advance(job, maiu_stages[pending_i % len(maiu_stages)])
            job.payload = {"retry_after": "9999-12-31T00:00:00+00:00"}
            pending_i += 1
        db.add(job)
        counts["maiu"] += 1

    await db.flush()
    return counts


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────


async def _storage_of(db: AsyncSession, name: str | None) -> pc.StorageCredentials | None:
    """이름으로 고른 NCP 저장소 연결의 값을 돌려준다. 이름을 주지 않으면 `None`이다.

    Raises:
        SystemExit: 그 이름의 연결이 없거나 여럿이거나, 키·버킷을 읽지 못한 경우.
    """
    if not name:
        return None
    conns = list((await db.scalars(select(Connection).where(
        Connection.name == name, Connection.provider == pc.STORAGE_PROVIDER))).all())
    if len(conns) != 1:
        raise SystemExit(f"NCP 저장소 연결 '{name}'이 {len(conns)}개입니다. 하나여야 합니다.")
    await pc.refresh(db)
    store = pc.storage_connection(str(conns[0].id))
    if store is None or not store.complete:
        raise SystemExit(f"저장소 연결 '{name}'의 키나 버킷을 읽지 못했습니다.")
    return store


async def _pin_storage(
    db: AsyncSession, project: Project, store: pc.StorageCredentials | None,
) -> None:
    """고른 저장소를 시드 프로젝트의 저장소 대상과 영상의 고정 기록에 남긴다. 있으면 둔다."""
    from api_gateway.services.media import storage

    if store is None:
        return
    target = pc.storage_target_of(project.settings)
    if target is None or not target.complete:
        target = pc.StorageTarget(store.connection_id, store.bucket)
        project.settings = pc.with_storage_target(project.settings, target)
    for v in (await db.scalars(select(Video).where(Video.project_id == project.project_id))).all():
        if storage.pin_of(v.extra) is None:
            v.extra = {**(v.extra or {}), storage.PIN_KEY: target.as_dict()}
    await db.flush()


async def main(storage_connection: str | None) -> int:
    """개발 시드 진입점. FK 순서대로 find-or-create하고 결과를 찍는다."""
    async with SessionLocal() as db:
        # 0. 저장소 연결
        store = await _storage_of(db, storage_connection)

        # 1. super_admin 조회
        admin_id, admin_email = await _get_super_admin(db)

        # 2. org
        org = await _default_org(db)

        # 3. project
        project = await _find_or_create_project(db, org)

        # 4. membership
        await _ensure_membership(db, project, admin_id)

        # 5. video
        video = await _find_or_create_video(db, project, admin_id, store)

        # 5b. 재생할 시드 샘플 영상을 준비한다. 실패해도 멈추지 않는다.
        ensure_seed_sample_video(store, video.source_uri or "")

        # 6. clips
        clip1, c1_new = await _find_or_create_clip(
            db, video, project,
            start=0.0, end=6.0,
            auto_caption=CLIP_1_AUTO_CAPTION,
            speakers=CLIP_1_SPEAKERS,
            extra=CLIP_1_EXTRA,
        )
        clip2, c2_new = await _find_or_create_clip(
            db, video, project,
            start=6.0, end=12.0,
            auto_caption=CLIP_2_AUTO_CAPTION,
            speakers=CLIP_2_SPEAKERS,
            extra=CLIP_2_EXTRA,
        )

        # 7. work_items
        wi1 = await _ensure_work_item(db, clip1)
        wi2 = await _ensure_work_item(db, clip2)

        # 8. 화면 확인용 데모 데이터셋: 작업자·제출/검수·반복 반려·MAIU
        demo_counts = await _seed_demo(db, project, admin_id, video, store)

        # 9. 고른 저장소를 프로젝트 저장소 대상과 영상 고정 기록에 남긴다.
        await _pin_storage(db, project, store)

        await db.commit()

        # 품질 등급은 계산하지 않는다. 도구는 사람을 등급 매기지 않는다.

    # ── 출력 ──
    print("=" * 60)
    print("  RebornStudio Dev Seed 완료")
    print("=" * 60)
    print(f"  login            : {admin_email}")
    print(f"  org slug         : {org.slug}  (org_id={org.org_id})")
    print(f"  project slug     : {PROJECT_SLUG}  (project_id={project.project_id})")
    print(f"  video_id         : {video.video_id}")
    print(f"  clip 1 id        : {clip1.clip_id}  ({'NEW' if c1_new else 'already seeded'})")
    print(f"  clip 2 id        : {clip2.clip_id}  ({'NEW' if c2_new else 'already seeded'})")
    print(f"  work_item 1 id   : {wi1.work_item_id}")
    print(f"  work_item 2 id   : {wi2.work_item_id}")
    print()
    print("  데모 데이터셋 (화면 확인용):")
    print(f"    데모 작업자      : {demo_counts['workers']}명  → 작업자 운영")
    print(f"    장면 영상        : {demo_counts['scene_videos']}건  → 콘텐츠(영상 탭)")
    print(f"    장면 영상 클립    : {demo_counts['scene_clips']}건  → 콘텐츠(클립 탭)")
    print(f"    라벨링 대기(claim): {demo_counts['available']}건  → 내 작업(라벨링)")
    print(f"    제출/검수 대기    : {demo_counts['submitted']}건  → 검수 큐·워크벤치")
    print(f"    반복 반려        : {demo_counts['repeated']}건  → 라벨링 단계 반려 누적 목록")
    print(f"    MI 분석 행       : {demo_counts['maiu']}건  → 반입 단계 분석 상태")
    print()
    print("  데모 워커 계정(로그인은 make dev-token EMAIL=<계정>) — 워커 화면 확인용:")
    for email, _name, role in DEMO_WORKERS:
        print(f"    {email}  ({role})")
    print()
    print(f"  /work URL (예시) : http://localhost:3000/projects/{project.project_id}/work")
    print()
    print("  NOTE: 영상 재생 — 샘플 MP4 업로드 시도 (위 [스토리지] 행 참조).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    _ap = argparse.ArgumentParser(description="라벨링 워크벤치 개발 시드")
    _ap.add_argument("--storage-connection", default=None,
                     help="시드 프로젝트가 쓸 NCP 저장소 연결 이름(자격증명 연결에 등록한 것)")
    raise SystemExit(asyncio.run(main(_ap.parse_args().storage_connection)))
