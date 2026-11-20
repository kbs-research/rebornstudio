"""앱이 직접 쓰는 저장소 경로와, 그 경로 아래 파일을 원본으로 등록하지 않는 판정.

같은 버킷을 여러 프로젝트가 쓰면 앱 경로 아래에 다른 프로젝트의 산출물이 있다.
원본으로 다시 등록하면 같은 영상이 사본으로 한 번 더 들어가고, 다른 프로젝트의 파일을 읽게 된다.
`work/`의 분석본 마스터는 비식별화 전 파일이다.
등록 API와 폴더 단위 등록 작업이 이 모듈의 같은 함수로 판정한다.
"""

#: 앱이 직접 파일을 쓰는 최상위 경로. 새 산출 경로를 만들면 여기에도 더한다.
#: 경로별로 쓰는 모듈은 transcoded/(media.transcode·deident), clip-thumbs/(media.clip_thumbnail),
#: video-posters/(media.video_thumbnail), _diagnostics/(ops.diagnostics·stacks.project_storage),
#: intake/(media.video 업로드 원본), projects/(exports 납품 패키지), work/(media.transcode 마스터),
#: archive/(media.video 원본 보관)다.
APP_PREFIXES: tuple[str, ...] = (
    "transcoded/", "clip-thumbs/", "video-posters/", "_diagnostics/", "intake/", "projects/",
    "work/", "archive/",
)


def is_app_key(key: str) -> bool:
    """키가 앱 경로 아래에 있거나 앱 경로 폴더 이름과 같으면 참을 돌려준다."""
    k = key.lstrip("/")
    return any(k.startswith(p) or k == p.rstrip("/") for p in APP_PREFIXES)


def prefix_reaches_app(prefix: str) -> bool:
    """폴더 단위로 등록할 prefix의 목록에 앱 경로 아래 키가 섞일 수 있으면 참을 돌려준다.

    목록 조회는 키가 prefix로 시작하는지만 본다. 그래서 prefix가 앱 경로 안에 있을 때뿐 아니라
    앱 경로가 prefix로 시작할 때(`intak`·`t`·빈 prefix인 버킷 루트)도 앱 경로의 키가 나온다.
    """
    p = prefix.lstrip("/")
    return any(p.startswith(a) or a.startswith(p) for a in APP_PREFIXES)
