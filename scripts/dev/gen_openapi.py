"""FastAPI 앱의 OpenAPI 3.1 명세를 `docs/api/openapi.json`에 쓴다.

`#/components/schemas/` 참조가 모두 실제 스키마를 가리키는지도 확인한다.
깨진 참조가 있으면 종료 코드 1로 끝난다.

실행:
    uv run --package api-gateway python scripts/dev/gen_openapi.py
    # 또는
    make openapi
"""
import json
import os
import sys
from pathlib import Path

# 명세는 실행 환경과 무관하다. `.env`가 없는 체크아웃에서도 개발용 비밀값으로 앱을 import하게 한다.
os.environ.setdefault("ENVIRONMENT", "local")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api-gateway"))

from api_gateway.main import app  # noqa: E402

spec = app.openapi()
spec["openapi"] = "3.1.0"  # FastAPI 기본값이지만 명시적으로 고정한다

out = ROOT / "docs" / "api" / "openapi.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(spec, indent=2, ensure_ascii=False))

paths = spec.get("paths", {})
schemas = spec.get("components", {}).get("schemas", {})

# 기본 검증: 모든 $ref가 실제로 있는 schema를 가리키는지 본다.
def _refs(node):
    """spec 트리를 재귀 순회해 모든 $ref 문자열을 yield한다."""
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            yield node["$ref"]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)

broken = [
    r for r in _refs(spec)
    if r.startswith("#/components/schemas/")
    and r.rsplit("/", 1)[1] not in schemas
]

ops = sum(
    1 for p in paths.values() for v in p
    if v.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
)
print(f"✅ {out.relative_to(ROOT)}")
print(f"   paths:      {len(paths)}")
print(f"   operations: {ops}")
print(f"   schemas:    {len(schemas)}")
if broken:
    print(f"⚠️  깨진 $ref {len(broken)}건: {broken[:5]}")
    sys.exit(1)
