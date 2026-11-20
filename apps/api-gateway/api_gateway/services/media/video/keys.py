"""영상 원본의 오브젝트 키와 URI 해석."""
from uuid import UUID

from fastapi import HTTPException, status

from api_gateway.models import Video


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key(또는 ncp://)를 (bucket, key)로 나눈다. 형식이 맞지 않으면 409를 낸다."""
    for scheme in ("s3://", "ncp://"):
        if uri.startswith(scheme):
            bucket, _, key = uri[len(scheme):].partition("/")
            if bucket and key:
                return bucket, key
    raise HTTPException(status.HTTP_409_CONFLICT, "invalid_source_uri")


def _upload_key(video: Video) -> str:
    """반입 스테이징 키를 만든다.

    호출자가 키를 정하면 다른 영상의 키를 지정하는 IDOR이 가능해서 서버가 정한다.
    브라우저가 올린 파일은 준비를 거치지 않은 원본이라 `intake/` 프리픽스에 둔다.
    최종 산출과 프리픽스를 나누면 라이프사이클 규칙을 따로 걸 수 있고,
    산출 프리픽스의 파일은 모두 내보내도 된다는 규칙이 유지된다.

    파생물 생성이 끝나면 `retention.dispose_finished_sources`가 프로젝트 정책에 따라
    원본을 보관 프리픽스로 옮기거나 지운다.
    """
    return f"intake/{video.video_id}/source"


def archive_key(video_id: UUID) -> str:
    """원본 보관 키를 만든다. `retain_original`인 프로젝트는 준비가 끝난 원본을 여기로 옮긴다."""
    return f"archive/{video_id}/source"
