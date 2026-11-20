"""프로젝트별 캡션 QC 설정(`settings.caption_qc`).

QC 설정은 분석 프리셋과 분리한다. MI로 분석하고 Claude로 QC하는 조합을 지원한다.
`scene_options`와 같은 방식으로 프로젝트 `settings` dict에 저장한다.
"""
from __future__ import annotations

from typing import Final
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.models import Connection
from api_gateway.services.caption_qc import adapters

SETTINGS_KEY: Final = "caption_qc"

# 검사를 적용하지 않는 사유 문장. 러너가 `skipped`에 남기고 화면이 그대로 보여 준다.
#: 비속어 사전이 비어 비속어 단계를 실행하지 않는 경우의 사유.
PROFANITY_EMPTY: Final = "비속어 사전이 비어 있습니다"
#: 클립 길이 기준(최소·최대)이 없어 길이 단계를 실행하지 않는 경우의 사유.
CLIP_LENGTH_UNSET: Final = "클립 길이 기준이 없습니다"
#: 용어집이 비어 모델에 확인할 용어가 없는 경우의 사유.
GLOSSARY_EMPTY: Final = "용어집이 비어 있습니다"
#: 모델 연결을 정하지 않아 규칙 검사만 실행하는 경우의 사유. 실패가 아니라 설정 상태다.
MODEL_UNCONFIGURED: Final = "모델 연결이 없어 규칙 검사만 합니다"
#: 규칙 검사에서 판정에 드는 지적이 나와 AI 검사를 호출하지 않은 경우의 사유.
#: 결과가 이미 보류라서 호출하면 비용만 든다.
RULES_FLAGGED: Final = "규칙 검사에서 지적이 나와 AI 검사를 건너뛰었습니다"
#: AI 검사를 호출했지만 응답을 받지 못한 경우의 사유. 설정 문제가 아니라 장애다.
MODEL_FAILED: Final = "AI 검사를 실행하지 못했습니다"


class CaptionQcSettings(BaseModel):
    """프로젝트의 캡션 QC 설정.

    비워 둔 값은 "정하지 않음"이다(0이나 빈 문자열과 구분한다).
    `connection_name`이 없으면 모델을 호출하지 않고 규칙 검사만 실행하며, 결과에
    `adapter_key="rules"`가 남는다.
    """

    #: QC를 쓰면 True. 꺼져 있으면 아무 검사도 실행하지 않는다.
    #: 설정이 없는 프로젝트도 이 기본값이다.
    enabled: bool = False
    #: 호출할 어댑터(`dummy`·`llm`·`bedrock`). 카탈로그 키와 같아야 한다.
    adapter_key: str = "llm"
    #: 연결을 가리키는 조직 `connections`의 이름. id로 가리키면 연결을 다시 만들 때 설정이 끊긴다.
    connection_name: str | None = None
    #: 검사 항목별 프롬프트 재정의. 비우면 어댑터 기본값을 쓴다.
    #: 프롬프트 지문은 판정과 함께 기록된다.
    prompts: dict[str, str] = Field(default_factory=dict)
    #: 용어·표기 일관성 검사에 쓰는 `{잘못된 표기: 바른 표기}` 사전.
    glossary: dict[str, str] = Field(default_factory=dict)
    #: 비속어 사전. 프로젝트마다 기준이 달라서 코드에 고정하지 않는다.
    profanity: list[str] = Field(default_factory=list)


def of_project(settings: dict | None) -> CaptionQcSettings:
    """프로젝트의 QC 설정을 읽는다. 설정이 없으면 꺼진 기본값을 돌려준다.

    저장된 값이 올바르지 않아도 기본값을 돌려준다. QC 장애가 파이프라인을 막지 않도록
    설정 하나 때문에 제출·승인이 실패하게 하지 않는다.
    """
    raw = (settings or {}).get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return CaptionQcSettings()
    try:
        return CaptionQcSettings(**raw)
    except ValueError:
        return CaptionQcSettings()


async def check_connection_name(
    db: AsyncSession, org_id: UUID, name: str | None, *, current: str | None,
) -> None:
    """저장하려는 모델 연결이 조직에 있고 품질검수에 쓸 수 있는지 확인한다. 설정 저장이 부른다.

    없는 이름이나 모델을 호출할 수 없는 연결(저장소·MI)을 저장하면 이후 모든 AI 검사가 실패한다.
    쓸 수 있는지는 어댑터의 `providers`로 정한다(`adapters.adapter_keys_for`).
    비우는 요청과 현재 저장된 이름을 다시 보내는 요청은 통과시킨다. 저장한 뒤 연결이 삭제됐어도
    다른 설정을 고치는 저장은 성공해야 한다.

    Raises:
        HTTPException: 조직에 그 이름의 연결이 없는 경우(422 connection_not_found),
            품질검수에 쓸 수 없는 서비스의 연결인 경우(422 connection_not_for_caption_qc).
    """
    if not name or name == current:
        return
    provider = await db.scalar(
        select(Connection.provider).where(Connection.org_id == org_id, Connection.name == name)
    )
    if provider is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_found")
    if not adapters.adapter_keys_for(provider):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "connection_not_for_caption_qc")
