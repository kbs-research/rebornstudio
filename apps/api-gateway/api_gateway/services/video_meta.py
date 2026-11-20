"""클립이 물려받는 영상 단위 메타 네 가지를 만드는 모듈.

방송사·프로그램명·장르·방영일은 영상 단위의 사실이다. 운영자가 영상에서 한 번 입력하고,
그 영상의 클립은 모두 같은 값을 쓴다. 라벨러는 이 값을 읽기만 한다.

읽는 곳은 납품 메타(`export_package.build_metadata`), 품질검수의 메타 검사(`caption_qc.runner`),
워크벤치의 읽기 전용 필드(`media.clip.get_one`), 완료 상세(`export_service.get_completed`)다.
읽는 곳마다 영상 열을 따로 고르면 한 곳에서 장르를 빠뜨려도 화면과 납품이 달라진다.
그래서 여기서 한 번 만든다.

마이그레이션 0081이 클립 `payload.meta`의 옛 값을 영상으로 옮겼으므로 클립의 `payload.meta`는
읽지 않는다.
"""
from __future__ import annotations

from typing import Final

from reborn_contracts import VideoMetaDTO

from api_gateway.models import Video

#: 네 값의 키(납품 스키마·영상 열과 같은 이름)와 화면 이름.
#: 이 목록과 품질검수의 메타 검사(`rules`), 화면(`lib/video_meta.ts`)은 같은 집합이어야 한다.
#: 테스트가 셋을 대조한다.
FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("broadcaster", "방송사"),
    ("program_name", "프로그램명"),
    ("genre", "장르"),
    ("aired_at", "방영일"),
)


def _text(v: str | None) -> str | None:
    """앞뒤 공백을 제거한 값을 돌려준다. 빈 값과 공백뿐인 값은 `None`으로 돌려준다."""
    return (v or "").strip() or None


def of_video(v: Video | None) -> VideoMetaDTO:
    """영상에서 클립이 물려받는 메타 네 가지를 만든다. 영상이 없으면 네 값이 모두 `None`이다."""
    if v is None:
        return VideoMetaDTO()
    return VideoMetaDTO(
        broadcaster=_text(v.broadcaster),
        program_name=_text(v.program_name),
        genre=_text(v.genre),
        aired_at=v.aired_at,
    )
