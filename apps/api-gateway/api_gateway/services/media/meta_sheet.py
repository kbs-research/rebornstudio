"""영상 메타 일괄 입력의 표 파일(.xlsx·.csv)을 만들고 읽는 모듈.

DB를 읽지 않는다. 양식을 만드는 `build_template`과 올린 파일을 행으로 바꾸는 `read_table`,
셀 값을 필드 값으로 바꾸는 `cell_text`·`parse_aired`가 있다. 행을 영상과 맞추고 검사하는 일은
`media.meta_import`가 한다.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final

from fastapi import HTTPException, status
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reborn_contracts import BULK_META_MAX_ROWS

from api_gateway.services import video_meta
from api_gateway.services.csv_cell import FORMULA_LEADS

#: 올릴 수 있는 파일의 최대 크기(바이트).
#: API 본문 한도(`max_request_body_bytes`, 2 MiB) 안에 들어가게 둔다.
#: 양식(.xlsx) 5,000행은 약 300 KB라서 행 수 상한보다 먼저 걸리지 않는다.
MAX_FILE_BYTES: Final = 1024 * 1024

#: .xlsx를 풀었을 때 허용하는 최대 크기(바이트). 작은 압축 파일이 수 GB로 풀리는 파일을 막는다.
MAX_UNZIPPED_BYTES: Final = 64 * 1024 * 1024

#: 머리글을 찾을 최대 행 수. 양식 위에 사람이 제목 줄을 몇 줄 더 넣어도 읽는다.
HEADER_SCAN_ROWS: Final = 10

#: 이 수만큼 빈 행이 이어지면 표가 끝난 것으로 본다.
#: 서식만 남은 빈 행이 백만 개 있는 시트를 끝까지 읽지 않게 한다.
MAX_BLANK_RUN: Final = 1000

#: 데이터 시트 이름. 양식은 이 시트와 안내 시트 두 개로 만든다.
SHEET_NAME: Final = "메타"
#: 안내 시트 이름. 읽을 때는 이 시트를 건너뛴다.
GUIDE_SHEET_NAME: Final = "안내"

#: 방영일을 저장하는 시간대. 화면의 `airedAtFromInput`과 같이 KST 자정으로 저장한다.
KST: Final = timezone(timedelta(hours=9))

_META_NAME = dict(video_meta.FIELDS)

#: 양식의 열 순서와 머리글. 영상 ID와 파일은 영상을 찾는 열이고 나머지는 바꾸는 열이다.
COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("video_id", "영상 ID"),
    ("file", "파일"),
    ("title", "제목"),
    ("broadcaster", _META_NAME["broadcaster"]),
    ("program_name", _META_NAME["program_name"]),
    ("genre", _META_NAME["genre"]),
    ("episode", "회차"),
    ("aired_at", _META_NAME["aired_at"]),
)

#: 영상을 찾는 데 쓰는 열. 머리글에 하나도 없으면 표로 읽지 않는다.
MATCH_KEYS: Final = ("video_id", "file", "title")

#: 바꾸는 열. 키는 `VideoUpdateRequest`의 필드 이름과 같다.
FIELD_KEYS: Final = ("title", "broadcaster", "program_name", "genre", "episode", "aired_at")


def _norm_header(text: str) -> str:
    """머리글을 비교할 형태로 바꾼다. 공백·밑줄을 지우고 영문은 소문자로 바꾼다."""
    return re.sub(r"[\s_]+", "", unicodedata.normalize("NFC", text)).lower()


#: 머리글 이름과 필드 키의 대응. 양식의 머리글 외에 영문 키와 흔히 쓰는 다른 이름도 받는다.
_ALIASES: Final[dict[str, str]] = {
    _norm_header(name): key
    for key, names in {
        "video_id": ("영상 ID", "영상아이디", "video_id", "id"),
        "file": ("파일", "파일명", "파일 이름", "file", "filename", "key"),
        "title": ("제목", "영상 제목", "title"),
        "broadcaster": ("방송사", "broadcaster"),
        "program_name": ("프로그램명", "프로그램", "program_name", "program"),
        "genre": ("장르", "genre"),
        "episode": ("회차", "episode"),
        "aired_at": ("방영일", "방송일", "aired_at", "air_date"),
    }.items()
    for name in names
}


@dataclass(frozen=True)
class Formula:
    """수식이 든 셀. 값 대신 수식을 넣은 셀은 저장하지 않고 오류로 표시한다."""
    text: str


@dataclass
class TemplateRow:
    """양식 한 행에 채울 영상 하나의 지금 값."""
    video_id: str
    file: str | None
    title: str
    broadcaster: str | None
    program_name: str | None
    genre: str | None
    episode: str | None
    aired_at: datetime | None


@dataclass
class Table:
    """올린 파일에서 읽은 표."""
    #: 읽은 시트 이름. CSV이면 `None`이다.
    sheet: str | None
    #: 알아본 열의 필드 키. 파일에 있는 순서다.
    columns: list[str]
    #: 알아보지 못한 열의 머리글.
    ignored: list[str]
    #: 데이터 행. 행 번호와 필드 키별 셀 값의 쌍이다. 빈 행은 뺀다.
    rows: list[tuple[int, dict[str, object]]] = field(default_factory=list)


def aired_text(at: datetime | None) -> str | None:
    """방영일을 양식에 쓸 KST 날짜(`YYYY-MM-DD`)로 바꾼다."""
    if at is None:
        return None
    aware = at if at.tzinfo else at.replace(tzinfo=UTC)
    return aware.astimezone(KST).date().isoformat()


def _text_cell(ws, row: int, col: int, value: str | None) -> None:
    """문자열 셀을 쓴다. 수식 시작 글자로 시작해도 수식이 아닌 문자열로 저장한다.

    `=HYPERLINK(...)` 같은 제목이 수식으로 저장되면 내려받은 사람이 파일을 열 때 실행된다.
    `csv_cell.safe_cell`처럼 작은따옴표를 값에 붙이면 다시 올릴 때 제목이 바뀐 것으로 읽힌다.
    그래서 셀 형식을 문자열로 고정하고 `quotePrefix` 표시만 단다. 값은 그대로 남는다.
    """
    cell = ws.cell(row=row, column=col)
    if value is None:
        return
    cell.value = value
    cell.data_type = "s"
    if value.startswith(FORMULA_LEADS):
        cell.quotePrefix = True


def build_template(rows: list[TemplateRow]) -> bytes:
    """묶음 영상의 지금 값을 채운 양식(.xlsx)을 만든다.

    첫 시트는 머리글 한 줄과 영상 한 줄씩이다. 둘째 시트에 입력 규칙을 적는다.
    모든 열을 텍스트 형식으로 둔다. 숫자 형식이면 회차 `001`이 `1`로 바뀐다.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    head_font = Font(bold=True)
    ref_fill = PatternFill("solid", fgColor="EEEEEE")
    for col, (key, name) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.font = head_font
        if key in ("video_id", "file"):
            cell.fill = ref_fill
    for r, v in enumerate(rows, start=2):
        values = (
            v.video_id, v.file, v.title, v.broadcaster, v.program_name, v.genre, v.episode,
            aired_text(v.aired_at),
        )
        for col, value in enumerate(values, start=1):
            _text_cell(ws, r, col, value)
    widths = (38, 28, 30, 12, 24, 12, 10, 12)
    for col, width in enumerate(widths, start=1):
        letter = ws.cell(row=1, column=col).column_letter
        ws.column_dimensions[letter].width = width
        ws.column_dimensions[letter].number_format = "@"
    ws.freeze_panes = "A2"

    guide = wb.create_sheet(GUIDE_SHEET_NAME)
    lines = (
        "영상 메타 일괄 입력 양식",
        "",
        f"1. '{SHEET_NAME}' 시트의 값을 고친 뒤 이 파일을 그대로 올립니다.",
        "2. 영상 ID와 파일은 영상을 찾는 열입니다. 고치지 않습니다.",
        "   영상 ID가 비어 있으면 파일, 파일도 비어 있으면 제목으로 이 묶음의 영상을 찾습니다.",
        "   여러 영상과 같은 파일·제목은 반영하지 않습니다.",
        "3. 빈 셀은 바꾸지 않습니다. 값을 지우려면 영상 상세에서 메타를 편집합니다.",
        f"4. {_META_NAME['aired_at']}은 2026-05-28, 2026.05.28, 20260528 형식으로 넣습니다.",
        "5. 수식은 넣지 않습니다. 값만 읽습니다.",
        f"6. 한 파일에 최대 {BULK_META_MAX_ROWS:,}행, {MAX_FILE_BYTES // 1024:,} KB까지 올립니다.",
        "   행이 더 많으면 파일을 나눠 올립니다.",
        "   행마다 따로 영상을 찾으므로 나눠 올려도 결과가 같습니다.",
        "7. 파일을 올리면 반영하기 전에 바뀌는 값을 미리 보기로 보여 줍니다.",
    )
    for r, line in enumerate(lines, start=1):
        cell = guide.cell(row=r, column=1, value=line)
        cell.alignment = Alignment(wrap_text=False)
        if r == 1:
            cell.font = head_font
    guide.column_dimensions["A"].width = 100

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def cell_text(value: object) -> str | None:
    """셀 값을 필드에 넣을 문자열로 바꾼다. 빈 값은 `None`이다.

    유니코드는 NFC로 맞춘다. macOS에서 만든 파일 이름은 한글이 NFD로 분리되어 있어서
    맞추지 않으면 같은 파일 이름이 다른 문자열로 비교된다.
    수식 시작 글자 앞에 붙은 작은따옴표는 `csv_cell.safe_cell`이 붙인 표시라서 뗀다.
    """
    if value is None or isinstance(value, Formula):
        return None
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = str(int(value)) if value.is_integer() else str(value)
    elif isinstance(value, datetime):
        midnight = value.time() == datetime.min.time()
        text = value.date().isoformat() if midnight else value.isoformat()
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    text = unicodedata.normalize("NFC", text).strip()
    if len(text) > 1 and text[0] == "'" and text[1:].startswith(FORMULA_LEADS):
        text = text[1:]
    return text or None


_DATE_SEP = re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*\.?$")
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def parse_aired(value: object) -> datetime:
    """방영일 셀을 KST 자정 시각으로 바꾼다.

    받는 형식은 날짜 셀, `YYYY-MM-DD`, `YYYY.MM.DD`(화면 표기 `2026. 5. 28.` 포함),
    `YYYY/MM/DD`, `YYYYMMDD`다. 시각이 있는 날짜 셀은 날짜만 쓴다.
    범위 검사(1920년부터 내일까지)는 `VideoUpdateRequest`가 한다.

    Raises:
        ValueError: 날짜로 읽지 못하는 경우. 메시지는 화면에 그대로 표시한다.
    """
    if isinstance(value, Formula):
        raise ValueError("수식 대신 날짜 값을 넣습니다")
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        text = cell_text(value) or ""
        m = _DATE_SEP.match(text) or _DATE_COMPACT.match(text)
        if not m:
            raise ValueError("날짜 형식이 아닙니다. 2026-05-28처럼 넣습니다")
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            raise ValueError("달력에 없는 날짜입니다") from None
    return datetime(d.year, d.month, d.day, tzinfo=KST)


def _reject(code: str, http: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    """파일을 읽지 못한 이유를 담은 예외를 만든다."""
    return HTTPException(http, code)


def _header_map(cells: list[object]) -> tuple[list[tuple[int, str]], list[str]] | None:
    """머리글 행 후보에서 알아본 열과 알아보지 못한 머리글을 돌려준다.

    영상을 찾는 열(`MATCH_KEYS`)이 하나도 없으면 머리글 행이 아니라서 `None`이다.

    Raises:
        HTTPException: 같은 필드의 열이 둘 이상인 경우(422 `meta_import_duplicate_column`).
    """
    known: list[tuple[int, str]] = []
    ignored: list[str] = []
    for i, c in enumerate(cells):
        text = cell_text(c)
        if not text:
            continue
        key = _ALIASES.get(_norm_header(text))
        if key is None:
            ignored.append(text)
        else:
            known.append((i, key))
    keys = [k for _, k in known]
    if not any(k in MATCH_KEYS for k in keys):
        return None
    if len(keys) != len(set(keys)):
        raise _reject("meta_import_duplicate_column")
    return known, ignored


def _collect(raw_rows) -> tuple[list[tuple[int, str]], list[str], list[tuple[int, dict]]]:
    """행 목록에서 머리글을 찾고 데이터 행을 모은다.

    Args:
        raw_rows: `(행 번호, 셀 값 목록)`을 차례로 내는 이터레이터.

    Raises:
        HTTPException: 머리글이 없는 경우(422 `meta_import_no_header`),
            데이터 행이 `BULK_META_MAX_ROWS`를 넘는 경우(422 `meta_import_too_many_rows`).
    """
    header = None
    for row_no, cells in raw_rows:
        header = _header_map(cells)
        if header is not None:
            break
        if row_no >= HEADER_SCAN_ROWS:
            break
    if header is None:
        raise _reject("meta_import_no_header")
    known, ignored = header
    rows: list[tuple[int, dict]] = []
    blank_run = 0
    for row_no, cells in raw_rows:
        values = {
            key: cells[i] for i, key in known
            if i < len(cells) and (isinstance(cells[i], Formula) or cell_text(cells[i]) is not None)
        }
        if not values:
            blank_run += 1
            if blank_run >= MAX_BLANK_RUN:
                break
            continue
        blank_run = 0
        if len(rows) >= BULK_META_MAX_ROWS:
            raise _reject("meta_import_too_many_rows")
        rows.append((row_no, values))
    return known, ignored, rows


def _xlsx_rows(data: bytes) -> tuple[str, object]:
    """.xlsx에서 읽을 시트 이름과 `(행 번호, 셀 값 목록)` 이터레이터를 돌려준다.

    `메타` 시트가 있으면 그 시트를, 없으면 안내 시트가 아닌 첫 시트를 읽는다.
    수식 셀은 `Formula`로 바꾼다. 다른 프로그램이 저장한 파일에는 계산 결과가 없을 수 있어서
    계산 결과를 읽지 않는다.

    Raises:
        HTTPException: 압축을 풀 수 없거나 풀린 크기가 `MAX_UNZIPPED_BYTES`를 넘는 경우,
            시트를 읽지 못하는 경우(422 `meta_import_unreadable`).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_BYTES:
                raise _reject("meta_import_unreadable")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except HTTPException:
        raise
    except Exception:   # noqa: BLE001
        raise _reject("meta_import_unreadable") from None
    names = [n for n in wb.sheetnames if n != GUIDE_SHEET_NAME] or wb.sheetnames
    name = SHEET_NAME if SHEET_NAME in wb.sheetnames else names[0]
    ws = wb[name]

    def _gen():
        """시트의 행을 셀 값 목록으로 하나씩 낸다."""
        for row_no, row in enumerate(ws.iter_rows(), start=1):
            out: list[object] = []
            for c in row:
                v = getattr(c, "value", None)
                out.append(Formula(str(v)) if getattr(c, "data_type", None) == "f" else v)
            yield row_no, out
    return name, _gen()


def _decode_csv(data: bytes) -> str:
    """CSV 바이트를 문자열로 바꾼다. UTF-8(BOM 포함)을 먼저 읽고 안 되면 CP949로 읽는다.

    한국어 Excel의 기본 CSV 저장 형식이 CP949라서 UTF-8만 받으면 그 파일이 모두 거절된다.

    Raises:
        HTTPException: 두 인코딩으로 모두 읽지 못하는 경우(422 `meta_import_unreadable`).
    """
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise _reject("meta_import_unreadable")


def read_table(data: bytes, filename: str | None) -> Table:
    """올린 파일(.xlsx·.csv)을 읽어 머리글과 데이터 행으로 나눈다.

    형식은 내용의 첫 바이트로 정한다. 확장자만 보면 이름만 바꾼 파일을 잘못 읽는다.
    옛 Excel 형식(.xls)은 받지 않는다.

    Raises:
        HTTPException: 파일이 `MAX_FILE_BYTES`를 넘는 경우(413 `meta_import_file_too_large`),
            지원하지 않는 형식인 경우(422 `meta_import_unsupported_file`),
            읽지 못하는 경우(422 `meta_import_unreadable`),
            머리글이 없는 경우(422 `meta_import_no_header`),
            같은 필드의 열이 둘 이상인 경우(422 `meta_import_duplicate_column`),
            데이터 행이 많은 경우(422 `meta_import_too_many_rows`).
    """
    if len(data) > MAX_FILE_BYTES:
        raise _reject("meta_import_file_too_large", status.HTTP_413_CONTENT_TOO_LARGE)
    name = (filename or "").lower()
    if data.startswith(b"PK\x03\x04"):
        sheet, raw = _xlsx_rows(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0") or name.endswith((".xls", ".xlsx", ".xlsm")):
        raise _reject("meta_import_unsupported_file")
    elif name.endswith((".csv", ".txt")) or not name:
        text = _decode_csv(data)
        if "\x00" in text:
            raise _reject("meta_import_unreadable")
        reader = csv.reader(io.StringIO(text))
        sheet, raw = None, ((i, list(r)) for i, r in enumerate(reader, start=1))
    else:
        raise _reject("meta_import_unsupported_file")
    try:
        known, ignored, rows = _collect(iter(raw))
    except csv.Error:
        raise _reject("meta_import_unreadable") from None
    return Table(sheet=sheet, columns=[k for _, k in known], ignored=ignored, rows=rows)
