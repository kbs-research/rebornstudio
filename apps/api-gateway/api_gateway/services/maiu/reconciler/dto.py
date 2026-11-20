"""MI 응답을 클립 입력과 납품 필드 용어로 바꾸는 순수 함수. DB와 외부 호출이 없다."""
from __future__ import annotations


def _group_segments_by_scene(segments: list[dict]) -> list[dict]:
    """MAIU shot segment를 scene 단위로 묶어 clip 입력 dict 목록을 돌려준다.

    같은 `scene`에서 시간상 연속한 shot 구간을 한 그룹으로 묶는다. 입력 순서는 유지한다.
    직전 shot과 scene이 다르면 새 그룹을 시작한다.
    그래서 scene이 1, 2, 1 순서로 떨어져 나타나도 구간마다 독립 clip이 된다.
    clip 시간 범위가 겹치지 않는 성질은 UNIQUE 제약 없이 이 묶음 규칙으로 보장한다.
    scene이 None이거나 없으면 각 shot을 독립 그룹으로 처리한다.

    그룹 dict의 키는 다음과 같다.
    `startMs`·`endMs`는 그룹 안의 최솟값·최댓값이다.
    `speeches`는 shot별 발화를 `startMs` 순으로 합친 목록이다.
    `keywords`는 shot 전체를 합친 뒤 `_merge_keywords`로 중복을 줄인 목록이다.
    `people`은 shot 전체를 합친 목록이다.
    `shots`는 shot별 원본 메타(shot·scene·프레임 범위·썸네일)를 보존한다.
    `thumb`는 값이 있는 첫 shot의 `sceneThumbnailUrl`(대표 이미지)이다.
    """
    groups: list = []
    cur: dict | None = None
    prev_scene = None
    for seg in segments:
        scene = seg.get("scene")
        # 첫 항목이거나 scene이 없거나 직전 scene이 없거나 scene이 바뀐 경우에 새 그룹을 시작한다.
        if cur is None or scene is None or prev_scene is None or scene != prev_scene:
            cur = {"scene": scene, "startMs": None, "endMs": None,
                   "speeches": [], "keywords": [], "people": [], "shots": [], "thumb": None}
            groups.append(cur)
        sms = seg.get("startMs") or 0
        ems = seg.get("endMs") or 0
        cur["startMs"] = sms if cur["startMs"] is None else min(cur["startMs"], sms)
        cur["endMs"] = ems if cur["endMs"] is None else max(cur["endMs"], ems)
        cur["speeches"].extend(seg.get("speeches") or [])
        cur["keywords"].extend(seg.get("keywords") or [])
        cur["people"].extend(seg.get("people") or [])
        if cur["thumb"] is None:
            cur["thumb"] = seg.get("sceneThumbnailUrl")
        cur["shots"].append({
            "shot": seg.get("shot"), "scene": scene,
            "startMs": sms, "endMs": ems,
            "start_frame": seg.get("start"), "end_frame": seg.get("end"),
            "scene_thumbnail_url": seg.get("sceneThumbnailUrl"),
            "people": seg.get("people") or [],
        })
        prev_scene = scene
    for g in groups:
        g["speeches"].sort(key=lambda s: s.get("startMs") or 0)
        g["keywords"] = _merge_keywords(g["keywords"])
    return groups

def _merge_keywords(items: list) -> list:
    """샷마다 검출된 같은 키워드를 하나로 합친다.

    MI는 키워드를 샷 단위로 돌려준다. 한 클립이 여러 샷에 걸치면 같은 낱말이 샷 수만큼 들어온다.
    그러면 라벨러가 키워드를 골라 넣는 화면에 같은 칩이 반복돼 비교하기 어렵다.

    낱말이 같아도 점수는 샷마다 다르다. 점수는 키워드가 얼마나 분명하게 검출됐는지를 나타낸다.
    그래서 점수가 가장 높은 항목을 남긴다.
    임의로 남기면 뒤쪽 샷의 점수가 낮은 검출이 대표 항목이 될 수 있다.

    순서는 처음 나온 위치를 따른다. dict는 삽입 순서를 보존하고, 값을 갱신해도 위치는 바뀌지 않는다.

    Args:
        items: 샷별 키워드를 이어 붙인 목록. dict가 아닌 원소는 예상 밖 형태라서 버린다.

    Returns:
        text·group·category가 같은 것을 하나로 합친 목록.
    """
    best: dict = {}
    for k in items:
        if not isinstance(k, dict):
            continue
        key = (k.get("text"), k.get("group"), k.get("category"))
        cur = best.get(key)
        if cur is None or (k.get("score") or 0) > (cur.get("score") or 0):
            best[key] = k
    return list(best.values())
