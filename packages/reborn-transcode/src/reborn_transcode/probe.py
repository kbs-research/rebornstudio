"""FFprobe로 미디어 메타를 추출한다. MI 요청 전 규격 게이트가 이 모듈을 쓴다.

presigned URL을 직접 probe한다. MI 요청 전에 파일이 규격에 맞는지 적은 비용으로 확인한다.
Naver MI는 시간당 과금이라 깨진 파일·미지원 파일·규격 초과 파일을 분석에 보내면
그 비용을 버린다.

설계 결정:
  - 전체를 다운로드하지 않는다. ffprobe에 presigned URL을 그대로 넘기면 HTTP Range로
    컨테이너 헤더만 읽는다. moov가 파일 끝에 있는 non-faststart mp4도 끝으로 seek해서 읽는다.
    예를 들어 4.4MB 파일이면 `Range: bytes=0-` 다음에 `Range: bytes=4461928-`를 요청한다.
    전체 다운로드는 트랜스코딩에만 필요하다. 트랜스코딩은 transcode.py가 맡는다.
  - subprocess를 직접 호출하고 JSON 출력을 파싱한다(transcode.py와 같은 방식).
    파이썬 래퍼 의존성을 추가하지 않는다.
  - 네트워크 대상이라 timeout이 필수다. 응답하지 않는 URL에 워커가 묶이지 않게 한다.
  - 실패는 ProbeError 하나로 낸다(returncode와 stderr 끝부분, 쿼리스트링을 지운 뒤).
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import TypedDict

from reborn_transcode.input_guard import guard_args, is_playlist

_DEFAULT_FFPROBE = "ffprobe"
_DEFAULT_TIMEOUT = 60

# ffprobe는 실패하면 입력 URL을 stderr에 그대로 출력한다. 그 URL은 presigned라
# `X-Amz-Signature`가 붙어 있고, 호출부는 이 메시지를 DB에 저장해 화면까지 보낸다.
# 서명은 만료 전까지 객체 접근 권한과 같으므로 메시지가 생기는 곳에서 지운다.
# 지우면 메시지도 짧아진다. 서명이 300자를 거의 다 차지하면 실제 실패 사유가 잘린다.
_QUERY_RE = re.compile(r"\?[^\s'\"]*")


def _redact(text: str) -> str:
    """URL 쿼리스트링을 `?…`로 바꾼다. presigned 서명이 유출되지 않게 한다."""
    return _QUERY_RE.sub("?…", text)


class ProbeError(RuntimeError):
    """ffprobe 실패·타임아웃·파싱 불가를 하나로 나타내는 예외."""


class ProbeTimeoutError(ProbeError):
    """ffprobe가 제한 시간 안에 끝나지 않은 경우.

    파일 결함보다 서버 부하로 늦은 경우일 수 있다. 부르는 쪽이 일시 오류로 구분할 수 있게
    별도 예외로 둔다. `ProbeError`를 상속하므로 `ProbeError`만 잡는 호출부도 그대로 동작한다.
    """


class ProbeResult(TypedDict):
    """probe()의 반환값. 규격 검증에 필요한 필드만 평평하게 담는다."""

    duration_seconds: float | None
    size_bytes: int | None
    # 전체 비트레이트(bit/s). 컨테이너 값이 없으면 비디오 스트림 값을 쓴다.
    # 비디오 스트림 값은 오디오를 빼므로 컨테이너 값보다 조금 작다.
    bit_rate: int | None
    # 컨테이너 이름. 정확히는 ffprobe의 디먹서 이름이다. ISO BMFF 계열은 디먹서 하나가
    # MOV·MP4·M4A·3GP를 모두 처리해서 "mov,mp4,m4a,3gp,3g2,mj2"처럼 한꺼번에 나온다.
    # 규격 검사는 허용 목록과 하나라도 겹치면 통과시킨다. 화면은 아래 major_brand를 쓴다.
    format_name: str | None
    # ISO BMFF의 `ftyp` major brand(예: "isom" · "mp42" · "qt  ").
    # 계열 안에서 실제 포맷을 구분하는 유일한 단서다. BMFF가 아닌 컨테이너(mkv 등)에는 없다(None).
    major_brand: str | None
    video_codec: str | None  # 예: "h264", "hevc"
    width: int | None
    height: int | None
    fps: float | None
    # 타임라인 시작 시각(초). 0이 아니면 이후 모든 시각이 그만큼 밀려 있다.
    #
    # 트랜스코딩은 이 오프셋을 0 근처로 정규화한다(예: 원본 1.500초가 마스터에서 0.033초).
    # 분석은 마스터를 읽으므로 클립 타임코드는 마스터 기준이다. 납품은 고객이 가진 원본 기준이다.
    # 두 기준의 차이가 이 값이다. 원본은 지워질 수 있으므로 등록할 때 잰다.
    start_time_seconds: float | None


def build_command(uri: str, *, ffprobe: str = _DEFAULT_FFPROBE) -> list[str]:
    """ffprobe 명령을 만든다. format과 streams만 JSON으로 받는다.

    테스트가 인자를 검증할 수 있게 따로 둔다.
    입력 앞에 프로토콜·디먹서 제한을 둔다(`input_guard`).
    """
    return [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        *guard_args(uri),
        uri,
    ]


def _fps(rate: str | None) -> float | None:
    """ffprobe의 r_frame_rate("30/1")를 float으로 바꾼다.

    분모가 0이거나 파싱에 실패하면 None이다.
    """
    if not rate or "/" not in rate:
        return None
    num, _, den = rate.partition("/")
    try:
        d = float(den)
        return round(float(num) / d, 3) if d else None
    except ValueError:
        return None


def probe(
    uri: str, *, ffprobe: str = _DEFAULT_FFPROBE, timeout: int = _DEFAULT_TIMEOUT,
) -> ProbeResult:
    """로컬 경로 또는 URL(presigned)의 컨테이너 메타를 추출한다.

    URL이면 HTTP Range로 헤더만 읽고 전체를 다운로드하지 않는다(모듈 docstring 참조).

    Args:
        uri: 로컬 파일 경로 또는 http(s) URL. presigned URL도 그대로 받는다.
        timeout: 제한 시간(초). 네트워크 대상이라 필수다. 응답이 없을 때 워커가 묶이지 않게 한다.

    Returns:
        ProbeResult: 없는 필드는 None이다(컨테이너에 그 정보가 없는 경우가 있다).

    Raises:
        ProbeTimeoutError: ffprobe가 제한 시간을 넘긴 경우.
        ProbeError: ffprobe가 실패(rc≠0)한 경우,
            JSON을 읽지 못하거나 비디오 스트림이 없는 경우.
    """
    cmd = build_command(uri, ffprobe=ffprobe)
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ProbeTimeoutError(f"ffprobe timeout after {timeout}s") from e
    except FileNotFoundError as e:  # ffprobe가 없는 이미지(api 등)
        raise ProbeError("ffprobe not found — worker-io 이미지에서 실행해야 한다") from e

    if proc.returncode != 0:
        # 자르기 전에 서명을 지운다. 순서를 바꾸면 서명이 300자를 거의 다 차지해 사유가 잘린다.
        tail = _redact(proc.stderr.decode("utf-8", errors="replace"))[-300:]
        raise ProbeError(f"ffprobe failed (rc={proc.returncode}): {tail}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError("ffprobe output is not valid JSON") from e

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream found")
    if is_playlist(fmt.get("format_name")):
        # 재생목록은 목록에 적힌 다른 주소를 연다. 디먹서 제한을 거쳐도 남은 경우를 여기서 막는다.
        raise ProbeError(f"playlist format not allowed: {fmt.get('format_name')}")

    def _num(v, cast):
        """v를 cast로 바꾼다. 바꿀 수 없으면 None이다."""
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None

    return ProbeResult(
        duration_seconds=_num(fmt.get("duration"), float),
        size_bytes=_num(fmt.get("size"), int),
        bit_rate=_num(fmt.get("bit_rate"), int) or _num(video.get("bit_rate"), int),
        format_name=fmt.get("format_name"),
        major_brand=((fmt.get("tags") or {}).get("major_brand") or "").strip() or None,
        video_codec=video.get("codec_name"),
        width=_num(video.get("width"), int),
        height=_num(video.get("height"), int),
        fps=_fps(video.get("r_frame_rate")),
        # 비디오 스트림 기준이다. 컨테이너(format)의 값은 오디오가 더 이르면 오디오를 따른다.
        # 클립 시각은 화면을 기준으로 매기므로 비디오 스트림의 값을 쓴다.
        start_time_seconds=_num(video.get("start_time"), float),
    )
