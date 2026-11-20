"""한 핸들러 함수가 한 경로에만 등록되는지 확인하는 테스트.

라우트를 지울 때 여러 줄짜리 데코레이터(`@router.get(...)`)가 남으면 바로 아래 다른 함수에 붙는다.
그러면 그 함수가 엉뚱한 경로에서도 응답하는데, 원래 경로의 테스트는 그대로 통과해서 드러나지 않는다.
"""
from collections import defaultdict

from api_gateway.main import app
from fastapi.routing import APIRoute


def test_each_handler_serves_one_path():
    """핸들러마다 등록된 (경로, 메서드)가 하나뿐이다."""
    seen: dict[tuple[str, str], list[tuple[str, list[str]]]] = defaultdict(list)
    for route in app.routes:
        if isinstance(route, APIRoute):
            key = (route.endpoint.__module__, route.endpoint.__qualname__)
            seen[key].append((route.path, sorted(route.methods)))
    doubled = {f"{m}.{q}": paths for (m, q), paths in seen.items() if len(paths) > 1}
    assert not doubled, f"한 핸들러가 여러 경로에 걸려 있다: {doubled}"
