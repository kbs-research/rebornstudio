"""도메인 테스트 공통 헬퍼. 격리 프로젝트, 멤버, 영상 업로드, 클립 작업 시드를 만든다."""
import hashlib
from decimal import Decimal
from uuid import UUID

import httpx
from api_gateway.database import SessionLocal
from api_gateway.models import Clip, DomainEvent, Project, User, Video, WorkItem
from api_gateway.services.org import organization as org_service
from httpx import AsyncClient
from sqlalchemy import select
from tests import _storage
from tests.conftest import uniq


async def default_org_id() -> str:
    """기본 조직의 id를 돌려준다. 조직은 한 행뿐이고 프로젝트와 연결이 모두 그 조직에 속한다."""
    async with SessionLocal() as s:
        return str(await org_service.default_org_id(s))


async def new_project(
    client: AsyncClient, admin_token: str, slug_prefix: str = "p",
    *, analysis_preset: str | None = None, caption_min_tokens: int | None = None,
    storage: bool = True,
) -> str:
    """테스트용 프로젝트를 하나 만들고 ID를 돌려준다.

    `analysis_preset`을 주면 그 스택으로 만든다. 스택은 생성 시 고정된다.
    `storage`가 참이면 테스트 저장소 연결을 저장소 대상으로 기록한다(`tests._storage.pin_project`).
    저장소 대상이 없으면 영상 등록이 409로 막히므로, 그 상태를 확인하는 테스트만 거짓을 준다.
    `caption_min_tokens`는 제출 게이트를 피하려는 테스트만 준다.
    수정률의 편집 거리처럼 캡션 자체가 테스트 대상이면 채움 문장이 측정 대상을 가리기 때문이다.
    그 밖의 테스트는 기본값(50)을 그대로 쓰고 `caption_payload`로 제출한다.
    최소 길이는 API로 바꿀 수 없는 고정 기본값이라 DB에 직접 쓴다(`set_project_options`).
    """
    slug = f"{slug_prefix}-{uniq('').split('@')[0]}"
    body = {"slug": slug, "name": slug}
    if analysis_preset:
        body["analysis_preset"] = analysis_preset
    r = await client.post(
        "/projects", headers={"Authorization": f"Bearer {admin_token}"}, json=body,
    )
    # 생성이 거절되면 응답 본문을 그대로 보인다. 본문 없이 KeyError로 끝나면 거절 사유를 찾을 수 없다.
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    if storage:
        await _storage.pin_project(pid)
    if caption_min_tokens is not None:
        await set_project_options(pid, caption_min_tokens=caption_min_tokens)
    return pid


async def set_project_options(
    project_id: str, *, settings: dict | None = None, **columns: object,
) -> None:
    """고정 처리 옵션을 DB에 직접 쓴다. 옵션에 따라 달라지는 동작을 테스트할 때 쓴다.

    처리 옵션(보유 만료 시간·캡션 최소 길이·원본 보관·클립 길이·반려 사유 등)은 API로 바꿀 수 없다.
    동작 코드는 프로젝트 행에서 값을 읽는다. 그래서 다른 값의 동작은 행을 직접 고쳐 테스트한다.

    Args:
        project_id: 고칠 프로젝트.
        settings: `projects.settings`에 병합할 키. 다른 키는 그대로 둔다.
        **columns: `Project` 컬럼 이름과 값(`caption_min_tokens=0` 등).
    """
    async with SessionLocal() as s:
        project = await s.get(Project, UUID(project_id))
        assert project is not None, project_id
        for key, value in columns.items():
            assert hasattr(Project, key), key
            setattr(project, key, value)
        if settings:
            project.settings = {**(project.settings or {}), **settings}
        await s.commit()


async def user_id_by_email(email: str) -> str:
    """계정의 id를 이메일로 찾는다. 이메일만 아는 테스트가 쓴다."""
    async with SessionLocal() as s:
        uid = await s.scalar(select(User.user_id).where(User.email == email))
    assert uid is not None, f"없는 이메일: {email}"
    return str(uid)


#: 테스트 클라이언트(`base_url` http://test)의 자기 출처. refresh·logout의 출처 확인을 통과한다.
SAME_ORIGIN = {"Origin": "http://test"}

#: refresh 토큰 쿠키 이름. 앱의 `session_cookie.REFRESH_COOKIE`와 같다.
REFRESH_COOKIE = "reborn_refresh"


def refresh_headers(token: str | None) -> dict[str, str]:
    """refresh 쿠키와 자기 출처를 실은 요청 헤더를 만든다.

    앱은 쿠키를 `Path=/api/auth`로 주고 테스트는 `/api` 접두 없이 부르므로 클라이언트의 쿠키 저장소가
    쿠키를 붙이지 않는다. 그래서 요청마다 헤더로 싣는다.
    """
    if token is None:
        return dict(SAME_ORIGIN)
    return {**SAME_ORIGIN, "Cookie": f"{REFRESH_COOKIE}={token}"}


def refresh_cookie_of(response: httpx.Response) -> str | None:
    """응답의 `Set-Cookie`에서 refresh 토큰을 꺼낸다. 없으면 None이다."""
    return response.cookies.get(REFRESH_COOKIE)


async def refresh_with_cookie(client: AsyncClient, token: str | None) -> httpx.Response:
    """refresh 쿠키를 실어 `POST /auth/refresh`를 부른다."""
    return await client.post("/auth/refresh", headers=refresh_headers(token))


async def events(
    *, event_type: str | None = None, target_id: str | None = None,
    project_id: str | None = None, actor_user_id: str | None = None,
) -> list[dict]:
    """감사 이벤트(`domain_events`)를 최신순으로 읽는다.

    감사 로그를 조회하는 API가 없으므로 DB에서 직접 읽는다. `event_type`은 앞부분 일치로 거른다.
    돌려주는 행의 id 값은 문자열이다.
    """
    q = select(DomainEvent).order_by(DomainEvent.occurred_at.desc(), DomainEvent.event_id.desc())
    if event_type:
        q = q.where(DomainEvent.event_type.startswith(event_type))
    if target_id:
        q = q.where(DomainEvent.target_id == UUID(target_id))
    if project_id:
        q = q.where(DomainEvent.project_id == UUID(project_id))
    if actor_user_id:
        q = q.where(DomainEvent.actor_user_id == UUID(actor_user_id))
    async with SessionLocal() as s:
        rows = (await s.scalars(q)).all()
    return [{
        "event_type": e.event_type, "payload": e.payload or {},
        "target_kind": e.target_kind,
        "target_id": str(e.target_id) if e.target_id else None,
        "project_id": str(e.project_id) if e.project_id else None,
        "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
        "occurred_at": e.occurred_at,
    } for e in rows]


async def make_account(
    client: AsyncClient, admin_token: str, prefix: str = "u", *, consent: bool = True,
) -> tuple[str, str, str, str]:
    """로그인할 수 있는 활성 계정을 만들고 (uid, email, password, token)을 돌려준다.

    관리자 계정 생성, 재설정 링크로 비밀번호 설정, 로그인 순서로 만든다.
    공개 가입이 없으므로 실제 사용자가 계정을 받는 순서와 같다.
    이메일과 비밀번호로 다시 로그인해야 하는 테스트가 쓴다.

    Args:
        prefix: 이메일 앞부분과 표시 이름에 쓰는 문자열.
        consent: 참이면 필수 개인정보 동의까지 마친다. 동의 전 상태를 테스트할 때만 거짓을 준다.
    """
    email = uniq(prefix)
    pwd = f"DomTest{prefix.title()}123!@"
    created = await client.post(
        "/admin/users", headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": email, "display_name": prefix},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    reset = await client.post(
        "/auth/password/reset/confirm",
        json={"token": body["reset"]["token"], "new_password": pwd},
    )
    assert reset.status_code == 204, reset.text
    login = await client.post("/auth/login", json={"email": email, "password": pwd})
    assert login.status_code == 200, login.text
    tok = login.json()["session"]["access_token"]
    if consent:
        r = await client.post(
            "/users/me/privacy-consent", headers={"Authorization": f"Bearer {tok}"}, json={},
        )
        assert r.status_code == 200, r.text
    return body["user"]["user_id"], email, pwd, tok


async def make_user(
    client: AsyncClient, admin_token: str, role_prefix: str = "u"
) -> tuple[str, str]:
    """로그인할 수 있는 활성 사용자를 만들고 (uid, token)을 돌려준다. 어느 프로젝트의 멤버도 아니다.

    `make_account`로 필수 동의까지 마친 계정을 만든다.
    """
    uid, _email, _pwd, tok = await make_account(client, admin_token, role_prefix)
    return uid, tok


async def invite_user(
    client: AsyncClient, admin_token: str, project_id: str, uid: str, role: str
) -> None:
    """이미 있는 계정을 관리자가 이메일로 프로젝트에 초대한다. 멤버는 초대로만 들어온다."""
    async with SessionLocal() as s:
        email = await s.scalar(select(User.email).where(User.user_id == UUID(uid)))
    r = await client.post(
        f"/projects/{project_id}/members/invite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": email, "role": role},
    )
    assert r.status_code == 200, r.text


async def add_member(
    client: AsyncClient, admin_token: str, project_id: str, role: str
) -> tuple[str, str]:
    """프로젝트 멤버를 만들고 (uid, token)을 돌려준다.

    `make_user`로 계정을 만들고 관리자가 이메일로 초대한다(`invite_user`).
    토큰은 `make_user`가 로그인해 받은 토큰이다. 초대 뒤에 다시 발급하지 않는다.
    """
    uid, tok = await make_user(client, admin_token, role)
    await invite_user(client, admin_token, project_id, uid, role)
    return uid, tok


async def upload_video(
    client: AsyncClient,
    admin_token: str,
    project_id: str,
    *,
    title: str = "test-vid",
    policy: str = "pre_cleared",
    batch: str | None = None,
    probed: bool = False,
) -> str:
    """영상을 등록하고 S3 PUT과 complete까지 마친 뒤 video_id를 돌려준다.

    `batch`를 주면 같은 반입 묶음에 넣는다. 서버가 첫 영상에서 묶음 행을 만든다.
    새 영상은 모두 `cleared`로 등록된다.
    `probed`가 참이면 규격 검증 통과와 길이를 채운다(`mark_probed`).
    분석 요청 게이트는 검증을 통과하고 길이가 있는 영상만 보낸다.
    """
    body: dict = {"title": title, "copyright_policy": policy}
    if batch:
        body["upload_batch_id"] = batch
    vid = (
        await client.post(
            f"/projects/{project_id}/videos",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=body,
        )
    ).json()["video_id"]
    await put_video_file(client, admin_token, vid)
    if probed:
        await mark_probed(vid)
    return vid


async def put_video_file(
    client: AsyncClient, token: str, video_id: str, body: bytes = b"x" * 64,
) -> httpx.Response:
    """화면과 같은 멀티파트 경로로 파일을 올리고 업로드 완료 응답을 돌려준다.

    파트 하나를 presigned PUT으로 올린 뒤 완료를 부른다. 완료 요청에는 내용 해시를 실어
    같은 바이트를 두 번 올리면 중복으로 표시되게 한다. 화면도 내용 해시를 보낸다.
    """
    h = {"Authorization": f"Bearer {token}"}
    init = (await client.post(f"/videos/{video_id}/upload/multipart", headers=h)).json()
    urls = (await client.post(
        f"/videos/{video_id}/upload/parts", headers=h,
        json={"upload_id": init["upload_id"], "part_numbers": [1]},
    )).json()["urls"]
    async with httpx.AsyncClient() as c:
        put = await c.put(urls["1"], content=body)
        assert put.status_code == 200, put.text
    return await client.post(
        f"/videos/{video_id}/upload/complete", headers=h,
        json={"upload_id": init["upload_id"],
              "content_hash": hashlib.sha256(body).hexdigest()},
    )


async def mark_probed(video_id: str, *, duration: float = 60.0) -> None:
    """영상을 규격 검증 통과 상태로 바꾸고 길이가 없으면 채운다.

    probe 배치는 테스트에서 돌지 않는다. 분석 요청 게이트는 검증 통과와 길이를 요구하므로
    요청이 나가야 하는 테스트는 이 함수로 그 상태를 만든다.
    """
    async with SessionLocal() as s:
        v = await s.get(Video, UUID(video_id))
        v.probe_status = "passed"
        if not v.duration_seconds:
            v.duration_seconds = duration
        await s.commit()


async def seed_clip_work(
    project_id: str,
    video_id: str,
    *,
    rights_status: str = "auto_cleared",
    start: float = 0.0,
    end: float = 5.0,
) -> tuple[str, str]:
    """클립과 클립 work_item을 시드하고 (clip_id, work_item_id)를 돌려준다.

    사람 작업(라벨링·검수)은 클립 단위로만 이뤄진다(`_human_workable`).
    그래서 claim이 필요한 테스트는 영상 업로드만으로는 부족하다.
    이 함수가 MAIU 분석 결과인 클립을 대신 만든다.
    """
    async with SessionLocal() as s:
        clip = Clip(
            video_id=UUID(video_id),
            project_id=UUID(project_id),
            start_seconds=Decimal(str(start)),
            end_seconds=Decimal(str(end)),
            rights_status=rights_status,
        )
        s.add(clip)
        await s.flush()
        item = WorkItem(
            video_id=UUID(video_id),
            project_id=UUID(project_id),
            clip_id=clip.clip_id,
            status="available",
        )
        s.add(item)
        await s.commit()
        return str(clip.clip_id), str(item.work_item_id)


async def claim_review(
    client: AsyncClient, token: str, project_id: str, work_item_id: str
) -> None:
    """검수 판정 전에 그 작업을 가져온다. 판정은 검수를 보유한 사람만 한다.

    가져오기 순서는 서버가 정한다(제출 순).
    다른 작업을 가져오면 테스트 시드가 예상과 다른 상태다.
    그래서 넘어가지 않고 여기서 단언으로 실패시킨다.
    """
    r = await client.post(
        f"/projects/{project_id}/review/next",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"검수 가져오기 실패 {r.status_code}: {r.text[:300]}"
    got = r.json()["work_item_id"]
    assert got == work_item_id, f"판정하려던 {work_item_id} 대신 {got}이 왔다"


async def latest_label_id(client: AsyncClient, token: str, work_item_id: str) -> str:
    """작업의 최신 라벨 ID를 돌려준다. 수정 후 승인이 기준 라벨로 싣는 값이다."""
    r = await client.get(
        f"/work/{work_item_id}/labels", headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"라벨 조회 실패 {r.status_code}: {r.text[:300]}"
    return r.json()[-1]["label_id"]


async def reset_transcode_active() -> None:
    """변환·publish·비식별화의 활성 행(pending/queued/running)을 초기화한다. 테스트 격리용이다.

    dispatch는 전역 상한과 `created_at` 순서를 본다.
    그래서 앞 테스트가 남긴 활성 행이 뒤 테스트의 상한 계산과 선발 순서를 바꾼다.
    이 경우 파일 단독 실행은 통과하고 전량 실행만 실패한다.
    """
    from api_gateway.models import Video
    from sqlalchemy import update

    async with SessionLocal() as s:
        await s.execute(
            update(Video)
            .where(Video.transcode_status.in_(("pending", "queued", "running")))
            .values(transcode_status="skipped", transcode_token=None,
                    transcode_lease_until=None)
        )
        # publish도 같은 이유로 초기화한다. 상한을 함께 쓰는 별개 큐다.
        await s.execute(
            update(Video)
            .where(Video.publish_status.in_(("pending", "queued", "running")))
            .values(publish_status="skipped", publish_token=None,
                    publish_lease_until=None)
        )
        # 비식별화도 초기화한다. 전용 상한 `deident_max_inflight`가 기본 1이다.
        # 그래서 앞 테스트가 남긴 running 하나가 다음 테스트의 비식별화를 모두 막는다.
        await s.execute(
            update(Video)
            .where(Video.deident_status.in_(("pending", "queued", "running")))
            .values(deident_status="skipped", deident_token=None,
                    deident_lease_until=None)
        )
        await s.commit()


#: 제출 게이트를 넘는 채움 문장. 기본 기준 `caption_min_tokens=50`보다 길다.
#:
#: `{"caption": "x"}` 같은 한 글자 캡션은 프로젝트 설정의 최소 길이에 못 미쳐 제출할 수 없다.
#: 테스트도 화면이 보내는 모양으로 제출해야 제출 경로를 실제로 테스트한다.
_CAPTION_FILLER = (
    "앵커가 오늘 첫 소식으로 전국에 내린 폭우 피해를 전하고 현장 기자를 연결해 상황을 묻는다. "
    "기자는 하천 수위가 경보선을 넘었다고 답하며 제방 아래 주차장이 물에 잠긴 모습을 보여 준다. "
    "화면 아래에는 대피 안내 자막이 이어진다."
)


def caption_payload(note: str = "", **extra) -> dict:
    """제출 게이트를 넘는 캡션 payload를 만든다. 지금 화면이 보내는 모양과 같다.

    캡션 내용이 테스트 대상이 아닐 때 쓴다.
    수정률의 편집 거리처럼 캡션이 테스트 대상이면 캡션을 직접 쓴다.
    그리고 그 프로젝트의 `caption_min_tokens`를 DB에서 낮춰 게이트를 피한다.
    채움 문장을 붙이면 편집 거리 같은 측정값에 채움 문장이 섞인다.

    Args:
        note: 이 제출을 구별하는 문구(`처음 라벨`·`최종 제출`). 캡션 앞에 붙고 최소 길이는 채움 문장으로 채운다.
        **extra: payload에 함께 넣을 필드(`tags`·`meta` 등).
    """
    text = f"{note} {_CAPTION_FILLER}".strip()
    return {
        "caption": {"brief": text, "brief_sentences": [{"id": "s0", "text": text}]},
        **extra,
    }
