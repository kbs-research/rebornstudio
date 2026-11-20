"""단계 화면을 운영 밀도로 채우는 개발 시드.

반입부터 검수까지 단계마다 물량을 심는다.

목록 화면의 밀도·정렬·페이지네이션·상태 색은 물량이 있어야 확인할 수 있다.
`seed_dev_clip.py`는 라벨링 워크벤치 1건을 정밀하게 만들고, 이 스크립트는 목록 화면 전체를
채운다. 목적이 달라 둘을 나눈다.

멱등: 만든 것에 `videos.metadata->>'dev_volume'` 표식을 남기고, 재실행하면 표식이 있는
것만 지우고 다시 심는다. 사람이 직접 만든 데이터는 건드리지 않는다.

저장소: 대상 프로젝트(`default`)가 분석 연동에서 고른 저장소 대상을 영상마다 고정 기록한다.
고르지 않았으면 기록 없이 심는다. 그 영상은 썸네일·재생 주소가 비어 보인다.

분석 상태: 화면 판정(`media/video/classify.py`)이 읽는 `analysis_job`에 MI 영상 단위 행을 쓴다.
'분석 중' 표본은 리컨실러가 처리하지 않도록 백오프 시각을 먼 미래로 둔다(`_HOLD_UNTIL`).
가짜 에셋 번호로 실제 MI를 조회하면 잡이 실패로 넘어가 표본이 '막힘'으로 바뀐다.

실행(`.env`를 읽어야 하므로 repo 루트에서 실행한다):
    uv run --package api-gateway python scripts/dev/seed_dev_volume.py
    uv run --package api-gateway python scripts/dev/seed_dev_volume.py --clear   # 표식분 제거만
"""
# ruff: noqa: I001  — env vars를 설정한 뒤 import해야 하므로 isort 순서 제어 불가
from __future__ import annotations

import argparse
import asyncio
import os
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio",
)
os.environ.setdefault("ENVIRONMENT", "local")

# 서버 모듈을 import하기 전에 로컬 DB와 개발 환경인지 확인한다(`_local_only.py`).
from _local_only import refuse_unless_local  # noqa: E402

refuse_unless_local()

from sqlalchemy import delete, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from api_gateway.database import SessionLocal  # noqa: E402
from api_gateway.models.clip import Clip  # noqa: E402
from api_gateway.models.ingest_batch import IngestBatch  # noqa: E402
from api_gateway.models.analysis_job import AnalysisJob  # noqa: E402
from api_gateway.models.label import Review  # noqa: E402
from api_gateway.models.org import Project  # noqa: E402
from api_gateway.models.user import User  # noqa: E402
from api_gateway.models.video import Video  # noqa: E402
from api_gateway.models.work import WorkItem  # noqa: E402
from api_gateway.services.analysis import job_state  # noqa: E402
from api_gateway.services.analysis.registry import MAIU_KEY  # noqa: E402
from api_gateway.services.media import storage  # noqa: E402

# 재실행하면 이 표식이 있는 영상만 지운다. 직접 만든 데이터와 섞이지 않게 하기 위해서다.
MARKER = "dev_volume"

# 대상 프로젝트. 마이그레이션이 만들지 않으므로 화면에서 이 slug로 먼저 만든다.
PROJECT_SLUG = "default"
_NO_PROJECT = (f"프로젝트 '{PROJECT_SLUG}'가 없습니다. "
               f"화면에서 slug가 {PROJECT_SLUG}인 프로젝트를 먼저 만듭니다")

# 보도영상 제목 모양의 예시 이름. 목록에서 행을 구분하려면 제목이 서로 달라야 한다.
BROADCASTERS = [
    ("샘플 방송", ["9시 뉴스 샘플", "아침 뉴스 샘플", "탐사 보도 샘플", "시사 기획 샘플"]),
    ("예시 방송", ["저녁 뉴스 샘플", "오전 뉴스 샘플", "시사 고발 샘플"]),
    ("견본 방송", ["8시 뉴스 샘플", "심야 뉴스 샘플", "기획 취재 샘플"]),
    ("시범 방송", ["정오 뉴스 샘플", "밤 뉴스 샘플"]),
    ("지역 방송 샘플", ["종합 뉴스 샘플", "사건 브리핑 샘플"]),
]

# 반려 사유. 실제 검수자가 쓸 법한 문장으로 반려 누적 목록의 '마지막 반려 사유' 칸을 채운다.
REJECT_REASONS = [
    "화자 구분이 3명 이상인데 캡션이 1인 기준으로 작성됨",
    "비식별화 누락 — 취재원 얼굴이 그대로 노출",
    "자막에 외부 방송사 로고가 남아 있음 — 권리 관계 불명확",
    "장면 요약과 실제 영상 내용이 불일치",
    "발화 시작·종료 시각이 2초 이상 어긋남",
    "고유명사 표기 오류 반복(지명·인명)",
]

# 장면 요약 문구 재료. 목록에서 클립이 서로 달라 보이게 하는 최소한의 다양성을 준다.
SCENE_KINDS = ["앵커 스튜디오 멘트", "현장 리포트", "인터뷰", "자료 화면", "그래픽 설명"]

# '분석 중'으로 남길 MAIU 진행 단계. 진행 막대가 서로 다른 위치에 오도록 분산한다.
MAIU_STAGES = ["registering", "registered", "analyzing", "analyzing", "analyzed", "summarizing"]

# '분석 중' 표본의 백오프 시각. 리컨실러는 `payload.retry_after`가 지나지 않은 잡을 건너뛴다
# (`reconciler.stages._lock_batch`). 진행 중 요청 수(`in_flight_count`)도 0으로 두어
# 멈춘 잡 회수(`sweep_stale_in_flight`) 대상에서도 빠진다.
_HOLD_UNTIL = "9999-12-31T00:00:00+00:00"

# 막힘 상태의 종류와 표시 이름.
BLOCK_KINDS = [
    ("probe", "규격 위반"),
    ("transcode", "트랜스코딩 실패"),
    ("noticket", "분석 티켓 없음"),
]


def _maiu_job(v: Video, project_id: UUID, asset_id: str, *, updated: datetime) -> AnalysisJob:
    """영상 단위 MI 분석 행을 만든다. 단계와 중립 상태는 호출자가 `job_state.advance`로 정한다."""
    return AnalysisJob(
        video_id=v.video_id, project_id=project_id, clip_id=None, adapter_key=MAIU_KEY,
        external_ref=asset_id, attempt=0, in_flight_count=0, deferred=False, payload={},
        created_at=v.created_at, updated_at=updated,
    )


def _rng() -> random.Random:
    """고정 시드의 난수 생성기를 돌려준다. 재실행해도 같은 목록이 나와야 화면을 비교할 수 있다."""
    return random.Random(20260730)


async def _resolve(db: AsyncSession) -> tuple[Project, User, str | None]:
    """대상 프로젝트·업로더·재생 가능한 source_uri를 찾는다.

    source_uri는 기존 영상에서 빌려 온다. 새로 올릴 파일이 없으므로, 썸네일과 미리보기가
    실제로 뜨게 하려면 이미 스토리지에 있는 객체를 가리켜야 한다.
    """
    project = await db.scalar(select(Project).where(Project.slug == PROJECT_SLUG))
    if not project:
        raise SystemExit(_NO_PROJECT)
    user = await db.scalar(select(User).order_by(User.created_at).limit(1))
    if not user:
        raise SystemExit("사용자가 없습니다 — 먼저 관리자 계정을 만드세요")
    src = await db.scalar(
        select(Video.source_uri)
        .where(Video.project_id == project.project_id, Video.source_uri.is_not(None))
        .limit(1)
    )
    return project, user, src


async def clear(db: AsyncSession, project_id: UUID) -> int:
    """표식이 있는 영상과 그 파생물을 지우고, 지운 영상 수를 돌려준다.

    clips·work_items는 FK cascade 대상이지만 reviews·분석 행(`analysis_job`)과 함께
    순서대로 직접 지운다.
    영상을 지운 뒤 비게 된 반입 묶음도 지운다.
    """
    ids = list(await db.scalars(
        select(Video.video_id).where(
            Video.project_id == project_id,
            text("videos.metadata->>'dev_volume' = 'true'"),
        )
    ))
    if not ids:
        return 0
    # reviews는 work_items의 cascade로 지워지지만, work_items FK가 SET NULL인 경로가 있어
    # 먼저 지운다.
    wids = list(await db.scalars(select(WorkItem.work_item_id).where(WorkItem.video_id.in_(ids))))
    if wids:
        await db.execute(delete(Review).where(Review.work_item_id.in_(wids)))
    await db.execute(delete(AnalysisJob).where(AnalysisJob.video_id.in_(ids)))
    await db.execute(delete(WorkItem).where(WorkItem.video_id.in_(ids)))
    await db.execute(delete(Clip).where(Clip.video_id.in_(ids)))
    batch_ids = [
        b for b in await db.scalars(
            select(Video.upload_batch_id).where(
                Video.video_id.in_(ids), Video.upload_batch_id.is_not(None)
            ).distinct()
        )
    ]
    await db.execute(delete(Video).where(Video.video_id.in_(ids)))
    # 시드가 만든 묶음 행을 정리한다. 영상 삭제 후 비게 된 묶음만 지운다(직접 만든 묶음 보호).
    if batch_ids:
        still_used = set(await db.scalars(
            select(Video.upload_batch_id).where(Video.upload_batch_id.in_(batch_ids))
        ))
        empty = [b for b in batch_ids if b not in still_used]
        if empty:
            await db.execute(delete(IngestBatch).where(IngestBatch.batch_id.in_(empty)))
    await db.commit()
    return len(ids)


def _make_video(
    *, project_id: UUID, user_id: UUID, src: str | None, rnd: random.Random,
    idx: int, created: datetime, batch: UUID | None, pin: dict | None = None,
) -> Video:
    """영상 1건을 만든다. 방송사·프로그램·회차·길이를 분산해 목록이 실제처럼 보이게 한다.

    `pin`은 프로젝트가 고른 저장소 대상이다. 주면 영상에 고정 기록한다(`media.storage.pin_for`).
    """
    caster, programs = rnd.choice(BROADCASTERS)
    program = rnd.choice(programs)
    aired = created - timedelta(days=rnd.randint(0, 120))
    slug = f"{caster.lower()}-{program}-{aired:%Y%m%d}-{idx:03d}"
    return Video(
        video_id=uuid4(),
        project_id=project_id,
        uploaded_by=user_id,
        title=f"{program} {aired:%Y.%m.%d} ({caster})",
        source_uri=src,
        duration_seconds=float(rnd.randint(8, 62) * 60 + rnd.randint(0, 59)),
        file_size_bytes=rnd.randint(400, 4200) * 1024 * 1024,
        copyright_policy="pre_cleared",
        copyright_status="cleared",
        upload_status="uploaded",
        probe_status="passed",
        upload_batch_id=batch,
        broadcaster=caster,
        program_name=program,
        episode=str(rnd.randint(100, 9999)),
        aired_at=aired,
        created_at=created,
        updated_at=created,
        # registered_from=storage_browse로 두어 트랜스코딩 워커가 건너뛰게 한다.
        # 시드 영상은 빌린 source_uri만 있어서 버킷에 source 객체가 없다. 이 표식이 없으면
        # 워커가 404를 3회 받고 모두 transcode_failed(막힘)로 고정한다.
        extra={MARKER: True, "source_key": f"news/{slug}.mp4", "registered_from": "storage_browse",
               **({storage.PIN_KEY: pin} if pin else {})},
    )


def _make_clips(video: Video, n: int, rnd: random.Random) -> list[Clip]:
    """씬 클립 n개를 만든다. 20~180초 길이의 구간을 겹치지 않게 순서대로 자른다."""
    clips, t = [], float(rnd.randint(0, 40))
    head = f"{video.broadcaster} {video.program_name}"
    for _ in range(n):
        dur = float(rnd.randint(20, 180))
        clips.append(Clip(
            clip_id=uuid4(), video_id=video.video_id, project_id=video.project_id,
            start_seconds=Decimal(str(round(t, 3))),
            end_seconds=Decimal(str(round(t + dur, 3))),
            rights_status="auto_cleared",
            # 장면 요약은 컬럼이 아니라 auto_caption.summary에 있다(MAIU 응답 원형을 보관한다).
            auto_caption={"summary": f"{head} — {rnd.choice(SCENE_KINDS)} 장면"},
            created_at=video.created_at, updated_at=video.created_at,
        ))
        t += dur + rnd.randint(1, 8)
    return clips


async def seed(db: AsyncSession) -> dict[str, int]:  # noqa: PLR0915 — 상태별 분기라 나누면 오히려 흩어진다
    """단계마다 처리할 일이 있도록 물량을 심고, 항목별 건수를 돌려준다.

    분포는 운영 데이터의 일반적인 비율을 따른다. 완료가 가장 많고 판정 대기가 그다음이며,
    막힘·반복 반려는 소수다. 모두 같은 수로 심으면 필터마다 결과가 같아 보여 필터를 확인할 수 없다.
    """
    rnd = _rng()
    project, user, src = await _resolve(db)
    pid, uid = project.project_id, user.user_id
    pin = storage.pin_for(project.settings)
    now = datetime.now(UTC)
    stat: dict[str, int] = {}

    # 반입 배치 2개를 만든다. '방금 올린 묶음'만 걸러 보는 흐름을 화면에서 확인하기 위해서다.
    # 묶음 행을 영상보다 먼저 만든다. videos.upload_batch_id에 FK가 걸려 있다(0045).
    batch_recent, batch_old = uuid4(), uuid4()
    db.add(IngestBatch(
        batch_id=batch_recent, project_id=pid, created_by=uid, source="upload",
        label="시드 — 최근 반입분", created_at=now,
    ))
    db.add(IngestBatch(
        batch_id=batch_old, project_id=pid, created_by=uid, source="ncp",
        label="시드 — 이전 반입분", created_at=now - timedelta(days=2),
    ))
    await db.flush()

    videos: list[Video] = []
    clips: list[Clip] = []
    works: list[WorkItem] = []
    jobs: list[AnalysisJob] = []
    reviews: list[Review] = []
    idx = 0

    def add_video(created: datetime, batch: UUID | None = None) -> Video:
        """영상을 하나 만들어 `videos`에 추가하고 돌려준다."""
        nonlocal idx
        idx += 1
        v = _make_video(project_id=pid, user_id=uid, src=src, rnd=rnd,
                        idx=idx, created=created, batch=batch, pin=pin)
        videos.append(v)
        return v

    # ── 분석 대기 (11): 통과했고 영상 단위 티켓을 가진 영상이다.
    for i in range(11):
        v = add_video(now - timedelta(hours=8 + i), batch_recent if i < 4 else batch_old)
        works.append(WorkItem(
            work_item_id=uuid4(), video_id=v.video_id, project_id=pid,
            clip_id=None, status="available", rejection_count=0, payload={},
            created_at=v.created_at, updated_at=v.created_at,
        ))
    stat["ready"] = 11

    # ── 분석 중 (6): MI 진행 단계를 분산해 진행률 표시를 확인한다.
    for i, st in enumerate(MAIU_STAGES):
        v = add_video(now - timedelta(hours=20 + i), batch_old)
        job = _maiu_job(
            v, pid, f"asset-{i:04d}", updated=now - timedelta(minutes=rnd.randint(1, 50)),
        )
        job_state.advance(job, st)
        # 화면 확인용 표본이라 외부 API를 호출하지 않게 한다(`_HOLD_UNTIL`).
        job.payload = {"retry_after": _HOLD_UNTIL}
        jobs.append(job)
    stat["analyzing"] = 6

    # ── 완료 (22): 클립이 생성된 영상이다. 라벨링·검수 물량이 여기서 나온다.
    done_videos: list[Video] = []
    for i in range(22):
        v = add_video(now - timedelta(days=1, hours=i), batch_old)
        done_videos.append(v)
        job = _maiu_job(
            v, pid, f"asset-done-{i:04d}", updated=v.created_at + timedelta(minutes=40),
        )
        job_state.advance(job, "done")
        jobs.append(job)
        clips.extend(_make_clips(v, rnd.randint(9, 41), rnd))
    stat["done"] = 22

    # ── 막힘 (4): 조치할 수 없는 것 2 + 복구 가능한 것 2(복구 버튼 동작 확인용).
    for kind, _label in BLOCK_KINDS:
        v = add_video(now - timedelta(days=2, hours=idx))
        if kind == "probe":
            v.probe_status = "rejected"
        elif kind == "transcode":
            # 변환 실패는 `transcode_status`로 표시한다.
            # 시드가 운영에서 나올 수 없는 상태를 만들면 그 상태를 그리는 화면 분기를 지울 수 없고,
            # 그 값이 아직 쓰인다고 오해하게 된다. `upload_status`에는 변환 실패 값을 쓰지 않는다.
            v.transcode_status = "failed"
        # noticket: 티켓이 없는 영상이다(복구 대상). 따로 바꿀 값이 없다.
    add_video(now - timedelta(days=2, hours=idx))  # 두 번째 복구 대상
    stat["blocked"] = 4

    db.add_all(videos)
    await db.flush()
    db.add_all(clips)
    db.add_all(jobs)
    await db.flush()

    # ── 라벨링·검수: 클립은 `auto_cleared`로 생겨 모두 작업 대상이다.
    cleared = list(clips)
    rnd.shuffle(cleared)
    counts = {"available": 0, "claimed": 0, "submitted": 0, "approved": 0, "repeated": 0}
    for i, c in enumerate(cleared):
        if i < 14:
            st, rej = "submitted", 0          # 검수 대기
        elif i < 22:
            st, rej = "claimed", 0            # 라벨링 진행 중
        elif i < 28:
            st, rej = "repeated", 3           # 반려 3회 뒤 재작업 중
        elif i < 120:
            st, rej = "approved", 0           # 처리 완료(진척률 재료)
        else:
            st, rej = "available", 0          # 라벨링 대기
        created = c.created_at + timedelta(hours=2)
        w = WorkItem(
            work_item_id=uuid4(), video_id=c.video_id, project_id=pid, clip_id=c.clip_id,
            status="claimed" if st == "repeated" else st, rejection_count=rej, payload={},
            created_at=created, updated_at=created,
        )
        if st == "claimed":
            w.claimed_by, w.claimed_at = uid, now - timedelta(minutes=rnd.randint(3, 50))
        if st == "submitted":
            w.claimed_by = uid
            w.submitted_at = now - timedelta(minutes=rnd.randint(5, 300))
        if st == "repeated":
            last = now - timedelta(days=rnd.randint(0, 11))
            w.claimed_by, w.claimed_at = uid, last
            # 반려 누적 목록의 '마지막 반려 사유' 칸을 채운다.
            # 회차마다 사유가 달라야 마지막 사유가 보인다.
            for k in range(3):
                reviews.append(Review(
                    review_id=uuid4(), work_item_id=w.work_item_id, reviewer_id=uid,
                    verdict="reject",
                    comment=rnd.choice(REJECT_REASONS) if k == 2 else "재작업 요청",
                    reviewed_at=last - timedelta(hours=3 - k),
                ))
        counts[st] += 1
        works.append(w)

    db.add_all(works)
    await db.flush()
    db.add_all(reviews)
    await db.commit()

    stat.update({f"work_{k}": v for k, v in counts.items()})
    stat["clips"] = len(clips)
    stat["videos"] = len(videos)
    return stat


async def main() -> None:
    """`--clear`면 표식분을 지우기만 하고, 아니면 지운 뒤 다시 시드한다."""
    ap = argparse.ArgumentParser(description="단계 화면 물량 시드")
    ap.add_argument("--clear", action="store_true", help="시드 제거만 하고 종료")
    args = ap.parse_args()

    async with SessionLocal() as db:
        project = await db.scalar(select(Project).where(Project.slug == PROJECT_SLUG))
        if not project:
            raise SystemExit(_NO_PROJECT)
        removed = await clear(db, project.project_id)
        print(f"🧹 기존 시드 영상 {removed}건 제거")
        if args.clear:
            return
        stat = await seed(db)
        print("✅ 시드 완료")
        for k, v in stat.items():
            print(f"   {k:18} {v}")
        print(
            "\nℹ️  '분석 중' 6건은 화면 확인용 표본이다(가짜 asset_id).\n"
            "   리컨실러가 처리하지 않도록 백오프 시각을 먼 미래로 두었다."
        )


if __name__ == "__main__":
    asyncio.run(main())
