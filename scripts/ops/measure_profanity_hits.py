"""비속어 사전이 실제 캡션에서 무엇을 잡는지 센다(측정 도구이며 게이트가 아니다).

`check_profanity`는 부분 문자열 매칭(`w in text`)이다. 짧은 낱말이 다른 낱말 안에서
걸리고, 걸리면 승인 직후 `held`가 되어 납품에서 빠진다. 그래서 사전에는 다른 뜻으로 흔히
쓰이는 낱말을 넣지 않는다. `튀기`(기름에 튀기다) · `새끼`(새끼를 낳다) · `미친`이 그렇다.
`미친`은 대신 `미친놈`을 넣는다.
이 선택은 적은 캡션으로 정한 것이라 캡션이 쌓이면 다시 잰다. 그때마다 임시 스크립트를 쓰면
기준이 매번 달라지므로 이 도구가 한 명령으로 같은 방식으로 센다.

측정 방법: 프로젝트의 비속어 사전을 그 프로젝트의 실제 캡션 전체에 적용해, 낱말별로
몇 문장에 걸리는지 센다. 판정은 `caption_qc.rules.check_profanity`를 그대로 부른다.
여기서 매칭을 다시 구현하면 도구와 검사의 판정이 달라진다.

읽는 법: 코퍼스가 작으면 0은 아직 해당 문장을 만나지 않았다는 뜻일 수 있다.
그래서 출력에 문장 수를 함께 찍는다. 0은 분모(문장 수)와 함께 읽는다.

주의: 인자가 틀리면 이 docstring의 마지막 줄을 사용법으로 출력한다. 마지막 줄은
사용법 줄로 둔다.

    DATABASE_URL=... uv run --package api-gateway python \\
        scripts/ops/measure_profanity_hits.py <project_id>
"""
from __future__ import annotations

import asyncio
import re
import sys

from api_gateway.database import SessionLocal
from api_gateway.models import Project, WorkItem
from api_gateway.services.caption_qc import rules
from api_gateway.services.caption_qc import settings as qc_settings
from sqlalchemy import select


def _sentences(brief: str) -> list[dict]:
    """캡션 한 건을 문장 목록으로 나눈다. 저장된 문장 배열이 없을 때만 쓰는 근사다."""
    return [{"id": f"s{i}", "text": s.strip()}
            for i, s in enumerate(re.split(r"(?<=[.!?])\s+", brief)) if s.strip()]


async def main(project_id: str) -> int:
    """프로젝트 캡션 전체에 비속어 사전을 적용해 낱말별로 걸린 문장 수를 출력한다."""
    async with SessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None:
            print(f"프로젝트를 찾을 수 없습니다: {project_id}")
            return 1
        conf = qc_settings.of_project(project.settings)
        if not conf.profanity:
            print("비속어 사전이 비어 있습니다 — 잴 것이 없습니다.")
            return 0

        rows = (await db.scalars(
            select(WorkItem).where(WorkItem.project_id == project_id)
        )).all()

        caps, sent_total, hits = 0, 0, {}
        for w in rows:
            cap = ((w.payload or {}).get("caption") or {})
            sents = cap.get("brief_sentences") or _sentences(cap.get("brief") or "")
            if not sents:
                continue
            caps += 1
            sent_total += len(sents)
            for f in rules.check_profanity(sents, conf.profanity):
                for word in f["message"].split(":", 1)[-1].split("·"):
                    word = word.strip()
                    if word:
                        hits[word] = hits.get(word, 0) + 1

    print(f"프로젝트 {project.name}")
    print(f"  캡션 {caps}건 · 문장 {sent_total}개 · 사전 {len(conf.profanity)}개")
    if not hits:
        # 0건은 분모(문장 수)와 함께 읽는다. 코퍼스가 작으면 해당 문장이 아직 없었을 수 있다.
        print(f"  걸린 낱말 없음 — 문장 {sent_total}개 기준입니다. "
              "코퍼스가 작으면 '아직 안 만났다'는 뜻입니다.")
        return 0
    print("  걸린 낱말:")
    for word, n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"    {word:12} {n}문장")
    print("\n  ※ 걸린 것이 전부 오탐은 아닙니다. 문장을 열어 보고 판단하세요 — "
          "정당한 인용일 수 있습니다(그래서 판정이 `warn`입니다).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip())
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
