"""시연용 데이터를 준비한다. 실제로 분석된 영상 위에 시연할 상태를 만든다.

라벨링·검수 두 단계를 모두 보여 줄 수 있게 한다.

별도 스크립트로 두는 이유: `seed_dev_clip.py`는 워크벤치 1건을, `seed_dev_volume.py`는
목록 화면의 밀도를 만든다. 둘 다 가짜 데이터다. 시연은 다르다. 클립을 눌렀을 때 영상이
실제로 재생되고 AI 요약이 실제 한국어 문장이어야 설득력이 있다. 그래서 이 스크립트는
아무것도 지어내지 않고, 이미 MI 분석을 통과한 프로젝트에 빠진 것만 채운다.

빠지는 것은 늘 같은 두 가지다.
1. 멤버: 라벨러·검수자·관리자가 그 프로젝트에 없으면 워크벤치에 들어갈 수 없다.
2. 검수 대기 물량: work_item이 모두 `available`이면 검수 화면이 비어 있다. 누군가
   라벨링을 제출해야 검수할 것이 생긴다.

상태를 SQL로 만들지 않는 이유: `work_items.status='submitted'`를 UPDATE로 직접 바꾸면
Label revision과 domain_events가 비게 된다.
그러면 시연 중 "검수 이력"이나 "작업자 성과"를 누르는 순간
빈 화면이 나온다. 그래서 서비스 계층(`next_work`·`submit`·`next_review`·`decide`)을 그대로 거친다.
느리지만 만들어지는 상태가 운영과 같다.

시연용으로 남겨 두는 것:
- `available`을 넉넉히 남긴다. 시연 중에 라벨링을 직접 해 보여야 한다.
- `submitted`를 몇 건 남긴다. 검수를 직접 눌러 보여야 한다.

멱등: 이미 제출·승인된 것은 다시 만들지 않는다. `--clear`는 이 스크립트가 만든 것만
되돌린다(사람이 직접 한 결정은 건드리지 않는다).

실행(`.env`를 읽어야 하므로 repo 루트에서 실행한다):
    uv run --package api-gateway python scripts/dev/seed_demo.py
    uv run --package api-gateway python scripts/dev/seed_demo.py --project walkthrough-2026
    uv run --package api-gateway python scripts/dev/seed_demo.py --status   # 상태만 점검
"""
# ruff: noqa: I001  — env vars를 설정한 뒤 import해야 하므로 isort 순서 제어 불가
from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://reborn:reborn@localhost:5433/rebornstudio",
)
os.environ.setdefault("ENVIRONMENT", "local")

# 서버 모듈을 import하기 전에 로컬 DB와 개발 환경인지 확인한다(`_local_only.py`).
from _local_only import refuse_unless_local  # noqa: E402

refuse_unless_local()

from sqlalchemy import func, select, update  # noqa: E402

from api_gateway.database import SessionLocal  # noqa: E402
from api_gateway.security import hash_password  # noqa: E402
from api_gateway.models import (  # noqa: E402
    Clip,
    Label,
    Project,
    ProjectMember,
    User,
    Video,
    WorkItem,
)
from reborn_contracts import WorkSubmitRequest  # noqa: E402
from reborn_contracts.review import ReviewDecisionRequest  # noqa: E402

from api_gateway.services import caption as caption_rules  # noqa: E402
from api_gateway.services.work import review as review_service  # noqa: E402
from api_gateway.services.work import service as work_service  # noqa: E402

DEFAULT_SLUG = "walkthrough-2026"

# 시연 배역. 이미 dev DB에 있는 계정을 재사용하고 새 계정을 만들지 않는다.
# 역할은 관리자·검수자·라벨러 셋이다.
CAST = [
    ("demo.labeler1@example.com", "annotator"),
    ("demo.labeler2@example.com", "annotator"),
    ("demo.labeler3@example.com", "annotator"),
    ("demo.reviewer1@example.com", "reviewer"),
    ("demo.rights1@example.com", "project_admin"),
]

#: 배역 계정 공통 비밀번호. `SEED_DEMO_PASSWORD`가 없으면 무작위 값이고 출력하지 않는다.
#: 로그인은 `make dev-token EMAIL=<계정>`으로 한다.
DEMO_PASSWORD = os.getenv("SEED_DEMO_PASSWORD") or secrets.token_urlsafe(18)

# 이 스크립트가 만든 제출분의 표식. `--clear`는 이 표식이 있는 것만 되돌린다.
MARK = "demo_seed"

# 시연 중에 직접 해 볼 물량. 이만큼은 손대지 않는다.
LEAVE_AVAILABLE = 25     # 라벨링을 직접 시연
LEAVE_SUBMITTED = 8      # 검수를 직접 시연

TARGET_APPROVED = 12     # 검수 이력·작업자 성과가 보이려면 승인분이 필요하다
TARGET_REJECTED = 2      # 재작업 흐름(반려 뒤 라벨러 재제출)을 보여 준다


def _caption_from(summary: str) -> str:
    """AI 요약을 라벨러가 손본 캡션처럼 바꾼다.

    통째로 복사하면 시연에서 AI가 쓴 글을 그대로 제출하는 것처럼 보인다. 워크벤치는
    AI 초안 대비 교정 diff를 보여 주므로, 다듬은 흔적이 남아야 그 화면에 보여 줄 diff가 생긴다.

    캡션은 하나만 만든다. 상세 캡션 필드는 라벨링 화면에도 납품 스키마에도 없다.
    요약을 둘로 나눠 넣으면 시연 화면에만 다른 곳에 없는 항목이 뜬다.
    또 한 문단을 쪼갠 것처럼 보인다.

    Returns:
        캡션. 제출 게이트(기본 50토큰)를 넘겨야 한다.
    """
    text = re.sub(r"\s+", " ", (summary or "").strip())
    sentences = [s.strip() for s in re.split(r"(?<=[.다])\s+", text) if s.strip()]
    brief = " ".join(sentences[:3]) if sentences else text
    # 라벨러의 수정을 흉내 낸다. AI가 흔히 쓰는 군더더기를 지워 교정 diff가 생기게 한다.
    for junk in ("~로 보이는", "~와 유사한", "것으로 보입니다", "듯합니다"):
        brief = brief.replace(junk, "")
    brief = brief.replace("보입니다", "나타납니다").replace("잡히며", "이어지며")
    return brief.strip()


async def _ensure_members(db, project: Project) -> dict[str, list[User]]:
    """배역별 계정을 프로젝트 멤버로 추가한다. 이미 다른 역할이면 덮어쓰지 않는다.

    역할을 덮어쓰지 않는 이유: 직접 조정해 둔 배역이 있을 수 있다. 시드가 그 배역을
    알림 없이 되돌리면 전날과 상태가 달라진 이유를 찾기 어렵다.
    배역 계정의 비밀번호와 승인 상태는 시연 기준으로 맞춘다.
    """
    people: dict[str, list[User]] = {}
    for email, role in CAST:
        user = await db.scalar(select(User).where(User.email == email))
        if not user:
            print(f"  ⚠ {email} 계정이 없다 — 건너뜀")
            continue
        m = await db.get(ProjectMember, (project.project_id, user.user_id))
        if m is None:
            db.add(ProjectMember(
                project_id=project.project_id, user_id=user.user_id,
                role=role, status="active",
            ))
            actual = role
            print(f"  + {email} → {role}")
        else:
            if m.status != "active":
                m.status = "active"
            actual = m.role
            extra = "" if m.role == role else f" (원했던 역할: {role})"
            print(f"  = {email} 이미 {m.role}{extra}")
        # 비밀번호와 승인 상태를 시연 기준으로 맞춘다. 로그인하지 못하는 배역이 있으면
        # 그 워크벤치를 보여 줄 수 없다.
        user.password_hash = hash_password(DEMO_PASSWORD)
        if user.approval_status != "active":
            user.approval_status = "active"
            print(f"    ↳ {email} 승인 상태를 active로")
        people.setdefault(actual, []).append(user)
    await db.commit()
    return people


#: 요약이 짧아 제출 기준에 못 미칠 때 덧붙이는 데모 문장. 한 사람이 한 건씩 맡고 반납이 없어
#: 기준에 못 미친 작업을 놓을 수 없으므로 기준을 넘을 때까지 붙인다.
_FILLER = "화면에는 스튜디오와 현장 장면이 번갈아 이어지고 진행자가 주요 내용을 차례로 설명합니다."


async def _submit_batch(db, project: Project, labelers: list[User], want: int) -> int:
    """라벨러 계정으로 다음 작업과 제출을 실제로 수행한다. 만든 건수를 돌려준다."""
    made = 0
    while made < want:
        progressed = False
        for user in labelers:
            if made >= want:
                break
            try:
                nxt = await work_service.next_work(db, project.project_id, user)
            except Exception as e:  # noqa: BLE001 — 물량 소진은 정상 종료 사유
                if "no_work_available" in str(e):
                    return made
                continue
            item_id = nxt.work_item_id
            item = await db.get(WorkItem, item_id)
            clip = (
                await db.scalar(select(Clip).where(Clip.clip_id == item.clip_id))
                if item and item.clip_id else None
            )
            summary = ((clip.auto_caption or {}).get("summary") if clip else "") or ""
            brief = _caption_from(summary)
            min_tokens = project.caption_min_tokens or caption_rules.DEFAULT_MIN_TOKENS
            while caption_rules.too_short({"caption": {"brief": brief}}, min_tokens) is not None:
                brief = f"{brief} {_FILLER}".strip()
            payload = {
                "caption": {"brief": brief},
                "meta": {"broadcaster": "샘플 방송", "program": "9시 뉴스 샘플",
                         "genre": "보도", "air_date": "", "topic": "검찰 개혁"},
                "tags": {"scene_type": ["스튜디오", "현장"], "entities": [],
                         "events": [], "sentiment": "중립"},
                "speakers": (clip.speakers or []) if clip else [],
                "speaker_segments": [],
                MARK: True,          # `--clear`는 이 표식이 있는 것만 되돌린다
            }
            await work_service.submit(db, item_id, WorkSubmitRequest(payload=payload), user)
            made += 1
            progressed = True
        if not progressed:
            break
    return made


async def _review_batch(db, project: Project, reviewer: User, approve: int, reject: int) -> tuple:
    """검수자 계정으로 승인·반려를 실제로 수행한다. (승인, 반려) 건수를 돌려준다."""
    ok = no = 0
    while ok < approve or no < reject:
        try:
            item = await review_service.next_review(db, project.project_id, reviewer)
        except Exception:  # noqa: BLE001 — 대기 물량 소진
            break
        if no < reject:
            await review_service.decide(db, item.work_item_id, ReviewDecisionRequest(
                decision="reject",
                reason="캡션이 장면을 설명하지 않고 자막을 그대로 옮겼습니다. "
                       "화면에서 무엇이 일어나는지 중심으로 다시 써 주세요.",
                flagged_sentences=[],
            ), reviewer)
            no += 1
        else:
            await review_service.decide(db, item.work_item_id, ReviewDecisionRequest(
                decision="approve", reason=None,
            ), reviewer)
            ok += 1
    return ok, no


async def _release_strays(db, project: Project) -> int:
    """제출도 반려도 되지 않은 채 남아 있는 claim을 반납한다.

    시드 루프가 claim한 뒤 캡션 게이트를 넘지 못해 남긴 작업이다. 그대로 두면 시연 중
    "내 작업"에 정체 모를 항목이 뜬다. 그 라벨러의 동시 작업 한도도 그만큼 줄어든다.
    반려로 돌아온 것(rejection_count>0)은 건드리지 않는다. 재작업 대기이기 때문이다.
    """
    rows = (await db.scalars(
        select(WorkItem).where(
            WorkItem.project_id == project.project_id,
            WorkItem.status == "claimed",
            WorkItem.rejection_count == 0,
        )
    )).all()
    freed = 0
    for item in rows:
        has_label = await db.scalar(
            select(func.count()).select_from(Label).where(Label.work_item_id == item.work_item_id)
        )
        if has_label:
            continue
        item.status = "available"
        item.claimed_by = item.claimed_at = item.expires_at = None
        item.draft = None
        freed += 1
    if freed:
        await db.commit()
    return freed


async def _status(db, slug: str) -> None:
    """프로젝트의 작업 티켓·클립 상태·멤버·재생 가능 현황을 출력한다."""
    project = await db.scalar(select(Project).where(Project.slug == slug))
    if not project:
        print(f"❌ 프로젝트 없음: {slug}")
        return
    print(f"\n■ {project.name} ({slug})")

    rows = (await db.execute(
        select(WorkItem.status, func.count()).where(WorkItem.project_id == project.project_id)
        .group_by(WorkItem.status)
    )).all()
    print("  작업 티켓 :", ", ".join(f"{s} {n}" for s, n in rows) or "없음")

    rows = (await db.execute(
        select(Clip.rights_status, func.count()).where(Clip.project_id == project.project_id)
        .group_by(Clip.rights_status)
    )).all()
    print("  클립 상태 :", ", ".join(f"{s} {n}" for s, n in rows) or "없음")

    members = (await db.execute(
        select(ProjectMember.role, User.email).join(User, User.user_id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.project_id,
               ProjectMember.status == "active")
    )).all()
    print("  멤버      :", ", ".join(f"{e.split('@')[0]}({r})" for r, e in members) or "없음")

    playable = await db.scalar(
        select(func.count()).select_from(Video).where(
            Video.project_id == project.project_id, Video.deleted_at.is_(None),
            Video.extra["transcode"]["playback_key"].isnot(None),
        )
    )
    thumbs = await db.scalar(
        select(func.count()).select_from(Clip).where(
            Clip.project_id == project.project_id,
            func.jsonb_exists(Clip.extra, "scene_thumbnail_key"),
        )
    )
    print(f"  재생 가능 : 영상 {playable}편 · 썸네일 있는 클립 {thumbs}개")


async def _clear(db, slug: str) -> None:
    """이 스크립트가 만든 제출분만 되돌린다. 사람이 한 결정은 건드리지 않는다."""
    project = await db.scalar(select(Project).where(Project.slug == slug))
    if not project:
        raise SystemExit(f"프로젝트 없음: {slug}")
    n = await db.scalar(
        select(func.count()).select_from(WorkItem).where(
            WorkItem.project_id == project.project_id,
            WorkItem.payload[MARK].astext == "true",
        )
    )
    await db.execute(
        update(WorkItem)
        .where(WorkItem.project_id == project.project_id,
               WorkItem.payload[MARK].astext == "true")
        .values(status="available", claimed_by=None, claimed_at=None, expires_at=None,
                submitted_at=None, review_claimed_by=None, review_claimed_at=None,
                review_expires_at=None, payload={})
    )
    await db.commit()
    print(f"↩ 표식분 {n}건을 available로 되돌렸다 (Label 이력은 남는다 — 감사 기록이다)")


async def main() -> None:
    """배역 확인, 라벨링 제출, 검수, claim 정리 순서로 시연 상태를 만든다.

    `--status`는 상태 점검만, `--clear`는 이 스크립트가 만든 제출분 되돌리기만 한다.
    """
    ap = argparse.ArgumentParser(description="시연용 상태 준비")
    ap.add_argument("--project", default=DEFAULT_SLUG, help="대상 프로젝트 slug")
    ap.add_argument("--status", action="store_true", help="상태만 점검하고 끝낸다")
    ap.add_argument("--clear", action="store_true", help="이 스크립트가 만든 제출분만 되돌린다")
    args = ap.parse_args()

    async with SessionLocal() as db:
        if args.status:
            await _status(db, args.project)
            return
        if args.clear:
            await _clear(db, args.project)
            await _status(db, args.project)
            return

        project = await db.scalar(select(Project).where(Project.slug == args.project))
        if not project:
            raise SystemExit(f"프로젝트 없음: {args.project}")

        print(f"■ 대상: {project.name} ({args.project})")
        print("\n[1/3] 배역 확인")
        people = await _ensure_members(db, project)
        labelers = people.get("annotator", [])
        reviewer = (people.get("reviewer") or [None])[0]
        if not labelers or not reviewer:
            raise SystemExit("라벨러 또는 검수자 계정이 없다 — CAST를 확인하세요")

        # 목표는 증분이 아니라 총량이다. 증분으로 하면 재실행할 때마다 쌓여서 시연용으로
        # 남겨 둔 available이 사라진다.
        async def _count(**kw):
            """프로젝트의 작업 수를 센다. `status`나 `rejected=True`로 범위를 좁힌다."""
            q = select(func.count()).select_from(WorkItem).where(
                WorkItem.project_id == project.project_id)
            if "status" in kw:
                q = q.where(WorkItem.status == kw["status"])
            if kw.get("rejected"):
                q = q.where(WorkItem.rejection_count > 0)
            return await db.scalar(q) or 0

        avail = await _count(status="available")
        have_sub = await _count(status="submitted")
        have_ok = await _count(status="approved")
        have_no = await _count(rejected=True)

        need_ok = max(0, TARGET_APPROVED - have_ok)
        need_no = max(0, TARGET_REJECTED - have_no)
        need_sub = max(0, LEAVE_SUBMITTED - have_sub)
        need = need_ok + need_no + need_sub
        budget = max(0, avail - LEAVE_AVAILABLE)
        want = min(need, budget)

        print(f"\n[2/4] 라벨링 제출 — 현재 승인 {have_ok}/{TARGET_APPROVED} · "
              f"반려 {have_no}/{TARGET_REJECTED} · 대기 {have_sub}/{LEAVE_SUBMITTED}")
        if want < need:
            print(f"  ⚠ available {avail}건 — 시연용 {LEAVE_AVAILABLE}건을 남기면 "
                  f"{want}건만 만들 수 있다(필요 {need}건)")
        made = await _submit_batch(db, project, labelers, want) if want else 0
        print(f"  제출 {made}건")

        print(f"\n[3/4] 검수 — 승인 {need_ok}건 · 반려 {need_no}건 더 필요")
        ok, no = (await _review_batch(db, project, reviewer, need_ok, need_no)
                  if (need_ok or need_no) else (0, 0))
        print(f"  승인 {ok}건 · 반려 {no}건")

        print("\n[4/4] 흘러나온 claim 정리")
        freed = await _release_strays(db, project)
        print(f"  {freed}건 반납" if freed else "  없음")

        await _status(db, args.project)
        print("\n✅ 준비 완료. 배역 계정 로그인은 make dev-token EMAIL=<계정>으로 한다.")


if __name__ == "__main__":
    asyncio.run(main())
