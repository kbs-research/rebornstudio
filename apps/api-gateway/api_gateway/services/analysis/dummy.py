"""더미 분석 어댑터. 계약 테스트가 기준으로 삼는 어댑터다.

- 더미가 곧 명세다. 계약이 코드로 드러나고, 새 어댑터를 "더미와 같은 계약인가"로 검증한다.
- 네트워크와 NCP 자격증명 없이 분석 결과를 만든다.
- 새 분석기를 붙이는 일이 같은 계약 테스트를 통과시키는 일이 된다.

더미가 지키는 조건은 둘이다.

- 결정적이어야 한다. 같은 입력에 같은 세그먼트가 나와야 화면 확인 결과가 매번 같다.
  그래서 난수 대신 `video_id`의 해시를 시드로 쓴다.
- 빈 결과를 만들 수 있어야 한다. 요약이나 음성 인식 결과가 없을 때 화면이 어떻게 그리는지
  확인하려면 그 상태를 재현해야 한다. `EMPTY`가 그 프리셋이다.

구간 길이는 MI 결과에 맞춘다. MI 구간은 보도영상에서 평균 약 60초다.
그래서 기본 더미는 의미 단위 세그먼트를 60초 안팎으로 만든다.
컷 경계(2~5초)는 별도 능력(`SHOT_BOUNDARIES`)이라 재현하지 않는다.
컷 경계로 만들면 더미로 개발한 화면의 구간 수가 실제와 자릿수부터 달라진다.
"""
from __future__ import annotations

import hashlib

from reborn_contracts.analysis import (
    AdapterInfo,
    AnalysisResult,
    Capability,
    Keyword,
    Segment,
    Speech,
)

# 의미 단위 목표 길이(초). MI 구간의 평균 길이에 맞춘다.
_TARGET_SEGMENT_S = 60.0
# 구간 길이에 더할 결정적 편차의 폭(초).
# 모든 구간이 같은 길이면 실제 분포와 달리 화면이 지나치게 고르게 나온다.
_JITTER_S = 20.0

FULL = AdapterInfo(
    key="dummy",
    label="더미 (전 능력)",
    capabilities=frozenset({
        Capability.SEMANTIC_SEGMENTS,
        Capability.SCENE_SUMMARY,
        Capability.SPEECH_TO_TEXT,
        Capability.SPEAKER_DIARIZATION,
        Capability.KEYWORDS,
    }),
)

# 구간만 만드는 스택. 화면이 "없음"을 어떻게 그리는지 확인하는 프리셋.
SEGMENTS_ONLY = AdapterInfo(
    key="dummy-segments-only",
    label="더미 (구간만)",
    capabilities=frozenset({Capability.SEMANTIC_SEGMENTS}),
)

# 결과를 하나도 만들지 않는 스택. 분석을 붙였는데 결과가 비는 경우를 재현한다.
EMPTY = AdapterInfo(key="dummy-empty", label="더미 (빈 결과)", capabilities=frozenset())


def _seed(video_id: str) -> int:
    """`video_id`로 결정적 시드를 만든다. 난수를 쓰면 같은 영상이 매번 다르게 잘린다."""
    return int(hashlib.blake2b(video_id.encode(), digest_size=8).hexdigest(), 16)


def _boundaries(video_id: str, duration: float) -> list[tuple[float, float]]:
    """구간 경계를 만든다. 결과는 결정적이다. 구간은 서로 겹치지 않고 빈틈 없이 이어진다.

    마지막 구간은 영상 끝에 정확히 맞춘다.
    남는 끝부분을 새 구간으로 만들면 1초짜리 세그먼트가 생겨 실제와 분포가 달라진다.
    """
    if duration <= 0:
        return []
    seed = _seed(video_id)
    out: list[tuple[float, float]] = []
    t = 0.0
    i = 0
    while t < duration:
        # 시드로 편차를 정한다. 같은 영상이면 언제 실행해도 같은 값이 나온다.
        jitter = ((seed >> (i % 32)) & 0xFF) / 255.0 * _JITTER_S - _JITTER_S / 2
        end = min(duration, t + _TARGET_SEGMENT_S + jitter)
        # 남는 길이가 목표의 4분의 1보다 짧으면 현재 구간을 영상 끝까지 늘린다.
        if duration - end < _TARGET_SEGMENT_S / 4:
            end = duration
        if end - t < 1.0:
            break
        out.append((round(t, 3), round(end, 3)))
        t = end
        i += 1
    return out


def analyze(info: AdapterInfo, *, video_id: str, duration_seconds: float) -> AnalysisResult:
    """더미 분석을 실행한다. 선언한 능력만 채운다.

    선언하지 않은 능력을 비워 두는 것이 이 어댑터의 목적이다.
    그래서 화면이 "없음"을 어떻게 그리는지 확인할 수 있다.
    계약 테스트가 그 빈 값을 결함으로 세는지 정상 결과로 세는지도 확인할 수 있다.
    """
    segs: list[Segment] = []
    if info.can(Capability.SEMANTIC_SEGMENTS) or info.can(Capability.SHOT_BOUNDARIES):
        wants_summary = info.can(Capability.SCENE_SUMMARY)
        segs = [
            Segment(
                start=a, end=b,
                summary=f"{i + 1}번째 구간 — 더미 요약" if wants_summary else None,
            )
            for i, (a, b) in enumerate(_boundaries(video_id, duration_seconds))
        ]

    speeches: list[Speech] = []
    if info.can(Capability.SPEECH_TO_TEXT):
        diarize = info.can(Capability.SPEAKER_DIARIZATION)
        for i, s in enumerate(segs):
            # 구간마다 발화를 하나 만든다. 구간 경계를 넘는 발화는 시간 축이 틀린 결과라서
            # 발화를 구간 안에 둔다.
            speeches.append(Speech(
                start=s.start, end=min(s.end, s.start + 5.0),
                text=f"더미 발화 {i + 1}",
                speaker_id=f"p{i % 3 + 1}" if diarize else None,
            ))

    keywords: list[Keyword] = []
    if info.can(Capability.KEYWORDS):
        keywords = [Keyword(text=t) for t in ("더미", "키워드", "샘플")]

    return AnalysisResult(segments=segs, speeches=speeches, keywords=keywords)
