"""납품 스키마 파일에 개발용 설명이 실리지 않는지 보는 테스트."""

import json

from reborn_contracts.clip_metadata import PUBLIC_DESCRIPTIONS, json_schema

#: 받는 쪽이 읽는 글에 있으면 안 되는 표시. PR·기능 번호·라이브러리·코드 이름이 노출될 수 있는 곳이다.
_INTERNAL = ("PR #", "DC-", "F-LBL", "pydantic", "export_package", "extra=", "`")


def _descriptions(node):
    """스키마 안의 모든 description 문자열을 모은다."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "description" and isinstance(v, str):
                yield v
            else:
                yield from _descriptions(v)
    elif isinstance(node, list):
        for v in node:
            yield from _descriptions(v)


def test_schema_descriptions_are_customer_text():
    """스키마의 모든 설명이 공개용 글이고 내부 표시가 없는지 확인한다."""
    texts = list(_descriptions(json_schema()))
    assert texts
    leaked = [t for t in texts for m in _INTERNAL if m in t]
    assert leaked == []


def test_every_model_in_the_schema_has_a_public_description():
    """스키마의 모델마다 공개용 설명이 있는지 확인한다. 모델을 더하고 설명을 빠뜨리면 이 테스트가 실패한다."""
    s = json_schema()
    assert s["description"] == PUBLIC_DESCRIPTIONS["ClipMetadata"]
    for name, sub in s["$defs"].items():
        assert sub["description"] == PUBLIC_DESCRIPTIONS[name]


def test_schema_is_still_serializable():
    """설명을 바꿔도 스키마가 패키지에 쓰는 JSON으로 직렬화되는지 확인한다."""
    json.dumps(json_schema(), ensure_ascii=False)
