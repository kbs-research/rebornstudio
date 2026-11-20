"""클립 썸네일을 영상의 실제 프레임으로 한꺼번에 만든다.

정기 작업(beat `run_pending_clip_thumbnails`, 1분)이 같은 일을 조금씩 하므로 평소에는 필요 없다.
배포 직후처럼 한꺼번에 다시 만들어야 할 때 쓴다.

규칙은 정기 작업과 같은 서비스 함수(`clip_thumbnail.backfill`)가 정한다.

- 비식별화를 쓰는 영상은 비식별화와 재생본 확정이 끝난 뒤 분석본(비식별화본)에서 뽑는다.
  그 전에는 만들지 않고 건너뛴다. 원본에서 뽑으면 가리지 않은 얼굴이 목록에 나간다.
- 비식별화를 쓰지 않는 영상은 원본에서 뽑는다.
- 뽑은 파일의 종류를 `scene_thumbnail_from`에 남긴다.
  원본에서 뽑은 옛 썸네일은 먼저 키를 지워 다시 뽑는다.
- 실패는 정기 작업과 같이 센다(일시 오류는 대기 후 재시도, 영구 오류는 3회째에 확정).
- 정기 작업과 같은 락을 잡는다. 정기 작업이 도는 중이면 잠시 기다렸다 다시 잡는다.

멱등이다. 썸네일이 이미 있으면 건너뛴다(`--force`면 있거나 실패로 확정된 것도 다시 만든다).

실행(.env를 읽어야 하므로 저장소 루트에서 실행한다):
    uv run --package api-gateway python scripts/ops/backfill_clip_thumbnails.py \
        [--force] [--project <프로젝트 ID>]
"""
import argparse
import asyncio
from uuid import UUID

from api_gateway.database import SessionLocal
from api_gateway.services.media import clip_thumbnail

#: 정기 작업이 락을 가지고 있을 때 다시 잡기까지 기다리는 시간(초)과 횟수.
#: 정기 작업 한 번은 시간 예산(40초)에 한 건의 상한을 더한 만큼이라 2분이면 충분하다.
_WAIT_S = 5
_TRIES = 24


def _print_one(clip_id: UUID, ok: bool) -> None:
    """클립 하나의 결과를 찍는다."""
    print(f"  {'ok  ' if ok else 'skip'} clip={clip_id}")


async def main(force: bool, project_id: UUID | None) -> None:
    """정기 작업의 락을 얻을 때까지 기다린 뒤 서비스 함수로 썸네일을 만들고 결과를 찍는다."""
    for _ in range(_TRIES):
        try:
            async with SessionLocal() as db:
                done, not_done = await clip_thumbnail.backfill(
                    db, force=force, project_id=project_id, on_each=_print_one)
            break
        except clip_thumbnail.BusyError as e:
            print(f"  {e} — {_WAIT_S}초 뒤 다시 시도")
            await asyncio.sleep(_WAIT_S)
    else:
        raise SystemExit("정기 작업의 락을 얻지 못했다. 잠시 뒤 다시 실행한다.")
    print(f"\n백필 완료 — 생성 {done} / 생성 안 함 {not_done}"
          " (비식별화본 대기·실패. 사유는 클립의 thumbnail_error)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="썸네일이 있거나 실패로 확정된 클립도 다시 만든다")
    ap.add_argument("--project", type=UUID, default=None, help="이 프로젝트의 클립만 만든다")
    args = ap.parse_args()
    asyncio.run(main(args.force, args.project))
