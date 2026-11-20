"""씬 분할 엔진이 경계를 구간으로 바꾸고 묶는 규칙을 확인한다.

계약(어댑터가 결과를 도메인 모델로 옮기는 일)은 api-gateway의 `test_analysis_contract.py`가
확인한다. 여기서는 엔진의 판정을 확인한다. 어디서 끊는지, 읽지 못한 영상과 컷이 없는 영상을
어떻게 구분하는지다.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
from reborn_transcode import scene_split


def _shots(*bounds: float) -> list[dict]:
    """`0, a, b, …` 경계를 샷 목록으로 바꾼다. 병합 테스트를 읽기 쉽게 한다."""
    return [{"start": a, "end": b} for a, b in zip(bounds, bounds[1:], strict=False)]


# ── 컷에서 샷으로 ───────────────────────────────────────────────────────────────


def test_shots_cover_the_whole_video_without_gaps():
    """구간이 틈도 겹침도 없이 영상을 덮는다. 틈이 있으면 그 구간은 라벨링에서 통째로 빠진다."""
    shots = scene_split.to_shots([10.0, 20.0], duration=30.0)
    assert [(s["start"], s["end"]) for s in shots] == [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]


def test_too_short_shots_are_absorbed_not_kept():
    """자막 전환·플래시가 만드는 한 프레임짜리 경계는 앞 샷에 붙인다.

    그대로 두면 라벨러가 열어 볼 수 없을 만큼 짧은 클립이 생긴다.
    """
    shots = scene_split.to_shots([10.0, 10.1, 10.2, 25.0], duration=30.0)
    assert [(s["start"], s["end"]) for s in shots] == [(0.0, 10.0), (10.0, 25.0), (25.0, 30.0)]
    assert all(s["end"] - s["start"] >= scene_split.MIN_SHOT_SEC for s in shots)


def test_trailing_sliver_joins_the_previous_shot():
    """마지막 조각이 너무 짧으면 앞 구간을 끝까지 늘린다. 짧은 꼬리 클립을 만들지 않는다."""
    shots = scene_split.to_shots([10.0, 29.9], duration=30.0)
    assert [(s["start"], s["end"]) for s in shots] == [(0.0, 10.0), (10.0, 30.0)]


def test_out_of_range_and_duplicate_cuts_are_dropped():
    """범위 밖·중복 경계는 버린다. 검출기가 어떤 값을 돌려줘도 구간이 영상을 빈틈없이 덮는다."""
    shots = scene_split.to_shots([-5.0, 0.0, 10.0, 10.0, 30.0, 99.0], duration=30.0)
    assert [(s["start"], s["end"]) for s in shots] == [(0.0, 10.0), (10.0, 30.0)]


def test_no_cuts_means_one_shot_not_zero():
    """경계가 없으면 영상 전체가 한 샷이다.

    빈 목록을 돌려주면 화면이 컷이 없는 영상을 "분석 결과가 없다"로 표시한다.
    """
    assert scene_split.to_shots([], duration=30.0) == [{"start": 0.0, "end": 30.0}]


def test_zero_duration_gives_nothing():
    """영상 길이가 없으면 구간을 만들지 않는다. 길이를 추정하지 않는다."""
    assert scene_split.to_shots([10.0], duration=0.0) == []


# ── 병합 ──────────────────────────────────────────────────────────────────────


def test_silence_chooses_where_to_cut_not_whether_to_cut():
    """무음 구간이 있어도 상한에 닿기 전에는 끊지 않는다.

    이 규칙이 모듈의 핵심이다. 검출된 무음 구간의 80%가 기사 안쪽이라 무음 구간마다 끊으면
    클립이 2~3배로 늘어난다(246개 대 114개).
    """
    # 6초 지점에 무음 구간이 있지만 상한(40초)까지 한참 남아 끊지 않는다.
    segs = scene_split.merge(_shots(0.0, 6.0, 12.0, 18.0, 24.0), [(5.5, 6.5)], duration=24.0)
    assert len(segs) == 1, f"무음 구간에서 바로 잘랐다: {segs}"


def test_silence_wins_the_placement_inside_the_window():
    """끊어야 할 때는 창 안에서 무음 구간에 걸친 경계를 고른다. 그래야 발화를 자르지 않는다.

    상한이 12초면 0~12초 안에서 끊어야 한다. 그 안에 무음 구간이 있으면 마지막 샷 경계
    대신 무음 구간에 걸친 경계를 고른다.
    """
    shots = _shots(0.0, 6.0, 8.0, 10.0, 30.0)
    segs = scene_split.merge(shots, [(5.8, 6.4)], duration=30.0, max_sec=12.0)
    assert segs[0]["end"] == 6.0, f"무음 구간을 경계로 고르지 않았다: {segs}"


def test_the_latest_rest_in_the_window_wins():
    """창 안에 무음 구간에 걸친 경계가 여럿이면 가장 늦은 경계를 고른다.

    길이를 상한에 가깝게 채워야 조각 수가 늘지 않는다.
    """
    shots = _shots(0.0, 6.0, 10.0, 14.0, 40.0)
    segs = scene_split.merge(shots, [(5.9, 6.3), (13.8, 14.4)], duration=40.0, max_sec=16.0)
    assert segs[0]["end"] == 14.0, f"앞쪽 무음 구간을 골랐다: {segs}"


def test_no_rest_in_the_window_falls_back_to_the_last_boundary():
    """무음 구간이 없으면 창의 마지막 샷 경계에서 끊는다. 길이를 상한에 최대한 가깝게 채운다."""
    segs = scene_split.merge(_shots(*[i * 10.0 for i in range(11)]), [], duration=100.0)
    assert len(segs) > 1
    assert segs[0]["start"] == 0.0 and segs[-1]["end"] == 100.0
    assert all(a["end"] == b["start"] for a, b in zip(segs, segs[1:], strict=False))


def test_frequent_silence_no_longer_over_segments():
    """무음 구간이 잦아도 클립 수가 늘지 않는다. 무음 구간은 끊을 위치만 고른다.

    무음 구간마다 끊는 규칙이면 이 입력이 2초 단위로 잘게 나뉜다.
    """
    silences = [(t, t + 0.6) for t in range(2, 40, 2)]   # 2초마다 무음 구간
    dense = scene_split.merge(_shots(*[i * 2.0 for i in range(21)]), silences, duration=40.0)
    sparse = scene_split.merge(_shots(*[i * 2.0 for i in range(21)]), [], duration=40.0)
    assert len(dense) == len(sparse), f"무음 구간이 수를 바꿨다: {len(dense)} vs {len(sparse)}"


def test_a_shot_longer_than_the_cap_is_not_split():
    """샷 하나가 상한보다 길면 그대로 내보낸다. 샷 한가운데를 끊을 근거가 없다.

    측정 영상에도 73초·88초짜리 샷이 있다. 상한은 여러 샷을 얼마나 모을지 정하는 기준이다.
    샷을 자르는 데는 쓰지 않는다.
    """
    segs = scene_split.merge(_shots(0.0, 70.0, 80.0), [], duration=80.0, max_sec=40.0)
    assert segs[0]["end"] == 70.0, f"긴 샷을 쪼갰다: {segs}"


def test_merge_reduces_the_count_by_an_order_of_magnitude():
    """측정 영상에서는 샷 404개가 78개로 줄어든다. 자릿수가 줄지 않으면 병합 결함이다."""
    shots = _shots(*[i * 3.0 for i in range(413)])  # 3초짜리 412개
    segs = scene_split.merge(shots, [], duration=1236.0)
    assert len(segs) < len(shots) / 10, f"{len(shots)}개가 {len(segs)}개로밖에 안 줄었다"


def test_merge_of_a_single_shot_is_that_shot():
    """컷이 없는 영상은 한 구간이다. 병합이 빈 목록을 만들지 않는다."""
    assert scene_split.merge(_shots(0.0, 30.0), [], duration=30.0) == [
        {"start": 0.0, "end": 30.0}
    ]


# ── 명령·파싱 ─────────────────────────────────────────────────────────────────


def test_one_pass_asks_for_both_cuts_and_silence():
    """컷과 무음을 한 번의 디코딩으로 받는다. 따로 재면 원본을 두 번 내려받는다."""
    cmd = scene_split.build_command("http://x/v.mp4")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "select='gt(scene," in graph and "silencedetect" in graph
    assert cmd.count("-i") == 1, "입력이 둘이면 두 번 읽는 것이다"


def test_threads_bind_the_decoder_and_the_filter_graph(monkeypatch):
    """스레드 수를 주면 디코더와 필터 그래프 스레드를 그 수로 묶는다. 0이면 플래그가 없다."""
    cmd = scene_split.build_command("http://x/v.mp4", threads=2)
    assert cmd.index("-threads") < cmd.index("-i") and cmd[cmd.index("-threads") + 1] == "2"
    assert cmd[cmd.index("-filter_complex_threads") + 1] == "2"
    assert "-threads" not in scene_split.build_command("http://x/v.mp4")

    seen: dict = {}
    monkeypatch.setenv("TRANSCODE_THREADS", "3")
    monkeypatch.setattr(scene_split, "detect",
                        lambda uri, **kw: (seen.update(kw), ([], []))[1])
    scene_split.run_job({"input_url": "http://x/v.mp4", "duration_seconds": 10.0})
    assert seen["threads"] == 3, "워커의 TRANSCODE_THREADS를 따르지 않았다"


def test_no_audio_input_drops_the_audio_filter():
    """오디오가 없으면 `[0:a]`를 걸지 않는다. 필터 그래프 구성부터 실패하기 때문이다."""
    cmd = scene_split.build_command("http://x/v.mp4", with_audio=False)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "silencedetect" not in graph
    assert "[0:a]" not in graph


def test_unclosed_silence_is_dropped_not_guessed():
    """마지막 무음이 닫히지 않은 채 끝나면 버린다. 끝을 추정하면 없는 경계에서 끊게 된다."""
    _, silences = scene_split.parse(
        "pts_time:1.5\nsilence_start: 10.0\nsilence_end: 13.0\nsilence_start: 20.0\n"
    )
    assert silences == [(10.0, 13.0)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg 없음")
def test_failure_is_not_an_empty_result():
    """읽지 못한 영상은 컷이 없는 영상과 구분해 실패로 처리한다.

    실패를 무시하면 그 영상 전체가 한 클립이 되어 잘못 잘린 채 넘어간다.
    """
    with pytest.raises(scene_split.SceneSplitError):
        scene_split.detect("/dev/null")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg 없음")
def test_signature_is_redacted_from_errors():
    """실패 메시지에 presigned 서명이 남지 않는다. 서명은 만료 전까지 접근 권한과 같다."""
    with pytest.raises(scene_split.SceneSplitError) as e:
        scene_split.detect("/dev/null?X-Amz-Signature=deadbeef")
    assert "deadbeef" not in str(e.value)


def test_unknown_duration_is_a_permanent_failure():
    """길이를 모르면 재시도해도 바뀌지 않으므로 영구 실패다. 생산자가 채워야 할 값이다."""
    with pytest.raises(scene_split.SceneSplitError) as e:
        scene_split.run_job({"input_url": "x", "duration_seconds": 0})
    assert e.value.transient is False


# ── 실 FFmpeg ─────────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
def test_real_ffmpeg_finds_a_cut_and_a_silence(tmp_path: Path):
    """실제 FFmpeg로 컷과 무음을 한 번에 찾는다. 스텁만으로는 명령줄이 틀려도 드러나지 않는다.

    입력은 소리 있는 검은 화면 3초와 무음 흰 화면 3초다.
    가운데에 확실한 컷이 하나, 뒤쪽에 무음이 하나 있다.
    """
    def _clip(path: Path, color: str, audio: str) -> None:
        """단색 화면과 지정한 오디오로 3초짜리 클립을 만든다."""
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-y",
             "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=3:r=10",
             "-f", "lavfi", "-i", audio, "-pix_fmt", "yuv420p", "-shortest", str(path)],
            capture_output=True, check=True,
        )

    a, b, clip = (tmp_path / n for n in ("a.mp4", "b.mp4", "c.mp4"))
    _clip(a, "black", "sine=frequency=440:duration=3")
    _clip(b, "white", "anullsrc=r=44100:cl=mono:d=3")
    lst = tmp_path / "list.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-y", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(clip)],
        capture_output=True, check=True,
    )

    assert scene_split.has_audio(str(clip)) is True
    cuts, silences = scene_split.detect(str(clip), silence_min_sec=0.5)
    assert any(2.5 <= c <= 3.5 for c in cuts), f"3초 부근 컷을 못 찾았다: {cuts}"
    assert silences, "뒤쪽 무음을 못 찾았다 — 필터가 안 걸렸거나 파싱이 틀렸다"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
def test_real_ffmpeg_video_without_audio_still_splits(tmp_path: Path):
    """오디오가 없으면 상한만으로 샷을 묶는다. 실패로 처리하지 않는다."""
    clip = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=320x240:d=2:r=10", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, check=True,
    )
    assert scene_split.has_audio(str(clip)) is False
    result = scene_split.run_job({"input_url": str(clip), "duration_seconds": 2.0})
    assert result["segments"] == [{"start": 0.0, "end": 2.0}]
    assert result["raw_count"] == 1


def _fake_ffmpeg(monkeypatch, script: str) -> None:
    """검출 명령을 셸 스크립트로 바꾼다. 실제 ffmpeg 없이 실행 시간과 출력을 정한다."""
    monkeypatch.setattr(scene_split, "has_audio", lambda uri, **kw: False)
    monkeypatch.setattr(scene_split, "build_command", lambda uri, **kw: ["sh", "-c", script])


def test_detect_sends_heartbeats_while_ffmpeg_runs(monkeypatch):
    """검출이 도는 동안 주기마다 하트비트를 부르고, 끝나면 출력을 그대로 읽는다.

    하트비트가 없으면 리스가 처리 시간과 무관하게 끝나 정상 잡이 회수된다.
    """
    _fake_ffmpeg(monkeypatch, "sleep 0.5; echo 'pts_time:1.5' >&2")
    beats: list[int] = []
    cuts, silences = scene_split.detect("http://x/v.mp4", on_heartbeat=lambda: beats.append(1),
                                        heartbeat_every_s=0.1)
    assert cuts == [1.5] and silences == []
    assert len(beats) >= 2, f"하트비트가 {len(beats)}번만 나갔다"


def test_detect_timeout_is_transient_and_kills_ffmpeg(monkeypatch):
    """실행 상한을 넘기면 ffmpeg를 끝내고 일시 오류로 보고한다."""
    _fake_ffmpeg(monkeypatch, "sleep 5")
    with pytest.raises(scene_split.SceneSplitError) as e:
        scene_split.detect("http://x/v.mp4", timeout=0.3, on_heartbeat=lambda: None,
                           heartbeat_every_s=0.1)
    assert e.value.transient


def test_run_job_passes_the_execution_cap_and_heartbeat(monkeypatch):
    """페이로드의 실행 상한과 하트비트 간격을 검출에 넘긴다."""
    seen: dict = {}
    monkeypatch.setattr(scene_split, "detect",
                        lambda uri, **kw: (seen.update(kw), ([], []))[1])
    beat = object()
    scene_split.run_job({"input_url": "http://x/v.mp4", "duration_seconds": 10.0,
                         "timeout_s": 120, "heartbeat_every_s": 15}, on_heartbeat=beat)
    assert (seen["timeout"], seen["heartbeat_every_s"], seen["on_heartbeat"]) == (120, 15, beat)
