"""영상 메타 일괄 입력의 표 파일 읽기·쓰기(`media.meta_sheet`) 테스트. DB를 쓰지 않는다."""
import io
import unicodedata
from datetime import UTC, date, datetime

import pytest
from api_gateway.services.media import meta_sheet as ms
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from reborn_contracts import BULK_META_MAX_ROWS


def _xlsx(rows: list[list[object]], *, title: str = "Sheet") -> bytes:
    """행 목록으로 .xlsx 파일을 만든다. 문자열이 `=`로 시작하면 openpyxl이 수식으로 저장한다."""
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _code(exc: pytest.ExceptionInfo) -> tuple[int, str]:
    """`HTTPException`의 상태 코드와 detail을 돌려준다."""
    return exc.value.status_code, exc.value.detail


def test_template_headers_values_and_guide_sheet():
    """양식의 머리글 순서, 채운 값, 방영일의 KST 날짜, 안내 시트를 확인한다."""
    rows = [ms.TemplateRow(
        video_id="11111111-1111-1111-1111-111111111111", file="보도/SAMPLE_0003.mp4", title="SAMPLE_0003",
        broadcaster="샘플 방송", program_name=None, genre=None, episode="001",
        # UTC 15시는 KST로 다음 날 자정이다.
        aired_at=datetime(2026, 5, 27, 15, 0, tzinfo=UTC),
    )]
    wb = load_workbook(io.BytesIO(ms.build_template(rows)))
    assert wb.sheetnames == [ms.SHEET_NAME, ms.GUIDE_SHEET_NAME]
    ws = wb[ms.SHEET_NAME]
    assert [c.value for c in ws[1]] == ["영상 ID", "파일", "제목", "방송사", "프로그램명", "장르", "회차", "방영일"]
    assert [c.value for c in ws[2]] == [
        "11111111-1111-1111-1111-111111111111", "보도/SAMPLE_0003.mp4", "SAMPLE_0003", "샘플 방송", None, None,
        "001", "2026-05-28",
    ]
    assert ws["G2"].data_type == "s", "회차는 문자열로 둔다. 숫자가 되면 001이 1로 바뀐다"


def test_template_keeps_formula_like_title_as_text():
    """수식 시작 글자로 시작하는 제목을 수식이 아닌 문자열로 저장하고 값은 바꾸지 않는지 확인한다.

    수식으로 저장되면 내려받은 사람이 파일을 열 때 실행된다.
    작은따옴표를 값에 붙이면 다시 올릴 때 제목이 바뀐 것으로 읽힌다.
    """
    title = '=HYPERLINK("http://evil.example","x")'
    rows = [ms.TemplateRow("11111111-1111-1111-1111-111111111111", None, title,
                           None, None, None, None, None)]
    data = ms.build_template(rows)
    ws = load_workbook(io.BytesIO(data))[ms.SHEET_NAME]
    assert ws["C2"].value == title and ws["C2"].data_type == "s" and ws["C2"].quotePrefix
    table = ms.read_table(data, "meta.xlsx")
    assert table.rows[0][1]["title"] == title, "다시 읽으면 원래 제목과 같다"


def test_read_table_finds_header_below_title_lines_and_reports_unknown_columns():
    """머리글 위에 제목 줄이 있어도 머리글을 찾고, 모르는 열은 따로 알리는지 확인한다."""
    data = _xlsx([
        ["메타 입력"], [],
        ["영상 ID", "비고", "방송사"],
        ["x", "메모", "샘플 방송"],
        [None, None, None],
        ["y", None, "예시 방송"],
    ])
    t = ms.read_table(data, "a.xlsx")
    assert t.columns == ["video_id", "broadcaster"] and t.ignored == ["비고"]
    assert [r for r, _ in t.rows] == [4, 6], "빈 행은 빼고 행 번호는 시트와 같다"


def test_read_table_marks_formula_cells():
    """사용자가 넣은 수식 셀을 값이 아닌 `Formula`로 읽는지 확인한다."""
    data = _xlsx([["영상 ID", "방송사"], ["x", "=A2&\"샘플 방송\""]])
    cells = ms.read_table(data, "a.xlsx").rows[0][1]
    assert isinstance(cells["broadcaster"], ms.Formula)


def test_read_csv_utf8_bom_and_cp949():
    """CSV를 UTF-8(BOM 포함)과 CP949로 모두 읽는지 확인한다. 한국어 Excel의 기본 CSV가 CP949다."""
    text = "파일,방송사,방송일\nN_1.mp4,샘플 방송,2026. 5. 28.\n"
    for data in (text.encode("utf-8-sig"), text.encode("cp949"), text.encode("utf-8")):
        t = ms.read_table(data, "meta.csv")
        assert t.sheet is None and t.columns == ["file", "broadcaster", "aired_at"]
        assert t.rows == [(2, {"file": "N_1.mp4", "broadcaster": "샘플 방송", "aired_at": "2026. 5. 28."})]


def test_read_table_rejections():
    """크기·형식·머리글·중복 열·손상 파일을 정해진 코드로 거절하는지 확인한다."""
    cases = [
        (b"x" * (ms.MAX_FILE_BYTES + 1), "a.csv", (413, "meta_import_file_too_large")),
        (b"\xd0\xcf\x11\xe0" + b"\0" * 100, "a.xls", (422, "meta_import_unsupported_file")),
        (b"PK\x03\x04broken", "a.xlsx", (422, "meta_import_unreadable")),
        ("방송사,장르\n샘플 방송,보도\n".encode(), "a.csv", (422, "meta_import_no_header")),
        ("제목,title\na,b\n".encode(), "a.csv", (422, "meta_import_duplicate_column")),
        (b"\xff\xfe\x00\xd8", "a.csv", (422, "meta_import_unreadable")),
        (b"data", "a.pdf", (422, "meta_import_unsupported_file")),
    ]
    for data, name, want in cases:
        with pytest.raises(HTTPException) as e:
            ms.read_table(data, name)
        assert _code(e) == want, name


def test_read_table_row_cap():
    """데이터 행이 상한을 넘으면 거절하고, 상한까지는 읽는지 확인한다."""
    ok = "제목\n" + "".join(f"v{i}\n" for i in range(BULK_META_MAX_ROWS))
    assert len(ms.read_table(ok.encode(), "a.csv").rows) == BULK_META_MAX_ROWS
    with pytest.raises(HTTPException) as e:
        ms.read_table((ok + "one-more\n").encode(), "a.csv")
    assert _code(e) == (422, "meta_import_too_many_rows")


@pytest.mark.parametrize("raw", [
    "2026-05-28", "2026.05.28", "2026. 5. 28.", "2026/5/28", "20260528", 20260528,
    datetime(2026, 5, 28, 13, 30), date(2026, 5, 28),
])
def test_parse_aired_formats_to_kst_midnight(raw):
    """받는 방영일 형식이 모두 KST 자정으로 바뀌는지 확인한다. 화면의 `airedAtFromInput`과 같은 시각이다."""
    assert ms.parse_aired(raw).isoformat() == "2026-05-28T00:00:00+09:00"


@pytest.mark.parametrize("raw,msg", [
    ("2026-02-30", "달력에 없는 날짜"),
    ("5월 28일", "날짜 형식이 아닙니다"),
    (ms.Formula("=TODAY()"), "수식"),
])
def test_parse_aired_rejects(raw, msg):
    """날짜로 읽지 못하는 값을 화면에 표시할 문장과 함께 거절하는지 확인한다."""
    with pytest.raises(ValueError, match=msg):
        ms.parse_aired(raw)


def test_cell_text_normalizes():
    """셀 값 변환 규칙(NFC, 공백 제거, 정수형 실수, safe_cell 표시 제거)을 확인한다."""
    nfd = unicodedata.normalize("NFD", "뉴스.mp4")
    assert ms.cell_text(nfd) == "뉴스.mp4"
    assert ms.cell_text("  샘플 방송 ") == "샘플 방송"
    assert ms.cell_text("   ") is None
    assert ms.cell_text(3.0) == "3" and ms.cell_text(12) == "12"
    assert ms.cell_text("'=SUM(A1)") == "=SUM(A1)"
    assert ms.cell_text("'일반") == "'일반"
