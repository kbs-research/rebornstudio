"""미디어 자산 도메인. 영상·클립·오브젝트 스토리지·트랜스코딩을 다룬다.

`storage`는 NCP 오브젝트 저장소 접근(presign·browse)을 맡는다.
`video`와 `clip`은 자산 메타와 조회를 맡는다.
`transcode`는 ffmpeg 워커 잡을 준비하고 결과를 판정한다.
MI 분석과 클립화는 `services/maiu`가 맡는다.

서브모듈은 직접 import하고 별칭을 붙인다.

    from api_gateway.services.media import video as video_service
    from api_gateway.services.media import storage

주의: 이 파일에서 서브모듈을 미리 import하지 않는다.
`media.video`는 `work.service`를, `work.deident_reports`는 `media.clip`을 import한다.
이 파일이 모든 서브모듈을 불러오면 모듈 사이의 순환이 패키지 순환으로 번져 ImportError가 난다.
"""
