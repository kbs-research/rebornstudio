"""납품 패키지 생성. 승인분을 표준 디렉터리 구조의 zip 하나로 묶는다.

패키지 구성은 다음과 같다.

    metadata/{clip_id}.json    클립별 메타(`ClipMetadata` 스키마)
    manifest.jsonl             1행 1클립 색인과 체크섬
    sources.csv                1행 1원본 영상 목록(원본 파일을 찾는 참고 정보)
    schema/clip-metadata-v1.json
    dataset_card.yaml
    README.md
    report.json                포함·제외 집계

받는 쪽이 원본을 이미 갖고 있고 비식별화한 영상은 납품 범위가 아니어서 `media/`는 넣지 않는다.
클립은 `source_video_id`와 원본 기준 시각으로 원본 구간을 가리킨다.
패키지는 수십 MB에 그치고 브라우저로 바로 받을 수 있다.
`sources.csv`는 `source_video_id`마다 제목·원래 파일 이름·버킷 키·원본 체크섬을 싣는다.
메타 JSON만 받은 쪽이 플랫폼 DB 없이 원본 파일을 찾을 수 있게 한다(`sources_csv`).
클립 메타의 `deidentification`은 라벨링에 쓴 작업 사본에 적용한 비식별화를 적는다
(`deidentification_of`). 원본 파일에는 얼굴 가림이 없다.

라벨러가 채우는 내용은 캡션 하나다. 세부 묘사(`description`)와 키워드(`keywords`)는 납품
형식을 유지하려고 키를 두지만 값은 늘 비어 있다.

선택 항목 제외(`omit_optional`)를 켜면 메타 파일에서 값이 늘 비어 있는 선택 항목 키
(`OPTIONAL_LABEL_FIELDS`)를 뺀다. 검증은 전체 모델로 하고 파일을 쓸 때만 키를 뺀다.
그래서 납품 대상과 제외는 옵션과 무관하게 같다.

싣지 못하는 항목은 버리지 않고 사유와 함께 `report.json`의 제외 목록에 남긴다.

후보 쿼리에서 빠진 건도 제외로 센다. 캡션 품질검수가 보류한 승인분은 납품 후보
쿼리(`export_service.approved_rows_stmt`)에서 빠져 `write_package`의 행 루프를 거치지 않는다.
그래서 부르는 쪽이 `qc_held`로 넘기고 리포트에 싣는다. 싣지 않으면 일부만 담긴 패키지가
`excluded: 0`으로 표시된다. 패키지만 받는 쪽은 `report.json`으로만 보류 건을 알 수 있다.

분석이 트랜스코딩 마스터를 읽었으면 시간 축이 원본과 다르다.
예를 들어 원본 1.500s가 마스터에서 0.033s가 된다. 이 경우 등록 때 잰
`probe.start_time_seconds`를 더해 원본 축으로 환산한다. 이 값이 없는 클립은 환산할 수 없어
제외한다. 어긋난 시각을 그대로 내보내지 않는다.

제외 사유는 화면에서 쓰는 이름으로 쓴다. 사유 한 줄은 `{항목 이름}: {사유}` 형태다.
예: `캡션: 10자 이상 필요`, `비식별화 신고: 처리 대기`, `타임코드: 원본 축 보정값 없음`.
검수자는 스키마 키(`rights_status`)와 화면 필드의 대응을 알 수 없으므로 사유에 키를 쓰지 않는다.
이름은 `FIELD_LABEL` 한 곳에서 정한다. 완료 목록·상세·통계 힌트 세 화면이 같은
문자열을 받는다. 화면마다 사전을 두면 화면마다 이름이 달라진다. 스키마 키는 `report.json`
제외 항목의 `fields`에 따로 싣는다. 받는 쪽은 이 키로 스키마와 대응시킨다.

무결성을 검사한다. zip을 닫기 전에 색인 줄 수와 `metadata/` 파일 수를 대조하고,
닫은 뒤 다시 열어 색인이 가리키는 파일이 있는지와 체크섬을 확인한다.
어긋나면 `PackageIntegrityError`를 던진다. 내보내기는 실패로 끝나고 그 패키지는 올리지 않는다.
같은 루프가 색인과 파일을 함께 써서 지금 구조로는 어긋나지 않지만, 납품 규격이 이 확인을
요구한다.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import zipfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import xxhash
import yaml
from pydantic import ValidationError
from reborn_contracts import RIGHTS_CLEARED
from reborn_contracts.clip_metadata import (
    ClipDeidentification,
    ClipDescription,
    ClipMetadata,
    ClipResolution,
    ClipReview,
    json_schema,
)
from reborn_contracts.video import ORIGINAL_FILENAME_KEY

from api_gateway.models import Clip, Video, WorkItem
from api_gateway.services import caption, csv_cell, operating_time, video_meta

log = logging.getLogger(__name__)

# 납품에 실을 수 있는 클립 상태(`rights_status`). 정상 상태만 싣는다.
# `legal_review`(비식별화 미흡 신고)는 신고 처리가 끝나지 않은 상태다.
# `rejected`(제외)는 신고 처리에서 납품하지 않기로 한 상태다.
# 라벨이 승인돼도 정상 상태가 아닌 클립은 싣지 않는다.
_RIGHTS_OK = RIGHTS_CLEARED  # 티켓 생성과 같은 계약 상수를 기준으로 쓴다
# 상태 값별 사유. 이름 부분(`비식별화 신고`)은 사유를 조립할 때 붙이므로 여기에는 값만 둔다.
# 값에 이름을 넣으면 `비식별화 신고: 비식별화 신고 제외`처럼 이름이 두 번 나온다.
# 이름은 스키마 필드 사유면 `blocker`가, 관문 사유면 `_gate_blocker`가 붙인다.
# 괄호 설명을 붙이지 않는다.
_RIGHTS_LABEL = {
    "legal_review": "처리 대기",
    "rejected": "제외",
}

#: 납품 스키마 필드 키별 화면 이름. 사유 문자열의 앞부분이 된다.
#: 이름은 워크벤치 입력란, 완료 상세의 클립 카드, QC 규칙의 필수 메타에서 이미 쓰는 말로 고른다.
#: 중첩 키는 점으로 잇는다(`description.location`). 목록 원소(`speakers.0.start_time`)는 번호를
#: 떼고 찾고, 없으면 부모 이름(`화자 구간`)을 쓴다. 고칠 사람에게는 묶음 이름이면 충분하다.
#: 주의: `ClipMetadata`에 필드를 더하면 여기에도 더한다. 빠지면 키 원문이 화면에 나간다
#: (`test_every_schema_field_has_a_screen_name`이 잡아낸다).
FIELD_LABEL: dict[str, str] = {
    "schema_version": "스키마 버전",
    "clip_id": "클립 식별자",
    "source_video_id": "원본 영상 식별자",
    "start_time": "시작 시각",
    "end_time": "끝 시각",
    "duration": "구간 길이",
    "source_start_offset": "축 보정",
    "broadcaster": "방송사",
    "program_name": "프로그램명",
    "episode": "회차",
    "genre": "장르",
    "aired_at": "방영일",
    "source_duration": "원본 길이",
    "source_checksum": "원본 체크섬",
    "caption": "캡션",
    "description": "세부 묘사",
    "description.persons": "인물",
    "description.location": "장소",
    "description.actions": "동작",
    "description.events": "사건",
    "description.on_screen_text": "화면 텍스트",
    "keywords": "키워드",
    "speakers": "화자 구간",
    "transcript": "음성 인식",
    "scene_summary": "장면 요약",
    "deidentification": "비식별화",
    "review": "검수",
    "resolution": "해상도",
    "fps": "프레임률",
}

#: 스키마 밖의 두 관문 키. 비식별화 신고(클립 상태)와 타임코드(원본 축 환산)다.
#: 비식별화 신고 관문의 이름은 스키마 필드 `deidentification`의 이름(`비식별화`)과 다르다.
#: 사유별 집계 키가 겹치지 않아야 신고로 빠진 건과 비식별화 필드 오류를 따로 센다.
#: `report.json`의 `fields`에 실리는 키도 이 값이다. 타임코드 키는 환산하지 못한 값
#: (`source_start_offset`)을 가리킨다. 납품을 막는 대상은 보정값이 아닌 시각이므로 화면 이름은
#: `축 보정` 대신 `타임코드`로 쓴다.
RIGHTS_FIELD = "rights_status"
OFFSET_FIELD = "source_start_offset"
_GATE_LABEL = {RIGHTS_FIELD: "비식별화 신고", OFFSET_FIELD: "타임코드"}

#: 캡션 품질검수가 보류한 승인분의 사유 한 줄.
#: 두 관문(비식별화 신고·타임코드)과 달리 보류분은 후보 쿼리에서 이미 빠져
#: `check_row`를 거치지 않는다. 그래서 부르는 쪽이 `qc_held`로 넘긴다.
#: 후보 쿼리에 보류분을 포함하는 옵션을 두면 옵션이 켜진 채 호출될 때 보류분이 납품된다.
#: 문구는 화면 용어(`품질검수`, `보류`)를 그대로 쓴다.
QC_HELD_REASON = "품질검수: 보류"

#: `report.json`의 제외 목록에 싣는 최대 건수. 파일 크기를 제한한다. 넘으면 잘렸다고 적는다.
EXCLUSIONS_LISTED = 1000


class PackageIntegrityError(RuntimeError):
    """색인과 파일이 어긋난 패키지에서 던지는 예외. 이 패키지는 올리지 않는다."""


#: 선택 항목 제외(`omit_optional`)가 메타 파일에서 빼는 최상위 키.
#: 모두 값이 늘 비어 있는 선택 항목이다. 스키마의 `required`에 없어 키가 없어도 검증을 통과한다.
#: 키를 더하거나 빼려면 이 집합만 고친다. 리포트·카드·README가 이 집합을 읽는다.
#: 주의: 시각(`start_time`·`end_time`·`duration`)과 `deidentification`은 넣지 않는다.
#: 시각은 색인과 대조하는 값이다. 비식별화는 선택 항목이 아닌 처리 기록이다.
OPTIONAL_LABEL_FIELDS: frozenset[str] = frozenset(
    {"description", "keywords", "speakers", "transcript", "scene_summary"}
)


#: `sources.csv`의 열. 받는 쪽 스크립트가 열 이름으로 읽으므로 순서와 이름을 바꾸지 않는다.
#: 메타 JSON과 같은 값을 싣는 열(`source_checksum`·`source_duration`·방송 메타 다섯 가지)은
#: 같은 키 이름을 쓴다. 받는 쪽은 `source_video_id`로 두 파일을 잇는다.
SOURCES_COLUMNS: tuple[str, ...] = (
    "source_video_id", "title", "original_filename", "storage_key", "source_checksum",
    "source_duration", "broadcaster", "program_name", "episode", "genre", "aired_at",
    "clip_count",
)
#: 메타 JSON에서 그대로 옮겨 싣는 열. 값의 형식(방영일의 ISO 8601 등)이 클립 메타와 같아진다.
_SOURCES_FROM_META = (
    "source_checksum", "source_duration", "broadcaster", "program_name", "episode", "genre",
    "aired_at",
)


def source_row(v: Video, doc: dict) -> dict[str, Any]:
    """원본 영상 하나의 `sources.csv` 행을 만든다. `clip_count`는 부르는 쪽이 센다.

    원래 파일 이름은 등록 때 저장한 값(`ORIGINAL_FILENAME_KEY`)을 쓴다. 이 값이 없는 버킷 등록
    영상은 버킷 키의 마지막 부분을 쓴다. 둘 다 없으면 빈 값이다.
    `storage_key`는 버킷 등록 영상의 키(`extra["key"]`)만 싣는다. 브라우저 업로드 영상의 키
    (`intake/{id}/source`)는 플랫폼 내부 경로라 받는 쪽이 찾아갈 수 없고, 준비가 끝나면 옮겨지거나
    지워진다(`keys._upload_key`).

    Args:
        v: 원본 영상.
        doc: 그 영상의 클립 하나를 파일에 쓴 메타(선택 항목을 뺀 뒤의 dict). 방송 메타·체크섬·길이는
            이 값에서 읽어 클립 메타와 같은 값과 형식을 싣는다.
    """
    extra = v.extra if isinstance(v.extra, dict) else {}
    key = extra.get("key") if isinstance(extra.get("key"), str) else ""
    stored = extra.get(ORIGINAL_FILENAME_KEY)
    original = stored if isinstance(stored, str) and stored else key.rsplit("/", 1)[-1]
    row: dict[str, Any] = {
        "source_video_id": str(v.video_id),
        "title": v.title or "",
        "original_filename": original,
        "storage_key": key,
    }
    for col in _SOURCES_FROM_META:
        got = doc.get(col)
        row[col] = "" if got is None else got
    row["clip_count"] = 0
    return row


def sources_csv(rows: Sequence[dict[str, Any]]) -> bytes:
    """`sources.csv`의 바이트를 만든다. UTF-8 BOM으로 시작하고 칸마다 수식 시작 글자를 막는다.

    엑셀은 BOM이 없는 UTF-8을 지역 인코딩으로 읽어 한글 제목이 깨진다. 감사 로그 CSV
    (`api/v1/admin.py`)와 같은 방식이다. 제목과 파일 이름은 사람이 정한 값이라
    `=HYPERLINK(...)`로 시작하면 파일을 연 사람의 엑셀이 수식으로 실행한다(`csv_cell.safe_cell`).
    줄 끝은 `csv` 모듈 기본값인 CRLF(RFC 4180)다.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(SOURCES_COLUMNS)
    for r in rows:
        w.writerow([csv_cell.safe_cell(r[c]) for c in SOURCES_COLUMNS])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")



#: 비식별화 처리로 세지 않는 모델. 검증용 복사 어댑터라 얼굴을 가리지 않는다.
_NOT_MASKING_MODELS = frozenset({"passthrough"})

#: 가린 영역이 있는 영상의 `deidentification.method`.
#: 내장 비식별화 모듈(`essential-yunet`)은 얼굴을 모자이크로 가린다.
#: 완료 보고에는 방식이 없으므로 이 값을 쓴다. 가린 영역이 없으면 `none`을 쓴다(`deident_masked`).
#: 주의: 비식별화 모듈의 처리 방식을 바꾸면 이 값도 바꾼다.
_DEIDENT_METHOD = "mosaic"


def _xxh64(data: bytes) -> str:
    """색인용 체크섬을 만든다. 항상 `xxh64:` 접두어를 붙인다.

    납품 규격이 색인 체크섬을 `xxh64`로 정한다.
    `xxhash`는 `api-gateway`가 직접 선언한 의존성이라 없으면 기동 단계에서 실패한다.
    받는 쪽 검증기가 `xxh64`만 검증하므로 다른 알고리즘으로 폴백하지 않는다.
    """
    return f"xxh64:{xxhash.xxh64(data).hexdigest()}"


def field_label(path: str) -> str:
    """스키마 필드 경로를 화면 이름으로 바꾼다. 번호를 떼고 찾고, 없으면 부모 이름을 쓴다.

    부모까지 이름이 없으면 키 원문을 돌려준다. 숨기면 새 필드의 사유가 표시되지 않는다.
    이름이 빠진 필드는 `test_every_schema_field_has_a_screen_name`이 먼저 잡아낸다.
    """
    parts = [p for p in path.split(".") if not p.isdecimal()]
    while parts:
        hit = FIELD_LABEL.get(".".join(parts))
        if hit:
            return hit
        parts.pop()
    return path


def blocker(field: str, reason: str) -> str:
    """사유 한 줄(`{화면 이름}: {사유}`)을 만든다. 완료 목록·상세·QC·통계가 같은 문자열을 받는다."""
    return f"{field_label(field)}: {reason}"


def _gate_blocker(field: str, reason: str) -> str:
    """스키마 밖 관문(비식별화 신고·타임코드)의 사유 한 줄을 만든다. 이름은 `_GATE_LABEL`에 있다.

    `source_start_offset`은 스키마 필드이기도 하다(`축 보정`). 사전을 하나로 합치면 관문 사유가
    `축 보정: …`이 된다. 납품을 막는 대상은 보정값이 아닌 시각이므로 관문 이름을 따로 둔다.
    """
    return f"{_GATE_LABEL[field]}: {reason}"


def blocker_label(line: str) -> str:
    """사유 한 줄에서 사유별 집계의 키인 이름 부분을 꺼낸다.

    통계 힌트(`dashboard._blocker_reasons`)도 같은 규칙(첫 `:` 앞)으로 센다.
    """
    return line.split(":", 1)[0].strip()


def rights_blocker(c: Clip | None) -> str | None:
    """비식별화 신고 때문에 납품이 막히는 이유를 돌려준다. 막히지 않으면 None을 돌려준다.

    클립 상태 관문 판정은 이 함수 한 곳에만 둔다.
    클립이 없는 영상 단위 작업은 통과시킨다. 클립 상태가 없고, 영상 단위 게이트가
    앞에서 이미 걸렀다. `_RIGHTS_LABEL`에 없는 상태 값은 원문(enum) 대신 `미정리`로 표시한다.
    정상 값(`RIGHTS_CLEARED`)이 아니면 모두 막는다.
    """
    if c is None or c.rights_status in _RIGHTS_OK:
        return None
    return _gate_blocker(RIGHTS_FIELD, _RIGHTS_LABEL.get(c.rights_status, "미정리"))


def offset_of(v: Video) -> tuple[float, str | None]:
    """원본 축으로 환산할 오프셋을 (값, 구하지 못한 이유)로 돌려준다.

    `analysis_uri`가 없으면 분석이 원본을 그대로 읽었으므로 오프셋은 0.0이다.
    있으면 등록 때 잰 값이 필요하다. 그 값이 없으면 환산할 수 없다.
    """
    if not v.analysis_uri:
        return 0.0, None
    probe = (v.extra or {}).get("probe") or {}
    off = probe.get("start_time_seconds")
    if off is None:
        # 분석본을 읽었는데 등록 때 잰 원본 시작 시각이 없어 환산할 값이 없다.
        # 사유 문구는 화면의 `축 보정` 필드 용어를 쓴다. 컬럼명이나 괄호 설명은 넣지 않는다.
        return 0.0, "원본 축 보정값 없음"
    return float(off), None


def deident_report(v: Video) -> dict:
    """영상의 비식별화 보고(`extra["deident"]`)를 돌려준다. 없거나 dict가 아니면 빈 dict다."""
    d = (v.extra or {}).get("deident")
    return d if isinstance(d, dict) else {}


def deident_processed(v: Video) -> bool:
    """영상의 작업 사본이 비식별화됐으면 True를 돌려준다.

    비식별화가 끝났고(`done`) 얼굴을 가리는 모델이 처리한 경우다. 검증용 복사 어댑터
    (`passthrough`)는 끝나도 얼굴을 가리지 않으므로 처리로 세지 않는다.
    가릴 얼굴이 없어 `masked`가 거짓인 영상도 처리한 영상으로 센다. 모델이 영상을 끝까지
    처리했지만 가릴 얼굴을 찾지 못한 경우다. 이때는 `review_needed`가 참이라 `suspected_miss`로
    표시되고, 가린 영역이 없으므로 방식은 `none`이다(`deident_masked`).
    클립 메타와 `report.json`의 비식별화 집계가 이 판정 하나를 쓴다.
    """
    return v.deident_status == "done" and deident_report(v).get("model") not in _NOT_MASKING_MODELS


def deident_masked(v: Video) -> bool:
    """비식별화한 영상의 작업 사본에 가린 영역이 하나라도 있으면 True를 돌려준다.

    비식별화 모듈은 가린 영역이 있는지를 보고의 `masked`로 남긴다. 얼굴을 찾지 못한 영상은
    `masked`가 거짓이고 작업 사본이 원본과 같다. 이 영상을 `mosaic`로 적으면 가리지 않은
    영상을 가렸다고 납품하게 된다.
    `masked` 키가 없는 보고는 가린 영역이 있는 것으로 본다. 비식별화 모듈은 얼굴을 찾지 못한
    결과를 `masked: false`로 보고한다.
    클립 메타의 방식과 `report.json`의 가린 영상 수가 이 판정 하나를 쓴다.
    """
    return deident_processed(v) and deident_report(v).get("masked") is not False


def deidentification_of(v: Video) -> ClipDeidentification:
    """클립 하나의 비식별화 정보를 만든다. 라벨링에 쓴 작업 사본 기준이다.

    처리하지 않은 영상은 기본값(`processed: false` · `none`)이다.
    처리했지만 가린 영역이 없는 영상은 `processed: true` · `none`이다.
    """
    if not deident_processed(v):
        return ClipDeidentification()
    return ClipDeidentification(
        processed=True,
        method=_DEIDENT_METHOD if deident_masked(v) else "none",
        suspected_miss=bool(deident_report(v).get("review_needed")),
    )


def _operating_time(at: datetime | None) -> datetime | None:
    """시각을 운영 시간대 오프셋(+09:00)으로 바꾼다. 같은 순간이고 형식은 ISO 8601 그대로다.

    방영일은 운영 시간대의 자정으로 저장된다. UTC로 내보내면 `2026-06-30T15:00:00Z`가 되어
    날짜 부분만 읽는 받는 쪽이 하루 앞의 날짜로 읽는다.
    """
    return at.astimezone(operating_time.OPERATING_TZ) if at is not None else None


def build_metadata(
    w: WorkItem, v: Video, c: Clip | None, approved_at, offset: float,
    revision_count: int = 0,
) -> ClipMetadata:
    """행 하나를 납품 메타로 만든다. 검증은 pydantic이 한다(스키마와 같은 출처).

    방송사·프로그램명·장르·방영일은 클립이 물려받는 영상의 값을 읽는다(`services/video_meta`).
    라벨러 입력(`payload.meta`)은 읽지 않는다.
    `revision_count`는 부르는 쪽이 판정 기록에서 센 값이다(`export_service._revision_map`).
    세부 묘사와 키워드는 입력란이 없어 늘 빈 값으로 싣는다. 이전 payload에 남은 값도 읽지 않는다.
    """
    payload = w.payload if isinstance(w.payload, dict) else {}
    inherited = video_meta.of_video(v)
    # 캡션은 제출 게이트와 같은 함수로 읽는다. `brief`만 읽으면 제출을 통과한 문장 배열
    # payload가 "캡션 없음 · 납품 불가"로 판정된다.
    brief = caption.final_text(payload)
    probe = (v.extra or {}).get("probe") or {}

    # 시각은 밀리초(소수 3자리)로 싣는다. float 덧셈·뺄셈은 `32.89999999999998`
    # 같은 오차를 남기므로 반올림한다. 길이는 반올림한 두 시각의 차로 구해야
    # 길이 검사(`duration == end_time - start_time`)가 받는 쪽 계산과 1ms 안에서 맞는다.
    raw_start = float(c.start_seconds) if c and c.start_seconds is not None else 0.0
    raw_end = float(c.end_seconds) if c and c.end_seconds is not None else 0.0
    start = round(raw_start + offset, 3)
    end = round(raw_end + offset, 3)

    return ClipMetadata(
        clip_id=c.clip_id if c else w.work_item_id,   # 클립 없는 영상 단위 작업의 식별자
        source_video_id=v.video_id,
        start_time=start,
        end_time=end,
        duration=round(max(0.0, end - start), 3),
        source_start_offset=offset,
        broadcaster=inherited.broadcaster,
        program_name=inherited.program_name,
        episode=v.episode,
        genre=inherited.genre,
        aired_at=_operating_time(inherited.aired_at),
        source_duration=v.duration_seconds,
        source_checksum=v.source_sha256,
        caption=brief or "",
        description=ClipDescription(),
        keywords=[],
        # 화자 구간은 싣지 않는다. 계약은 사람이 검토·편집한 값만 싣는데 워크벤치에는
        # 구간(시각 범위) 편집이 없다. MI 원본은 스택을 바꾸면 모양이 달라져 계약이 될 수 없다.
        # 납품 규격에서도 선택 항목이라 비어도 통과한다.
        speakers=[],
        # `scene_summary`·`transcript`는 비운다. MI 원본 결과는 스택마다 모양이 달라 계약이
        # 될 수 없다. 납품 규격에서 선택 항목이라 비어도 통과한다.
        scene_summary=None,
        deidentification=deidentification_of(v),
        # `reviewer_id`는 의도적으로 비운다. 내부 사용자 ID는 개인정보 성격이다.
        # 받는 쪽은 그 값을 쓸 곳이 없다. 승인자는 감사 로그와 `work_item` 이력에 남는다.
        # 계약이 `UUID | None`이라 빈 값도 검증을 통과한다.
        review=(
            ClipReview(reviewed_at=approved_at, revision_count=revision_count)
            if approved_at else None
        ),
        resolution=(
            ClipResolution(width=probe["width"], height=probe["height"])
            if probe.get("width") and probe.get("height")
            else None
        ),
        fps=probe.get("fps"),
    )


# pydantic 오류 종류를 한국어 사유로 바꾼다. `err["msg"]`는 영어("String should have at least
# 10 characters")라 그대로 쓰면 완료 상세의 빨간 배너에 영어가 표시된다.
# 사유는 이름 부분 뒤에 붙고 목록 행·통계 힌트처럼 좁은 곳에 한 줄로 표시된다. 그래서 명사구로
# 쓴다(`10자 이상 필요`). 필드 키는 넣지 않는다. 앞부분에 화면 이름이 붙고, 스키마 키는
# `report.json`의 `fields`에 따로 실린다.
def _msg_of(err: dict) -> str:
    """pydantic 오류 하나를 한국어 한 줄로 바꾼다.

    번역하지 않은 종류는 `형식 오류`로 표시하고 영문 원문은 운영 로그에 남긴다.
    사전을 갱신해야 하는지는 이 로그로만 알 수 있다.
    """
    ctx = err.get("ctx") or {}
    kind = err["type"]
    match kind:
        case "missing":
            return "값 없음"
        case "string_too_short":
            # 빈 값은 `missing`과 같은 사유(`값 없음`)로 표시한다.
            # 그러지 않으면 캡션이 아예 없는 행이 `캡션: 10자 이상 필요`로 표시된다.
            if not str(err.get("input") or "").strip():
                return "값 없음"
            return f"{ctx.get('min_length')}자 이상 필요"
        case "string_too_long":
            return f"{ctx.get('max_length')}자 이하 필요"
        case "greater_than":
            return f"{ctx.get('gt')}보다 큰 값 필요"
        case "greater_than_equal":
            return f"{ctx.get('ge')} 이상 필요"
        case "less_than":
            return f"{ctx.get('lt')}보다 작은 값 필요"
        case "less_than_equal":
            return f"{ctx.get('le')} 이하 필요"
        case "extra_forbidden":
            # 조립 코드가 스키마에 없는 이름을 넘긴 경우다(`ClipMetadata`의 docstring 참고).
            return "정의되지 않은 항목"
        case _ if kind.endswith(("_type", "_parsing")) or kind in ("literal_error", "enum"):
            return "형식 오류"
        case _:
            log.warning(
                "납품 검증 사유 미번역 type=%s loc=%s msg=%s", kind, err.get("loc"), err.get("msg"),
            )
            return "형식 오류"


def _path_of(err: dict) -> str:
    """오류 위치를 점으로 이은 필드 경로(`description.location`)로 바꾼다."""
    return ".".join(str(x) for x in err["loc"])


def reasons_of(e: ValidationError) -> list[str]:
    """검증 실패 사유 목록을 만든다. 고칠 수 있도록 어느 항목이 왜 실패했는지 쓴다."""
    return [blocker(_path_of(err), _msg_of(err)) for err in e.errors()]


def check_row(
    w: WorkItem, v: Video, c: Clip | None, approved_at
) -> tuple[ClipMetadata | None, list[str]]:
    """행이 패키지에 실리는지 판정한다. 납품 관문의 기준 함수다.

    통과하면 `(메타, [])`, 막히면 `(None, 사유들)`을 돌려준다.
    사유 옆에 스키마 키가 필요한 곳(`report.json`)은 `check_row_detail`을 부른다.
    같은 판정이고 돌려주는 모양만 다르다.

    `write_package`(`check_row_detail` 경유)와 완료 화면이 같은 판정을 쓴다. 관문을 각자
    부르면 하나만 빠뜨려도 화면에 보인 것과 실제로 나가는 것이 달라진다. 예를 들어 화면 쪽이
    `rights_blocker`를 부르지 않으면 신고로 멈췄거나 제외된 클립을 "납품 대상"이라고 표시한다.

    관문은 클립 상태를 스키마보다 먼저 본다. 신고로 막힌 클립에 "장르가 비었다"는 사유를 표시하면
    고칠 사람이 엉뚱한 곳을 고친다.

    `write_package`가 메타를 다시 만들면 pydantic 검증이 행마다 두 번 돌므로 메타를 함께
    돌려준다. 화면은 메타를 쓰지 않고 버린다.

    Args:
        approved_at: 승인 시각. 없어도 통과시킨다. 계약상 필수는 캡션뿐이다.
            옛 데이터나 수동 전이에는 판정 레코드가 없을 수 있으며 클립의 결함으로 보지 않는다.

    Returns:
        `(ClipMetadata, [])` 또는 `(None, ["항목 이름: 사유", …])`.
    """
    m, found = check_row_detail(w, v, c, approved_at)
    return m, [line for _field, line in found]


def check_row_detail(
    w: WorkItem, v: Video, c: Clip | None, approved_at, revision_count: int = 0,
) -> tuple[ClipMetadata | None, list[tuple[str, str]]]:
    """`check_row`와 같은 관문을 적용하고 사유마다 스키마 키를 함께 돌려준다.

    사유는 `[(키, "이름: 사유"), …]` 모양이다.

    판정은 이 함수에만 있고 `check_row`는 이 결과에서 문자열만 뽑는다.
    두 함수가 각자 판정하면 화면과 `report.json`이 달라진다.
    """
    rights_err = rights_blocker(c)
    if rights_err:
        return None, [(RIGHTS_FIELD, rights_err)]
    offset, off_err = offset_of(v)
    if off_err:
        return None, [(OFFSET_FIELD, _gate_blocker(OFFSET_FIELD, off_err))]
    try:
        return build_metadata(w, v, c, approved_at, offset, revision_count), []
    except ValidationError as e:
        return None, [(_path_of(err), blocker(_path_of(err), _msg_of(err))) for err in e.errors()]


def write_package(
    rows: list[tuple[WorkItem, Video, Clip | None]],
    approved_at_by_item: dict[UUID, Any],
    out_path: Path,
    *,
    project_name: str,
    sampled_from: int | None = None,
    generated_at: datetime | None = None,
    qc_held: Sequence[UUID] = (),
    revisions: dict[UUID, int] | None = None,
    omit_optional: bool = False,
) -> dict:
    """zip 파일 하나를 만들고 리포트를 돌려준다.

    클립이 수만 건이면 zip이 수백 MB가 되므로 메모리 대신 파일(`out_path`)에 바로 쓴다.
    업로드는 부르는 쪽이 한다.

    Args:
        revisions: 작업별 검수 수정 횟수. 메타의 `review.revision_count`가 된다. 없는 작업은 0이다.
        qc_held: 캡션 품질검수가 보류해 `rows`에 들어오지 않은 승인분의 작업 식별자.
            패키지에 담을 메타가 없으므로 리포트에만 싣는다. 부르는 쪽이 건네는 이유는
            `QC_HELD_REASON` 주석에 있다.
        omit_optional: 참이면 메타 파일에서 `OPTIONAL_LABEL_FIELDS` 키를 뺀다. 판정은 전체 모델로
            하고 파일을 쓸 때만 빼므로 납품 대상·제외는 옵션과 무관하다.
            체크섬은 뺀 뒤의 바이트로 잰다.

    Raises:
        PackageIntegrityError: 색인과 파일이 어긋난 경우. 이 패키지는 올리지 않는다.
    """
    included: list[dict] = []
    excluded: list[dict] = []
    by_reason: dict[str, int] = {}
    checksummed = 0
    # 실린 클립의 원본 영상. `report.json` 비식별화 집계의 분모다.
    videos: set[UUID] = set()
    # 실린 클립의 원본 영상 중 비식별화한 영상(`deident_processed`).
    processed: set[UUID] = set()
    # 비식별화한 영상 중 가린 영역이 있는 영상(`deident_masked`).
    masked: set[UUID] = set()
    omitted = sorted(OPTIONAL_LABEL_FIELDS) if omit_optional else []
    # 출처(방송사·프로그램·방영 구간)도 담긴 클립의 영상에서만 모은다.
    # 제외된 영상의 방송사가 카드에 표시되면 받는 쪽이 패키지에 없는 자료를 찾게 된다.
    sources: dict[UUID, tuple[str | None, str | None, datetime | None]] = {}
    # `sources.csv`의 행. 담긴 클립의 원본 영상만 싣는다. 카드의 출처와 같은 모집단이다.
    source_rows: dict[UUID, dict[str, Any]] = {}
    total_seconds = 0.0

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        manifest: list[dict] = []
        for w, v, c in rows:
            # 관문 판정은 `check_row_detail` 한 곳에 있다. 완료 화면도 같은 판정을 쓴다.
            m, found = check_row_detail(
                w, v, c, approved_at_by_item.get(w.work_item_id),
                (revisions or {}).get(w.work_item_id, 0),
            )
            if m is None:
                excluded.append({
                    "work_item_id": str(w.work_item_id),
                    # 사람이 읽는 사유(화면과 같은 문자열)와 스키마 키를 나란히 싣는다.
                    # 사람은 사유를 읽고, 기계는 키로 스키마와 대응시킨다.
                    "reasons": [line for _field, line in found],
                    "fields": [field for field, _line in found],
                })
                # 사유별 집계는 첫 사유로 센다. 통계 화면(`_blocker_reasons`)과 같은 규칙이다.
                # 한 클립이 여러 필드에 걸려도 한 번만 세어 합계가 `excluded`와 같게 한다.
                key = blocker_label(found[0][1])
                by_reason[key] = by_reason.get(key, 0) + 1
                continue

            # 선택 항목 제외는 여기서만 적용한다. 색인의 체크섬도 이 바이트로 잰다.
            doc = m.model_dump(mode="json", exclude=set(omitted) or None)
            body = json.dumps(doc, ensure_ascii=False, indent=1).encode()
            path = f"metadata/{m.clip_id}.json"
            z.writestr(path, body)
            manifest.append({
                # 스키마 버전을 행마다 싣는다. 색인만 읽는 쪽도 파싱 오류를 만나기 전에
                # 첫 필드에서 형식 변경을 확인할 수 있다. 값은 `ClipMetadata`의 값을 쓴다.
                "schema_version": m.schema_version,
                "clip_id": str(m.clip_id),
                "source_video_id": str(m.source_video_id),
                "metadata_path": path,
                "start_time": m.start_time,
                "end_time": m.end_time,
                "duration": m.duration,
                "caption": m.caption,
                "checksum": _xxh64(body),
            })
            included.append({"clip_id": str(m.clip_id)})
            videos.add(v.video_id)
            if deident_processed(v):
                processed.add(v.video_id)
            if deident_masked(v):
                masked.add(v.video_id)
            sources[v.video_id] = (v.broadcaster, v.program_name, v.aired_at)
            if v.video_id not in source_rows:
                source_rows[v.video_id] = source_row(v, doc)
            source_rows[v.video_id]["clip_count"] += 1
            total_seconds += m.duration
            if m.source_checksum:
                checksummed += 1

        # 품질검수가 보류한 승인분. 후보에 없어 위 루프를 거치지 않으므로 여기서 따로 싣는다.
        # 싣지 않으면 패키지만 받는 쪽은 승인분 몇 건이 빠졌는지 알 수 없다.
        for held_id in qc_held:
            excluded.append({
                "work_item_id": str(held_id),
                "reasons": [QC_HELD_REASON],
                # `fields`는 받는 쪽이 `schema/clip-metadata-v1.json`과 대응시키는 스키마 키다.
                # 보류는 사람이 풀어야 하는 운영 판정이라 대응시킬 스키마 키가 없다. 그래서
                # 빈 배열로 둔다(`qc_status`는 스키마에 없다). 행마다 모양이 같아야 읽는 쪽이
                # 분기하지 않으므로 필드 자체는 남긴다.
                "fields": [],
            })
            # 사유별 집계도 같은 규칙(첫 `:` 앞)으로 세어 합계가 `excluded`와 같게 한다.
            key = blocker_label(QC_HELD_REASON)
            by_reason[key] = by_reason.get(key, 0) + 1

        # 닫기 전에 대조한다. 어긋나면 색인을 쓰기 전에 예외로 멈춘다.
        _check_before_close(z, manifest)
        _check_sources(manifest, source_rows)
        z.writestr(
            "manifest.jsonl",
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in manifest),
        )
        # 제목순으로 싣는다. 같은 제목이 여럿이면 식별자순이라 같은 입력은 같은 파일이 된다.
        z.writestr("sources.csv", sources_csv(sorted(
            source_rows.values(), key=lambda r: (r["title"], r["source_video_id"]),
        )))
        z.writestr(
            "schema/clip-metadata-v1.json",
            json.dumps(json_schema(), ensure_ascii=False, indent=2),
        )
        z.writestr(
            "dataset_card.yaml",
            _dataset_card(
                # 카드와 리포트의 분모를 같게 둔다. 카드에서는 `source_rows`와 `total_clips`의
                # 차이가 제외분이다. 보류분을 리포트에만 실으면
                # 한 패키지 안의 두 파일이 다른 수를 표시한다.
                project_name, included, len(rows) + len(qc_held), sampled_from, generated_at,
                sources=sources, total_seconds=total_seconds,
                omitted=omitted,
            ),
        )
        z.writestr("README.md", _readme(sampled_from, omitted))

        n = len(included)
        listed = excluded[:EXCLUSIONS_LISTED]
        report = {
            # 리포트를 보는 사람이 가장 먼저 확인해야 하므로 샘플 여부를 맨 앞에 둔다.
            "sample": sampled_from is not None,
            "sampled_from": sampled_from,
            # 선택 항목 제외 여부와 뺀 키.
            # 메타 파일에 키가 없는 이유를 이 파일만 보고 알 수 있게 한다.
            "omit_optional": omit_optional,
            "omitted_fields": omitted,
            "included": n,
            "excluded": len(excluded),
            # 사유별 집계. 무엇을 더 만들어야 하는지는 건별 목록보다 이 집계로 판단한다.
            "excluded_by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
            # 건별 목록은 파일 크기를 제한하려고 상한까지만 싣고, 잘렸으면 잘렸다고 적는다.
            # `excluded`는 전수이므로 표시가 없으면 받는 쪽이 짧은 목록을 전체로 읽는다.
            "exclusions": listed,
            "exclusions_truncated": len(listed) < len(excluded),
            # 원본 체크섬이 붙은 클립의 비율. 받는 쪽이 원본과 대조할 수 있는 클립의 비율이다.
            # beat 태스크가 나중에 채우므로 시간이 지나면 1.0에 가까워진다.
            "source_checksum_rate": round(checksummed / n, 4) if n else 0.0,
            # 비식별화 집계. 실린 클립의 영상 기준이다.
            # `processed_videos`와 `masked_videos`의 차이는 처리했지만 가릴 얼굴을 찾지 못한
            # 영상의 수다.
            "deidentification": {
                "videos": len(videos),
                "processed_videos": len(processed),
                "masked_videos": len(masked),
            },
        }
        z.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))
    # 닫은 뒤 다시 열어 색인이 가리키는 파일이 실제로 있고 체크섬이 맞는지 확인한다.
    _verify(out_path, manifest)
    return report


def _check_before_close(z: zipfile.ZipFile, manifest: list[dict]) -> None:
    """zip을 닫기 전에 색인과 `metadata/` 파일을 대조한다.

    색인 줄 수와 `metadata/` 파일 수가 같고, 색인이 가리키는 파일이 모두 있어야 한다.
    `zipfile`은 같은 이름을 두 번 써도 경고만 낸다. 이때 줄 수는 맞아도 한 파일이 가려지므로
    이름 중복도 확인한다.
    """
    names = [n for n in z.namelist() if n.startswith("metadata/")]
    paths = [line["metadata_path"] for line in manifest]
    if len(paths) != len(names) or len(set(names)) != len(names) or set(paths) != set(names):
        raise PackageIntegrityError(
            f"색인 {len(paths)}줄 · metadata 파일 {len(names)}개"
            f"(서로 다른 이름 {len(set(names))}개) — 색인과 파일이 어긋남"
        )


def _check_sources(manifest: list[dict], source_rows: dict[UUID, dict[str, Any]]) -> None:
    """색인의 원본 영상과 `sources.csv`의 행을 대조한다.

    색인의 `source_video_id`가 모두 `sources.csv`에 있고, 행마다 센 클립 수의 합이 색인 줄 수와
    같아야 한다. 받는 쪽은 `sources.csv`에 없는 `source_video_id`의 원본을 찾을 수 없다.

    Raises:
        PackageIntegrityError: 색인과 원본 목록이 어긋난 경우.
    """
    in_manifest = {line["source_video_id"] for line in manifest}
    listed = {r["source_video_id"] for r in source_rows.values()}
    counted = sum(r["clip_count"] for r in source_rows.values())
    if in_manifest != listed or counted != len(manifest):
        raise PackageIntegrityError(
            f"색인 원본 영상 {len(in_manifest)}개 · sources.csv {len(listed)}행"
            f"(클립 {counted}개, 색인 {len(manifest)}줄) — 색인과 원본 목록이 어긋남"
        )


def _verify(out_path: Path, manifest: list[dict]) -> None:
    """닫은 zip을 다시 열어 파일 존재·경로·수량·체크섬을 확인한다.

    `ZipFile.read`는 CRC도 함께 검사하므로 손상된 항목도 여기서 잡힌다.
    """
    with zipfile.ZipFile(out_path) as z:
        names = set(z.namelist())
        lines = [json.loads(x) for x in z.read("manifest.jsonl").decode().splitlines() if x]
        if len(lines) != len(manifest):
            raise PackageIntegrityError(f"색인 {len(manifest)}줄을 썼는데 {len(lines)}줄이 읽힘")
        for line in lines:
            path = line["metadata_path"]
            if path not in names:
                raise PackageIntegrityError(f"색인이 가리키는 파일이 없음: {path}")
            if _xxh64(z.read(path)) != line["checksum"]:
                raise PackageIntegrityError(f"체크섬 불일치: {path}")


def _source_of(
    sources: dict[UUID, tuple[str | None, str | None, datetime | None]],
) -> dict[str, Any]:
    """데이터셋 카드의 `source` 절을 만든다.

    담긴 클립의 원본 영상에서 방송사·프로그램 수·방영 구간을 센다.
    방송사·프로그램명·방영일은 영상 단위 값이라 사실대로 셀 수 있다.
    `broadcaster: 샘플 방송`처럼 고정하면 프로젝트가 둘 이상일 때 사실과 달라진다.

    값이 있는 것만 표시한다. 값이 빈 영상은 세지 않고, 세 값이 모두 비면 절을 비운다.
    `broadcaster: ""`를 내면 받는 쪽이 방송사가 없다는 사실로 읽는다. 방송사가 여럿이면
    이름을 모두 나열해 방송사별로 구분한다.

    Args:
        sources: 담긴 영상별 (방송사, 프로그램명, 방영일). 담기지 않은 영상은 들어오지 않는다.

    Returns:
        `broadcaster`·`program_count`·`aired_range` 중 셀 수 있었던 것만 담은 dict.
    """
    out: dict[str, Any] = {}
    casters = sorted({b for b, _p, _a in sources.values() if b})
    if casters:
        out["broadcaster"] = casters[0] if len(casters) == 1 else casters
    programs = {p for _b, p, _a in sources.values() if p}
    if programs:
        out["program_count"] = len(programs)
    aired = sorted(a for _b, _p, a in sources.values() if a)
    if aired:
        # 방영일은 운영 시간대의 자정으로 저장되므로 UTC로 날짜를 자르면 하루 앞당겨진다.
        out["aired_range"] = [operating_time.local_date(aired[0]).isoformat(),
                              operating_time.local_date(aired[-1]).isoformat()]
    return out


def _dataset_card(
    project_name: str,
    included: list[dict],
    source_rows: int,
    sampled_from: int | None,
    generated_at: datetime | None = None,
    *,
    sources: dict[UUID, tuple[str | None, str | None, datetime | None]] | None = None,
    total_seconds: float = 0.0,
    omitted: Sequence[str] = (),
) -> str:
    """데이터셋 카드(YAML)를 만든다. 수는 실제로 담은 것을 기준으로 센다.

    제외분까지 세면 카드의 수가 사실과 달라진다.
    샘플이면 `sample: true`와 모집단 크기를 함께 적어 카드 파일만 보고도 샘플과 납품본을
    구별할 수 있게 한다.
    선택 항목을 뺐으면(`omitted`) 같은 방식으로 `omit_optional: true`와 뺀 키를 적는다.

    YAML은 직렬화기(`yaml.safe_dump`)가 쓰고 값도 직렬화기가 인용한다. f-문자열로 한 줄씩
    붙이면 프로젝트 이름에 `:`·`#`·앞 `- `가 있을 때 YAML이 깨지거나 뜻이 바뀐다
    (`name: 샘플 방송: 9시 뉴스`가 중첩 맵이 된다). 직렬화기는 주석을 쓸 수 없으므로 라이선스 설명은
    값(`*_note`)으로 둔다. 값이면 YAML을 다시 읽어도 사라지지 않는다.

    공정(`processing`)·보증(`quality`) 절은 싣지 않는다. 카드에는 분량·출처·라이선스만 싣는다.
    검수·비식별화 정보는 클립 레코드의 `review`·`deidentification`과 `report.json` 집계에 있다.
    """
    card: dict[str, Any] = {"name": project_name, "sample": sampled_from is not None}
    if sampled_from is not None:
        card["sampled_from"] = sampled_from
    card["omit_optional"] = bool(omitted)
    if omitted:
        card["omitted_fields"] = list(omitted)
    # 만든 시각을 적어 두 번째 전달분을 첫 번째와 구별할 수 있게 한다.
    if generated_at:
        card["generated_at"] = generated_at.isoformat()
    card["language"] = "ko"
    # 도메인은 플랫폼 자체의 정의(한국어 보도영상 가공)라 프로젝트마다 같다. 그래서 분할·캡션과
    # 달리 프로젝트 설정 대신 고정값으로 둔다.
    card["domain"] = "방송 보도영상"
    card["total_clips"] = len(included)
    # 분량. 받는 쪽이 가장 먼저 확인하는 수다. 담은 클립의 길이 합이라 `total_clips`와 모집단이
    # 같고 `manifest`의 `duration` 합과 같다. 제외분을 세면 두 수가 어긋난다.
    # 올림으로 계산한다. `round`를 쓰면 18초 미만이 `0.0`이 되어 클립이 담겼는데도 카드에
    # "분량 없음"이 표시된다. 0.01시간을 더 표시하는 오차는 작지만 "없음"은 사실과 반대다.
    card["total_duration_hours"] = math.ceil(total_seconds / 36) / 100 if total_seconds else 0.0
    # 담긴 수의 분모. 이 수와 `total_clips`의 차이가 `report.json`의 `excluded`와 같다.
    # 전량이면 승인 전수(품질검수 보류분 포함)이고, 샘플이면 뽑은 표본의 크기다.
    card["source_rows"] = source_rows
    card["media_included"] = False
    card["source"] = _source_of(sources or {})
    # `Apache-2.0`은 플랫폼 소스의 라이선스다. 이 값만 있으면 방송 보도영상 메타데이터도 그
    # 라이선스로 읽힌다. 그래서 데이터 라이선스를 따로 적는다. 데이터 이용 조건은 제공사와의
    # 계약이 정한다.
    card["license"] = {
        "platform_source": "Apache-2.0",
        "platform_source_note": "이 데이터를 만든 플랫폼(RebornStudio)의 라이선스입니다",
        "dataset": "별도 계약",
        "dataset_note": "데이터 이용 조건은 제공사와의 계약을 따릅니다",
    }
    return yaml.safe_dump(card, allow_unicode=True, sort_keys=False, width=1000)


def _readme(sampled_from: int | None = None, omitted: Sequence[str] = ()) -> str:
    """패키지 README를 만든다. 받는 쪽이 먼저 알아야 할 것만 쓴다.

    특히 미디어 미포함, 샘플 여부, 선택 항목 제외 여부를 알린다.
    """
    keys = ", ".join(f"`{k}`" for k in omitted)
    names = ", ".join(FIELD_LABEL[k] for k in omitted)
    omit = (
        "## 선택 항목 제외\n\n"
        "이 패키지는 선택 항목을 빼고 만들었습니다. `metadata/` 파일에 다음 키가\n"
        f"없습니다: {keys} ({names}).\n\n"
        "이 항목들은 값이 늘 비어 있어 키를 빼도 잃는 값이 없습니다.\n"
        "모두 스키마의 필수 항목이 아니므로 키가 없어도 스키마 검증을 통과합니다.\n\n"
        if omitted else ""
    )
    head = (
        f"# RebornStudio 데이터셋 패키지 (검수용 샘플)\n\n"
        f"**이것은 형식 확인용 샘플입니다.** 전체 {sampled_from}건 중 일부만 담겨 있으며\n"
        "납품본이 아닙니다. 샘플은 전체에서 고르게 뽑았습니다.\n\n"
        "`report.json`의 집계는 **담긴 표본 기준**입니다. 승인분 전수 기준 제외 집계는\n"
        "전량 패키지의 `report.json`에 있습니다.\n\n"
        if sampled_from is not None else
        "# RebornStudio 데이터셋 패키지\n\n"
    )
    return (
        head
        + omit
        + "## 미디어 파일은 들어 있지 않습니다\n\n"
        "클립은 `source_video_id`와 **원본 기준 시각**(`start_time`/`end_time`)으로\n"
        "원본 영상의 구간을 가리킵니다. 원본은 제공사 보유분을 사용하세요.\n\n"
        "`source_start_offset`은 분석에 쓴 파일이 원본과 축이 달랐을 때 더한 보정값입니다.\n"
        "`0`이면 분석이 원본을 그대로 읽었다는 뜻입니다.\n\n"
        "## 구성\n\n"
        "- `metadata/{clip_id}.json` — 클립별 메타데이터\n"
        "- `manifest.jsonl` — 1행 1클립 색인(스키마 버전·체크섬 포함)\n"
        "- `sources.csv` — 1행 1원본 영상 목록(원본 파일 찾기)\n"
        "- `schema/clip-metadata-v1.json` — 위 JSON의 스키마(검증에 쓴 것과 같은 출처)\n"
        "- `dataset_card.yaml` — 데이터셋 카드\n"
        "- `report.json` — 포함·제외 집계, 사유별 집계와 제외 목록\n\n"
        "## 무결성 확인\n\n"
        "`manifest.jsonl`의 `checksum`은 해당 `metadata/` 파일 바이트의 **xxh64** 값이며\n"
        "`xxh64:` 접두어가 붙어 있습니다. 패키지는 만들 때 색인 줄 수·파일 실재·체크섬을\n"
        "대조했고, 어긋나면 만들어지지 않습니다.\n\n"
        "## 원본 대조\n\n"
        "`source_checksum`은 분석에 쓴 원본 파일의 sha256이며 **`sha256:` 접두어가\n"
        "붙어 있습니다** — 대조할 때는 접두어를 뗀 뒤 비교하세요.\n\n"
        "```\n"
        "sha256sum <파일>        # Linux\n"
        "shasum -a 256 <파일>    # macOS\n"
        "```\n\n"
        "`null`이면 아직 측정되지 않은 것입니다. 값이 없다고 클립이 빠지지는 않습니다.\n\n"
        "## 원본 파일 찾기\n\n"
        "`sources.csv`는 이 패키지에 클립이 담긴 원본 영상을 한 행에 하나씩 나열합니다.\n"
        "`metadata/` 파일의 `source_video_id`를 `sources.csv`의 같은 열과 이으면 그 클립이\n"
        "어느 원본에서 나왔는지 알 수 있습니다.\n\n"
        "- `title` — 플랫폼에 등록한 제목\n"
        "- `original_filename` — 등록할 때의 파일 이름\n"
        "- `storage_key` — 저장소 버킷에서 등록한 영상의 오브젝트 키\n"
        "  (파일을 직접 올린 영상은 빈 값)\n"
        "- `source_checksum` — 원본 파일의 sha256\n"
        "  (메타의 `source_checksum`과 같은 값, `sha256:` 접두어 포함)\n"
        "- `clip_count` — 이 패키지에 담긴 그 원본의 클립 수\n\n"
        "파일 이름과 제목은 참고 정보이며 패키지 안에서 겹칠 수 있습니다. 플랫폼은 파일 이름이\n"
        "유일하기를 요구하지 않고, 같은 파일을 두 번 등록하면 플랫폼에서 중복으로 표시합니다.\n"
        "원본은 `source_video_id`로 가리키고 `source_checksum`(sha256)으로 확인합니다.\n\n"
        "파일은 UTF-8(BOM 포함)이라 엑셀에서 바로 열립니다. `=`·`+`·`-`·`@`로 시작하는 값은\n"
        "수식으로 실행되지 않도록 앞에 작은따옴표(`'`)를 붙였습니다. 파일 이름으로 대조할 때는\n"
        "이 따옴표를 떼고 비교하세요.\n\n"
        "## 비식별화\n\n"
        "클립 메타데이터의 `deidentification`은 **라벨링에 쓴 작업 사본**에 적용한 비식별화를\n"
        "적습니다(처리 여부·방식·누락 의심). 원본 파일에는 얼굴 가림이 없습니다.\n\n"
        "- `processed` — 비식별화 모델이 작업 사본을 끝까지 처리했는지\n"
        "- `method` — 작업 사본에서 영역을 가린 방식. 처리했지만 가릴 얼굴을 찾지 못해\n"
        "  가린 영역이 없으면 `none`입니다\n"
        "- `suspected_miss` — 사람이 다시 확인해야 하는지. 얼굴을 찾지 못한 영상도 `true`입니다\n\n"
        "영상별 처리 집계는 `report.json`의 `deidentification`에 있습니다. `processed_videos`는\n"
        "처리한 영상 수, `masked_videos`는 그중 가린 영역이 있는 영상 수입니다.\n\n"
        "## 제외분\n\n"
        "담기지 않은 클립은 **폐기된 것이 아니라** `report.json`에 **사유와 함께** 남습니다.\n"
        "제외 사유는 네 가지입니다.\n\n"
        "- **필수값 없음** — 캡션이 없거나 너무 짧은 경우입니다.\n"
        "- **시각을 원본 축으로 환산 불가** — 분석에 쓴 파일의 시작 오프셋을 모르는 경우입니다.\n"
        "  어긋난 시각을 내보내는 대신 제외합니다.\n"
        "- **비식별화 신고** — 비식별화 미흡 신고로 제외된 클립(`비식별화 신고: 제외`)과 신고\n"
        "  처리를 기다리는 동안 작업이 멈춘 클립(`비식별화 신고: 처리 대기`)입니다.\n"
        "- **품질검수 보류** — 캡션 품질검수에서 다시 확인이 필요하다고 판정한 승인분입니다.\n"
        "  운영자가 보류를 풀면 다음 전달분에 담깁니다.\n\n"
        "사유 한 줄은 `항목 이름: 사유` 꼴입니다(`캡션: 10자 이상 필요`).\n"
        "같은 항목의 **스키마 키**는 `fields`에 따로 있습니다(`caption`). 품질검수 보류는\n"
        "스키마 항목의 오류가 아니므로 `fields`가 빈 배열입니다.\n"
        "`excluded_by_reason`은 첫 사유 기준 집계라 합이\n"
        "`excluded`와 같습니다. 목록(`exclusions`)은 앞 1,000건까지만 싣고, 더 있으면\n"
        "`exclusions_truncated`가 `true`입니다 — 그때도 수(`excluded`)는 전체입니다.\n"
    )
