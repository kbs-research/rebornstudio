"""화면이 옮겨 적은 서버 상한이 서버 값과 같은지 대조하는 테스트.

메타 일괄 입력 파일 한도는 OpenAPI 계약에 없는 서버 상수다.
화면은 이 값을 상수로 옮겨 적어 안내 문구와 잘림 표시에 쓴다.
서버 값만 바뀌면 화면은 이전 상한을 안내하고, 상한에 닿은 목록을 잘리지 않은 목록으로 표시한다.
`tsc`와 웹 테스트는 서버 값을 읽지 못하므로 이 테스트가 웹 소스를 읽어 대조한다.
"""
import pathlib
import re

from api_gateway.services.media import meta_sheet
from reborn_contracts import BULK_META_MAX_ROWS

_WEB = pathlib.Path(__file__).resolve().parents[4] / "apps/web/src"


def _ts_const(rel: str, name: str) -> int:
    """웹 소스에서 `const NAME = 식;`의 값을 읽는다.

    식은 숫자 리터럴(`50_000`)과 곱셈(`8 * 1024 * 1024`)만 받는다.

    Args:
        rel: `apps/web/src` 아래 상대 경로.
        name: 상수 이름.

    Returns:
        식을 계산한 정수.

    Raises:
        AssertionError: 선언을 찾지 못한 경우, 또는 식에 숫자와 곱셈 밖의 기호가 있는 경우.
    """
    text = (_WEB / rel).read_text(encoding="utf-8")
    m = re.search(rf"\bconst {name}\s*=\s*([^;]+);", text)
    assert m, f"{rel}에서 {name} 선언을 찾지 못했다. 이름이나 위치가 바뀌었으면 이 테스트도 고친다"
    expr = m.group(1).strip()
    assert re.fullmatch(r"[\d_]+(\s*\*\s*[\d_]+)*", expr), f"{name}의 식을 계산할 수 없다: {expr}"
    value = 1
    for part in expr.split("*"):
        value *= int(part.strip().replace("_", ""))
    return value


def test_meta_import_limits_match_server():
    """메타 일괄 입력 창이 안내하는 파일 크기와 행 수가 서버 상한과 같은지 확인한다."""
    rel = "features/ingest/lib/meta_import.ts"
    assert _ts_const(rel, "META_IMPORT_MAX_BYTES") == meta_sheet.MAX_FILE_BYTES
    assert _ts_const(rel, "META_IMPORT_MAX_ROWS") == BULK_META_MAX_ROWS
