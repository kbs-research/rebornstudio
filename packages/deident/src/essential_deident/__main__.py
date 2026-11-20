"""명령줄 실행. 영상 하나를 비식별화하고 결과 보고를 JSON으로 출력한다.

    python -m essential_deident <원본> <결과.mp4> [--coords <좌표.json>]
"""

import argparse
import json

from .pipeline import anonymize


def main() -> None:
    """인자를 읽어 비식별화를 실행한다."""
    p = argparse.ArgumentParser(prog="essential_deident")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--coords")
    a = p.parse_args()
    print(json.dumps(anonymize(a.src, a.dst, a.coords), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
