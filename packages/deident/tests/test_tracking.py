"""트랙 보간과 linger가 검출이 빠진 프레임을 채우는지 확인한다."""

from collections import defaultdict

from essential_deident import tracking


def test_fill_interpolates_gap_and_lingers():
    """관측 사이 빈 프레임은 선형 보간으로, 마지막 관측 뒤는 linger 수만큼 채운다."""
    hist = {1: {0: (0, 0, 10, 10), 4: (40, 0, 50, 10)}}
    boxes = defaultdict(list)
    added = tracking.fill(boxes, hist, total=20, linger=3)
    assert boxes[2] == [(20.0, 0.0, 30.0, 10.0)]
    assert added == 3 + 3
    assert 8 not in boxes and 7 in boxes


def test_linger_stops_at_last_frame():
    """linger 구간이 영상 끝을 넘으면 마지막 프레임에서 멈춘다."""
    boxes = defaultdict(list)
    tracking.fill(boxes, {1: {8: (0, 0, 10, 10)}}, total=10, linger=5)
    assert sorted(boxes) == [9]


def test_track_links_moving_box_across_missing_frames():
    """한 프레임 빠진 이동 상자가 같은 트랙으로 이어진다."""
    per_frame = [[(10 + 2 * i, 10, 50 + 2 * i, 50, 0.9)] if i != 5 else [] for i in range(12)]
    hist = tracking.track(per_frame, fps=30.0, conf=0.4)
    assert len(hist) == 1
    track_frames = sorted(next(iter(hist.values())))
    assert 5 not in track_frames and track_frames[-1] == 11
