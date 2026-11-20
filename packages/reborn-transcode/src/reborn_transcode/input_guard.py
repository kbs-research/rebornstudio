"""ffmpeg·ffprobe가 여는 입력을 영상 컨테이너와 정해진 프로토콜로 제한하는 옵션.

입력 파일의 Content-Type이 HLS(`application/vnd.apple.mpegurl`)이거나 경로가 `.m3u8`이면
ffmpeg는 파일을 재생목록으로 읽고 목록에 적힌 주소를 워커 안에서 연다. 버킷에 올린 사람이 주소를
정하므로 워커가 닿는 내부 주소로 요청이 나간다. 프로토콜 제한만으로는 http 주소를 막지 못해서
디먹서도 영상 컨테이너로 제한한다.
"""

#: 네트워크 입력(presigned URL)이 쓰는 프로토콜. 연결 주소가 http인 저장소도 있어 http를 둔다.
NETWORK_PROTOCOLS = "http,https,tls,tcp"
#: 로컬 경로 입력이 쓰는 프로토콜.
LOCAL_PROTOCOLS = "file"
#: 열 수 있는 디먹서. 등록을 받는 확장자(mp4·mov·m4v·mkv·webm·avi·ts·mxf)의 컨테이너다.
#: ffprobe가 돌려주는 이름(`mov,mp4,m4a,…`·`matroska,webm`)은 쉼표 앞 이름 하나로 맞춘다.
MEDIA_DEMUXERS = "mov,matroska,avi,mpegts,mxf"
#: 다른 주소나 파일을 따라 여는 형식. probe 결과가 이 형식이면 거절한다.
PLAYLIST_FORMATS = frozenset({"hls", "applehttp", "concat", "dash"})


def is_network(uri: str) -> bool:
    """입력이 http(s) URL이면 참을 돌려준다."""
    return uri.startswith(("http://", "https://"))


def guard_args(uri: str) -> list[str]:
    """입력(`-i` 또는 ffprobe의 입력 인자) 바로 앞에 둘 제한 옵션을 돌려준다.

    네트워크 입력은 http·https만, 로컬 경로는 file만 연다.
    그래서 네트워크 입력의 재생목록이 로컬 파일을 가리켜도 읽지 않는다.
    """
    protocols = NETWORK_PROTOCOLS if is_network(uri) else LOCAL_PROTOCOLS
    return ["-protocol_whitelist", protocols, "-format_whitelist", MEDIA_DEMUXERS]


def is_playlist(format_name: str | None) -> bool:
    """ffprobe `format_name`이 재생목록 형식이면 참을 돌려준다."""
    names = {n.strip().lower() for n in (format_name or "").split(",") if n.strip()}
    return bool(names & PLAYLIST_FORMATS)
