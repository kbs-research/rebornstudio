"""`_group_segments_by_scene` 순수 헬퍼의 단위 테스트. DB를 쓰지 않는다."""
from api_gateway.services.maiu.reconciler import _group_segments_by_scene


def _shot(scene, shot, start_ms, end_ms, *, text="", pid="p", thumb=None):
    """테스트용 MAIU shot segment 1건을 만든다."""
    return {
        "scene": scene, "shot": shot,
        "startMs": start_ms, "endMs": end_ms,
        "start": start_ms, "end": end_ms,
        "sceneThumbnailUrl": thumb,
        "speeches": [{"startMs": start_ms, "endMs": end_ms, "text": text, "pid": pid, "name": pid}],
        "keywords": [{"text": f"kw{shot}"}],
        "people": [],
    }


def test_groups_shots_by_scene():
    """shot 4개와 scene 2개는 2개 그룹이 된다. 그룹 경계는 shot의 최소·최대 ms다."""
    segs = [
        _shot(1, 1, 0, 5000, thumb="t1"),
        _shot(1, 2, 5000, 11000, thumb="t2"),
        _shot(2, 3, 11000, 16000, thumb="t3"),
        _shot(2, 4, 16000, 22000, thumb="t4"),
    ]
    groups = _group_segments_by_scene(segs)
    assert len(groups) == 2
    g1, g2 = groups
    assert g1["scene"] == 1 and g1["startMs"] == 0 and g1["endMs"] == 11000
    assert g2["scene"] == 2 and g2["startMs"] == 11000 and g2["endMs"] == 22000
    # shot 원본을 보존하고 대표 썸네일은 첫 shot의 썸네일이다.
    assert [s["shot"] for s in g1["shots"]] == [1, 2]
    assert g1["thumb"] == "t1"
    # keywords를 병합한다.
    assert len(g1["keywords"]) == 2


def test_speeches_merged_and_time_sorted():
    """scene 내 여러 shot의 speeches가 startMs 순으로 병합된다."""
    segs = [
        _shot(1, 2, 5000, 9000, text="b"),
        _shot(1, 1, 0, 5000, text="a"),
    ]
    [g] = _group_segments_by_scene(segs)
    assert [sp["text"] for sp in g["speeches"]] == ["a", "b"]
    assert g["startMs"] == 0 and g["endMs"] == 9000


def test_noncontiguous_scene_splits_into_separate_runs():
    """같은 scene이라도 시간상 끊기면(scene 1, 2, 1 순서) 별도 그룹으로 나눈다.

    연속 구간 단위로 묶으므로 3개 그룹이 되고 인접 clip의 시간 범위가 겹치지 않는다.
    scene 1을 한 그룹으로 합치면 시간 범위 0~15000이 scene 2(5000~10000)와 겹친다.
    """
    segs = [
        _shot(1, 1, 0, 5000, thumb="a"),
        _shot(2, 2, 5000, 10000, thumb="b"),
        _shot(1, 3, 10000, 15000, thumb="c"),  # scene 1 재등장(비연속)
    ]
    groups = _group_segments_by_scene(segs)
    assert len(groups) == 3
    assert [g["scene"] for g in groups] == [1, 2, 1]
    assert (groups[0]["startMs"], groups[0]["endMs"]) == (0, 5000)
    assert (groups[1]["startMs"], groups[1]["endMs"]) == (5000, 10000)
    assert (groups[2]["startMs"], groups[2]["endMs"]) == (10000, 15000)
    # 인접 그룹의 시간 범위가 겹치지 않는다
    for a, b in zip(groups, groups[1:], strict=False):
        assert a["endMs"] <= b["startMs"]


def test_contiguous_same_scene_still_merges():
    """연속된 같은 scene의 shot은 한 그룹으로 합친다."""
    segs = [_shot(3, 1, 0, 4000), _shot(3, 2, 4000, 9000)]
    groups = _group_segments_by_scene(segs)
    assert len(groups) == 1
    assert groups[0]["scene"] == 3 and groups[0]["startMs"] == 0 and groups[0]["endMs"] == 9000


def test_none_scene_keeps_shot_independent():
    """scene이 None이면 각 shot을 독립 그룹으로 둔다(shot 단위 동작)."""
    segs = [_shot(None, 1, 0, 3000), _shot(None, 2, 3000, 6000)]
    groups = _group_segments_by_scene(segs)
    assert len(groups) == 2
    assert all(g["scene"] is None for g in groups)


def test_empty_segments():
    """빈 입력은 빈 목록을 돌려준다."""
    assert _group_segments_by_scene([]) == []


def test_keywords_deduped_keeping_highest_score():
    """같은 키워드가 여러 샷에서 나오면 한 번만 남기고 가장 높은 점수를 쓴다.

    점수는 샷마다 다르므로 가장 높은 점수를 남긴다.
    중복을 남기면 라벨러가 키워드를 고르는 화면이 같은 칩으로 가득 찬다.
    """
    segs = [
        {"scene": 1, "shot": 1, "startMs": 0, "endMs": 10, "speeches": [], "people": [],
         "keywords": [{"text": "사람", "group": "사람", "category": "객체", "score": 70.0},
                      {"text": "유도", "group": "무술", "category": "행위", "score": 60.0}]},
        {"scene": 1, "shot": 2, "startMs": 10, "endMs": 20, "speeches": [], "people": [],
         "keywords": [{"text": "사람", "group": "사람", "category": "객체", "score": 91.5},
                      {"text": "유도", "group": "무술", "category": "행위", "score": 55.0}]},
    ]
    kws = _group_segments_by_scene(segs)[0]["keywords"]
    assert [k["text"] for k in kws] == ["사람", "유도"], "중복이 남았거나 순서가 바뀌었다"
    assert [k["score"] for k in kws] == [91.5, 60.0], "가장 높은 점수를 안 남겼다"


def test_keywords_distinguished_by_group_and_category():
    """글자가 같아도 group·category가 다르면 다른 키워드로 남긴다."""
    segs = [
        {"scene": 1, "shot": 1, "startMs": 0, "endMs": 10, "speeches": [], "people": [],
         "keywords": [{"text": "그래픽", "group": "구성 요소", "category": "기법", "score": 80.0},
                      {"text": "그래픽", "group": "도구", "category": "객체", "score": 70.0}]},
    ]
    assert len(_group_segments_by_scene(segs)[0]["keywords"]) == 2


def test_keywords_ignore_non_dict_items():
    """dict가 아닌 항목이 섞여도 분석 결과 처리가 멈추지 않는다."""
    segs = [
        {"scene": 1, "shot": 1, "startMs": 0, "endMs": 10, "speeches": [], "people": [],
         "keywords": ["문자열", None, {"text": "사람", "group": "사람", "category": "객체", "score": 1.0}]},
    ]
    assert [k["text"] for k in _group_segments_by_scene(segs)[0]["keywords"]] == ["사람"]
