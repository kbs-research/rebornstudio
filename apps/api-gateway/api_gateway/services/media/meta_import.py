"""등록 묶음의 영상 메타를 표 파일로 일괄 입력하는 서비스.

세 함수가 화면의 세 단계를 맡는다.
  1. `template`: 묶음의 모든 영상과 지금 값을 채운 양식(.xlsx)을 만든다.
  2. `preview`: 올린 파일의 행을 묶음의 영상과 맞추고, 영상 메타 편집과 같은 규칙으로 검사해
     행마다 바뀌는 값과 오류를 돌려준다. 아무것도 저장하지 않는다.
  3. `apply`: 미리 보기에서 확인한 변경을 한 트랜잭션으로 저장한다. 요청 값은 다시 검사한다.

판정 규칙은 서버 한 곳에 둔다. 화면은 미리 보기 결과를 그리고, 반영할 행을 골라 보낸다.
파일 읽기와 셀 값 변환은 `media.meta_sheet`가 한다.
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from pydantic import ValidationError
from reborn_contracts import (
    BulkMetaApplyRequest,
    BulkMetaApplyResult,
    BulkMetaFieldChange,
    BulkMetaPreview,
    BulkMetaPreviewCounts,
    BulkMetaPreviewRow,
    BulkMetaRowError,
    VideoUpdateRequest,
)
from reborn_contracts.video import ORIGINAL_FILENAME_KEY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import IngestBatch, Project, User, Video
from api_gateway.services import audit, authz
from api_gateway.services.media import meta_sheet
from api_gateway.services.media.meta_sheet import FIELD_KEYS, KST, Formula, cell_text
from api_gateway.services.media.video import apply_meta

#: 감사 기록에서 일괄 입력으로 바뀐 영상을 단건 편집과 구분하는 표시(`video.updated`의 `via`).
VIA_BULK_IMPORT = "bulk_import"

#: 양식 파일의 MIME 형식.
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MatchedBy = Literal["video_id", "file", "title"]


@dataclass(frozen=True)
class _Video:
    """맞추기와 비교에 쓰는 영상 한 개의 값.

    영상 행 전체(`metadata` JSONB 포함)를 읽지 않으려고 필요한 열만 담는다.
    """
    video_id: UUID
    title: str
    broadcaster: str | None
    program_name: str | None
    genre: str | None
    episode: str | None
    aired_at: datetime | None
    key: str | None
    original_filename: str | None

    @property
    def file(self) -> str | None:
        """양식의 파일 열에 쓸 값. 브라우저에서 올린 원래 파일 이름, 없으면 저장소 경로다."""
        return self.original_filename or self.key


def _nfc(text: str) -> str:
    """비교할 문자열을 NFC로 맞추고 앞뒤 공백을 지운다."""
    return unicodedata.normalize("NFC", text).strip()


def _kst_day(at: datetime | None):
    """방영일의 KST 날짜를 돌려준다. 같은 날의 다른 시각은 같은 방영일로 본다."""
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(KST).date()


async def _load_batch(db: AsyncSession, batch_id: UUID, user: User) -> IngestBatch:
    """묶음을 읽고 운영 권한을 확인한다.

    Raises:
        HTTPException: 묶음이 없는 경우(404 `batch_not_found`),
            관리자가 아닌 경우(403 `not_project_admin`).
    """
    batch = await db.get(IngestBatch, batch_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")
    await authz.require_project_admin(db, batch.project_id, user)
    return batch


async def _batch_videos(db: AsyncSession, batch_id: UUID, project_id: UUID) -> list[_Video]:
    """묶음에서 삭제되지 않은 영상을 모두 읽는다. 순서는 제목순이다.

    양식과 맞추기는 묶음 전체가 대상이라 한 번에 200개까지 돌려주는 영상 목록 조회를 쓰지 않는다.
    묶음 프로젝트의 영상만 읽는다. 기존 데이터에는 다른 프로젝트 영상이 같은 묶음 id를 달고 있을 수
    있고, 그 영상의 메타가 이 묶음의 양식으로 나가면 안 된다.
    """
    rows = (await db.execute(
        select(
            Video.video_id, Video.title, Video.broadcaster, Video.program_name, Video.genre,
            Video.episode, Video.aired_at,
            Video.extra["key"].astext, Video.extra[ORIGINAL_FILENAME_KEY].astext,
        )
        .where(Video.upload_batch_id == batch_id, Video.project_id == project_id,
               Video.deleted_at.is_(None))
        .order_by(Video.title, Video.video_id)
    )).all()
    return [_Video(*r) for r in rows]


async def template(db: AsyncSession, batch_id: UUID, user: User) -> tuple[bytes, str]:
    """묶음의 모든 영상과 지금 메타를 채운 양식을 만든다.

    Returns:
        tuple: 파일 내용과 파일 이름(`<프로젝트 슬러그>-batch-<묶음 ID 앞 8자>-meta.xlsx`).

    Raises:
        HTTPException: 묶음이 없는 경우(404), 관리자가 아닌 경우(403).
    """
    batch = await _load_batch(db, batch_id, user)
    project = await db.get(Project, batch.project_id)
    videos = await _batch_videos(db, batch_id, batch.project_id)
    rows = [
        meta_sheet.TemplateRow(
            video_id=str(v.video_id), file=v.file, title=v.title,
            broadcaster=v.broadcaster, program_name=v.program_name, genre=v.genre,
            episode=v.episode, aired_at=v.aired_at,
        )
        for v in videos
    ]
    # 양식 생성은 CPU 작업이라 이벤트 루프 밖에서 돌린다. 수천 행이면 1초 가까이 걸린다.
    content = await anyio.to_thread.run_sync(meta_sheet.build_template, rows)
    slug = project.slug if project else "project"
    return content, f"{slug}-batch-{str(batch_id)[:8]}-meta.xlsx"


def _field_error(err: dict) -> str:
    """`VideoUpdateRequest` 검증 오류 하나를 화면에 표시할 문장으로 바꾼다."""
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "string_too_long":
        return f"최대 {ctx.get('max_length')}자까지 넣습니다"
    if kind == "value_error":
        return str(err.get("msg", "")).removeprefix("Value error, ")
    return "값이 올바르지 않습니다"


def _validate(cells: dict[str, object]) -> tuple[dict[str, object], list[BulkMetaRowError]]:
    """행의 바꾸는 열을 영상 메타 편집과 같은 규칙으로 검사한다. 빈 셀은 건너뛴다.

    필드마다 따로 검사한다. 한 필드의 오류가 같은 행의 다른 필드 결과를 가리지 않는다.

    Returns:
        tuple: 검사를 통과한 필드 값(방영일은 KST 자정 시각)과 오류 목록.
    """
    values: dict[str, object] = {}
    errors: list[BulkMetaRowError] = []
    for key in FIELD_KEYS:
        if key not in cells:
            continue
        raw = cells[key]
        if isinstance(raw, Formula):
            errors.append(BulkMetaRowError(field=key, message="수식 대신 값을 넣습니다"))
            continue
        if key == "aired_at":
            try:
                value: object = meta_sheet.parse_aired(raw)
            except ValueError as e:
                errors.append(BulkMetaRowError(field=key, message=str(e)))
                continue
        else:
            value = cell_text(raw)
            if value is None:
                continue
        try:
            checked = VideoUpdateRequest.model_validate({key: value})
        except ValidationError as e:
            errors.extend(
                BulkMetaRowError(field=key, message=_field_error(err)) for err in e.errors()
            )
            continue
        values[key] = getattr(checked, key)
    return values, errors


def _changes(video: _Video, values: dict[str, object]) -> list[BulkMetaFieldChange]:
    """검사를 통과한 값 중 지금 값과 다른 필드를 고른다. 방영일은 KST 날짜로 비교한다."""
    out: list[BulkMetaFieldChange] = []
    for key, new in values.items():
        before = getattr(video, key)
        if key == "aired_at":
            if _kst_day(before) == _kst_day(new):
                continue
            out.append(BulkMetaFieldChange(
                field=key, before=before.isoformat() if before else None, after=new.isoformat(),
            ))
        elif before != new:
            out.append(BulkMetaFieldChange(field=key, before=before, after=new))
    return out


class _Index:
    """묶음 영상을 영상 ID·파일·제목으로 찾는 색인."""

    def __init__(self, videos: list[_Video]) -> None:
        """영상 목록으로 세 색인을 만든다.

        파일 색인에는 원래 파일 이름과 저장소 경로의 모든 뒷부분
        (`a/b/c.mp4`, `b/c.mp4`, `c.mp4`)을 넣는다. 묶음 화면이 경로의 뒤 두 단계만 보여 주므로
        화면에 보이는 값을 그대로 적어도 찾게 한다.
        """
        self.by_id = {v.video_id: v for v in videos}
        self.by_file: dict[str, list[_Video]] = defaultdict(list)
        self.by_title: dict[str, list[_Video]] = defaultdict(list)
        for v in videos:
            parts = v.key.split("/") if v.key else []
            names = {v.original_filename, *("/".join(parts[i:]) for i in range(len(parts)))}
            for n in {_nfc(n) for n in names if n}:
                self.by_file[n].append(v)
            self.by_title[_nfc(v.title)].append(v)

    def match(
        self, cells: dict[str, object],
    ) -> tuple[_Video | None, MatchedBy | None, str, BulkMetaRowError | None]:
        """행이 가리키는 영상을 찾는다.

        영상 ID가 있으면 영상 ID로만 찾는다.
        잘못된 ID를 파일·제목으로 다시 찾으면 다른 영상에 값이 들어간다.
        영상 ID가 없으면 파일, 파일도 없으면 제목으로 찾는다. 여러 영상과 같으면 고르지 않는다.

        Returns:
            tuple: 찾은 영상, 찾은 열, 판정(`matched`·`unmatched`·`ambiguous`), 찾지 못한 이유.
        """
        if "video_id" in cells:
            text = cell_text(cells["video_id"]) or ""
            try:
                vid = UUID(text)
            except ValueError:
                return None, None, "unmatched", BulkMetaRowError(
                    message="영상 ID 형식이 아닙니다. 양식의 영상 ID를 그대로 둡니다",
                )
            v = self.by_id.get(vid)
            if v is None:
                return None, None, "unmatched", BulkMetaRowError(
                    message="이 묶음에 없는 영상 ID입니다",
                )
            return v, "video_id", "matched", None
        for by, index, name in (("file", self.by_file, "파일"), ("title", self.by_title, "제목")):
            text = cell_text(cells.get(by))
            if not text:
                continue
            found = index.get(_nfc(text), [])
            if len(found) == 1:
                return found[0], by, "matched", None
            if not found:
                return None, None, "unmatched", BulkMetaRowError(
                    message=f"이 묶음에 같은 {name}인 영상이 없습니다",
                )
            return None, None, "ambiguous", BulkMetaRowError(
                message=f"같은 {name}인 영상이 {len(found):,}개입니다. 영상 ID를 넣습니다",
            )
        return None, None, "unmatched", BulkMetaRowError(
            message="영상 ID·파일·제목이 모두 비어 있습니다",
        )


async def preview(
    db: AsyncSession, batch_id: UUID, user: User, data: bytes, filename: str | None,
) -> BulkMetaPreview:
    """올린 파일의 행마다 찾은 영상, 바뀌는 값, 오류를 판정한다. 아무것도 저장하지 않는다.

    빈 셀은 바꾸지 않는다. 값을 지우는 일은 영상 상세의 메타 편집에서 한다.
    같은 영상을 가리키는 행이 여럿이면 어느 행의 값이 남을지 행 순서에 맡기지 않고
    모두 오류로 표시한다.

    Raises:
        HTTPException: 묶음이 없는 경우(404), 관리자가 아닌 경우(403),
            파일을 읽지 못하는 경우(413·422, `meta_sheet.read_table`).
    """
    batch = await _load_batch(db, batch_id, user)
    table = await anyio.to_thread.run_sync(meta_sheet.read_table, data, filename)
    videos = await _batch_videos(db, batch_id, batch.project_id)
    index = _Index(videos)

    rows: list[BulkMetaPreviewRow] = []
    for row_no, cells in table.rows:
        video, by, verdict, miss = index.match(cells)
        values, errors = _validate(cells)
        if video is None:
            rows.append(BulkMetaPreviewRow(
                row=row_no, status=verdict, title=cell_text(cells.get("title")),
                file=cell_text(cells.get("file")), errors=[miss, *errors] if miss else errors,
            ))
            continue
        changes = _changes(video, values)
        rows.append(BulkMetaPreviewRow(
            row=row_no,
            status="invalid" if errors else ("changed" if changes else "unchanged"),
            video_id=video.video_id, title=video.title, file=cell_text(cells.get("file")),
            matched_by=by, changes=changes, errors=errors,
        ))

    by_video: dict[UUID, list[BulkMetaPreviewRow]] = defaultdict(list)
    for r in rows:
        if r.video_id is not None:
            by_video[r.video_id].append(r)
    for same in by_video.values():
        if len(same) < 2:
            continue
        where = "·".join(str(r.row) for r in same)
        for r in same:
            r.status = "invalid"
            r.errors.append(BulkMetaRowError(
                message=f"같은 영상을 가리키는 행이 여럿입니다({where}행)",
            ))

    counts = BulkMetaPreviewCounts()
    for r in rows:
        setattr(counts, r.status, getattr(counts, r.status) + 1)
    return BulkMetaPreview(
        sheet=table.sheet, columns=table.columns, ignored_columns=table.ignored,
        batch_video_count=len(videos), matched_video_count=len(by_video),
        counts=counts, rows=rows,
    )


async def apply(
    db: AsyncSession, batch_id: UUID, payload: BulkMetaApplyRequest, user: User,
) -> BulkMetaApplyResult:
    """확인한 변경을 한 트랜잭션으로 저장한다. 하나라도 실패하면 아무것도 바꾸지 않는다.

    값은 요청 모델(`VideoUpdateRequest`)이 영상 메타 편집과 같은 규칙으로 다시 검사한다.
    영상마다 `video.updated`를 남기고 `via`로 일괄 입력임을 표시한다. 묶음에는
    `ingest_batch.meta_imported` 한 건을 남긴다.
    방영일은 KST 날짜가 같으면 바꾸지 않는다. 미리 보기와 같은 비교다.

    Raises:
        HTTPException: 묶음이 없는 경우(404), 관리자가 아닌 경우(403),
            요청의 영상 중 이 묶음에 없거나 삭제된 영상이 있는 경우
            (409 `meta_import_video_not_in_batch`).
    """
    batch = await _load_batch(db, batch_id, user)
    ids = [i.video_id for i in payload.items]
    # 영상 ID 순서로 행을 잠근다.
    # 같은 영상을 동시에 고치는 두 요청이 서로의 잠금을 기다리는 교착 상태를 막는다.
    found = (await db.scalars(
        select(Video)
        .where(
            Video.video_id.in_(ids), Video.project_id == batch.project_id,
            Video.upload_batch_id == batch_id, Video.deleted_at.is_(None),
        )
        .order_by(Video.video_id)
        .with_for_update()
    )).all()
    if len(found) != len(ids):
        raise HTTPException(status.HTTP_409_CONFLICT, "meta_import_video_not_in_batch")
    by_id = {v.video_id: v for v in found}

    updated = unchanged = changed_fields = 0
    for item in payload.items:
        video = by_id[item.video_id]
        fields = item.fields
        sent = set(fields.model_fields_set)
        if "aired_at" in sent and _kst_day(video.aired_at) == _kst_day(fields.aired_at):
            sent.discard("aired_at")
        changed = apply_meta(
            video, VideoUpdateRequest.model_validate({k: getattr(fields, k) for k in sent}),
        )
        if not changed:
            unchanged += 1
            continue
        updated += 1
        changed_fields += len(changed)
        audit.emit(
            db, event_type="video.updated", target_kind="video",
            target_id=video.video_id, actor_user_id=user.user_id, project_id=video.project_id,
            payload={"changed": changed, "via": VIA_BULK_IMPORT, "batch_id": str(batch_id)},
        )
    if updated:
        audit.emit(
            db, event_type="ingest_batch.meta_imported", target_kind="ingest_batch",
            target_id=batch_id, actor_user_id=user.user_id, project_id=batch.project_id,
            payload={"videos": updated, "changed_fields": changed_fields},
        )
    await db.commit()
    return BulkMetaApplyResult(updated=updated, unchanged=unchanged, changed_fields=changed_fields)
