"""연동 진단. 오류 없이 실패하는 설정을 첫 화면에서 찾아낸다.

이 시스템의 고장은 대개 500 오류로 드러나지 않는다. 동작하는 것처럼 보이지만 실제로는 실패한다.
예:

- 등록한 저장소·분석 연결이 없거나 점검에 실패하면 그 연결을 고른 프로젝트의 등록·분석이 멈춘다.
- Redis가 인메모리 폴백으로 바뀌면 레플리카마다 세션이 달라지는데 응답은 계속 200이다.
- 워커가 중단돼 있으면 화면은 "분석 중"으로 정상처럼 보이지만 처리가 진행되지 않는다.
- DB 백업이 멈춰도 서비스는 그대로 돈다. 복원이 필요해진 때에야 백업이 없다는 것을 알게 된다.
- TLS가 없으면 로그인은 되지만 비밀번호가 평문으로 전송된다.

이 모듈은 외부 시스템에 실제로 요청을 보내 비용이 크므로, 주기 태스크
(`worker_io.tasks.check_integrations`)가 부를 때만 돈다. 플랫폼 개요는 그 결과(스냅숏)를 읽고,
요청이 있어야 판정하는 항목만 개요의 요청으로 다시 판정한다(`with_request_checks`).

스토리지·MI·워커를 가로질러 보므로 업무 도메인이 아니라 운영 패키지(`ops`)에 둔다.
가로지르는 import는 도메인 경계 계약(import-linter)의 허용 목록에 `ops.diagnostics` 항목으로 적는다.

판정은 네 가지다.

- `ok`: 실제로 확인했다.
- `warn`: 지금은 동작하지만 운영에서는 문제가 된다(TLS 없음, 인메모리 폴백).
- `fail`: 그대로 두면 기능이 동작하지 않는다.
- `unchecked`: 요청이 있어야 볼 수 있는 항목인데 이어 쓸 판정이 없다(`_carried`).
  이상이 아니라 확인 전 상태이므로 주의로 세지 않고 전이로도 세지 않는다.

확인하다 막힌 항목은 `warn`으로 "확인하지 못했다"고 표시한다.
`ok`는 실제로 확인했을 때만 쓴다.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Request
from reborn_contracts.queues import WORKER_NODE_KEY_PREFIX
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.config import settings
from api_gateway.services import audit, platform_credentials

log = logging.getLogger(__name__)

# 워커 하트비트 키 접두사. 생산자(`worker_io.celery_app`)와 같아야 한다.
# 값은 `<epoch> <주기 초>`다. 주기가 없는 이전 형식 값은 `_BEAT_STALE_S`로만 잰다.
_BEAT_PREFIX = "reborn:beat:"
# 최근 실행으로 보는 시간 창(초). 가장 짧은 주기 태스크(reconciler 5초)보다 충분히 길다.
_BEAT_STALE_S = 300
# 주기 태스크 하나가 자기 주기의 이 배수만큼 성공하지 않으면 실패로 판정한다.
# 태스크마다 자기 주기로 재므로 다른 태스크가 성공해도 한 태스크의 실패가 가려지지 않는다.
# 하루 1회 태스크는 하루가 지나도 정상이고 5초 태스크는 15초만 늦어도 드러난다.
_BEAT_OVERDUE_FACTOR = 3
# 워커 노드 동작 기록. 생산자는 두 Celery 앱의 기동·이벤트 하트비트 신호다
# (`reborn_contracts.queues.WORKER_NODE_KEY_PREFIX`). 30초마다 갱신된다.
_NODE_STALE_S = 120
#: 노드 역할(노드 이름의 `@` 앞부분)과 화면 이름. compose의 `-n` 값과 같아야 한다.
_NODE_ROLES = (("worker", "짧은 작업 워커"), ("qc", "품질검수 워커"), ("gpu", "긴 작업 워커"))
#: 길이를 보여 줄 브로커 큐와 화면 이름. 이름은 `reborn_contracts.queues`의 큐 이름이다.
_QUEUE_LABELS = (("q.transcode", "변환"), ("q.deident", "비식별화"), ("q.qc", "품질검수"),
                 ("q.media", "썸네일·규격 검사"), ("q.maiu", "분석 연동"), ("default", "운영 작업"))
# DB 백업 하트비트 키. 생산자(`infra/backup/backup.sh`)와 같아야 한다.
# 백업은 Celery 태스크가 아니라 워커 판정(`_check_workers`)에서 빼고 따로 판정한다.
_BACKUP_KEY = f"{_BEAT_PREFIX}pg-backup"
# 백업 주기(12시간)를 두 번 놓친 뒤에 실패로 판정한다. 한 번은 일시 오류로 다음 주기에 회복된다.
_BACKUP_STALE_S = 25 * 3600


@dataclass
class Check:
    """진단 결과 한 항목. `detail`은 확인한 내용(측정값)이고 `hint`는 고치는 방법이다."""

    key: str
    label: str
    status: str          # ok | warn | fail | unchecked
    detail: str
    hint: str | None = None


@dataclass
class Diagnostics:
    """진단 결과 전체. 항목(`Check`) 목록을 담는다."""
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """`fail` 판정을 받은 항목 수를 돌려준다."""
        return sum(1 for c in self.checks if c.status == "fail")


# 예외의 화면 사유 변환.
#
# 원문(예외 타입·오류 코드)은 서버 로그에 남기고, 화면에는 무엇이 막혔는지만 한국어로 싣는다.
# 예외 클래스 이름은 운영자가 읽기 어렵고 내부 구현을 드러낸다.

#: 자격증명 거절을 뜻하는 클라우드 오류 코드. S3 호환(AWS·NCP)과 AWS 제어 평면의 코드를 담는다.
#: 서명 불일치(`SignatureDoesNotMatch`·`InvalidSignatureException`)는 키가 틀렸을 때와
#: 서버 시각이 어긋났을 때 모두 나므로 힌트가 두 원인을 모두 안내한다.
CREDENTIAL_REJECTED = frozenset({
    "AccessDenied", "AccessDeniedException", "403", "Forbidden",
    "InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidSignatureException",
    "UnrecognizedClientException", "InvalidClientTokenId", "ExpiredToken",
    "ExpiredTokenException", "AuthFailure", "RequestTimeTooSkewed",
})
#: 접속은 됐지만 요청한 대상이 없음을 뜻하는 오류 코드.
TARGET_MISSING = frozenset({"NoSuchBucket", "404", "NotFound", "ResourceNotFoundException"})


def cloud_error_code(exc: BaseException) -> str | None:
    """botocore `ClientError` 형태의 예외에서 오류 코드를 꺼낸다. 다른 예외면 None을 돌려준다.

    로그와 분기에만 쓴다.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    return str((response.get("Error") or {}).get("Code") or "") or None


def exception_reason(exc: BaseException) -> str:
    """연동 점검에서 난 예외를 카드에 실을 한국어 사유 조각으로 바꾼다. 원문은 싣지 않는다.

    진단(이 모듈)과 자격증명 점검(`services/stacks/connections`)이 함께 쓰므로
    같은 실패를 같은 사유로 표시한다.
    분석 서버(MI) 클라이언트의 예외는 `error_text.operator_reason`에 넘긴다.

    Returns:
        `조회 실패 · {반환값}` 형태로 이어 붙일 조각. 항상 내용이 있는 문자열이다.
    """
    name = type(exc).__name__
    code = cloud_error_code(exc)
    if code is not None:  # 접속은 됐지만 거절된 경우
        if code in CREDENTIAL_REJECTED:
            return "자격증명이 거부됐습니다"
        if code in TARGET_MISSING:
            return "대상을 찾지 못했습니다"
        return "요청이 거절됐습니다"
    if "Timeout" in name:
        return "응답이 없어 시간이 초과됐습니다"
    if name in ("NoCredentialsError", "PartialCredentialsError"):
        return "자격증명이 설정되지 않았습니다"
    text_ = str(exc)
    if "MAIU" in text_ or "Maiu" in name:
        from api_gateway.services.error_text import operator_reason

        return operator_reason(exc)
    if ("Connection" in name or "Connect" in name
            or name in ("OperationalError", "InterfaceError", "gaierror", "OSError")):
        return "엔드포인트에 연결하지 못했습니다"
    return "원인을 읽지 못했습니다(원문은 서버 로그에 있습니다)"


def _why(where: str, exc: BaseException) -> str:
    """예외의 화면 사유(`exception_reason`)를 돌려준다. 화면에서 뺀 원문은 로그에 남긴다."""
    log.warning("연동 진단 %s 실패: %s %s", where, type(exc).__name__, cloud_error_code(exc) or "")
    return exception_reason(exc)


async def _check_db(db: AsyncSession) -> Check:
    """DB 연결을 확인하고 적용된 마이그레이션 버전을 읽는다."""
    try:
        v = await db.scalar(text("SELECT version_num FROM alembic_version"))
        # 개요 머리글과 같은 용어("데이터베이스 버전")를 쓴다.
        # 화면 문구에는 `스키마`를 쓰지 않는다(check-jargon).
        return Check("db", "데이터베이스", "ok", f"연결됨 · 데이터베이스 버전 {v}")
    except Exception as e:  # noqa: BLE001
        return Check("db", "데이터베이스", "fail", _why("db", e),
                     "DATABASE_URL과 데이터베이스 서버 상태를 확인해주세요.")


def _check_redis() -> Check:
    """실제 Redis 서버에 연결됐는지 확인한다. 인메모리 폴백이면 `warn`을 돌려준다."""
    from api_gateway import redis_infra

    client = redis_infra.get_redis()
    if not hasattr(client, "ping"):
        # 인메모리 폴백은 한 프로세스 안에서만 유효하다.
        # 레플리카가 둘이면 세션·rate limit·락이 레플리카마다 따로 저장된다.
        return Check("redis", "Redis", "warn",
                     "Redis 없이 메모리로 동작 중입니다. 세션과 한도가 서버마다 따로 저장됩니다",
                     "REDIS_URL을 확인해주세요. 운영 환경에서는 실제 Redis 서버가 필요합니다.")
    try:
        client.ping()
        # 결과에는 접속 주소를 싣지 않고 연결 결과만 표시한다. 주소는 서버 설정에서 확인한다.
        return Check("redis", "Redis", "ok", "연결됨")
    except Exception as e:  # noqa: BLE001
        return Check("redis", "Redis", "fail", _why("redis", e),
                     "Redis 서버 상태와 REDIS_URL을 확인해주세요.")


#: 저장소 점검 항목의 이름. 화면 용어 표의 용어("저장소")를 쓴다.
_STORAGE_LABEL = "저장소"
#: 분석 점검 항목의 이름.
_ANALYSIS_LABEL = "분석 연결"


def credential_hint(slot: str) -> str:
    """저장소·분석 자격증명 문제를 고칠 곳을 안내한다.

    자격증명의 출처는 프로젝트가 고른 연결 하나다. 그래서 고칠 곳은
    프로젝트 설정의 분석 연동(고른 연결)과 자격증명 연결(그 연결의 키·권한)이다.
    """
    if slot == "storage":
        return "분석 연동에서 고른 저장소 연결의 키와 저장소 권한을 자격증명 연결에서 확인합니다."
    return "분석 연동에서 고른 분석 연결의 키와 워크스페이스를 자격증명 연결에서 확인합니다."


def missing_detail(slot: str) -> str:
    """연결을 고르지 않았거나 고른 연결을 읽지 못했을 때 진단 카드에 쓰는 문장을 만든다."""
    kind = "저장소" if slot == "storage" else "분석"
    return f"{kind} 연결을 고르지 않았거나 읽지 못했습니다"


async def _check_connections(
    db: AsyncSession, *, key: str, label: str, provider: str, noun: str,
) -> Check:
    """등록한 연결의 수와 마지막 점검 결과를 센다. 외부 시스템에 요청하지 않는다.

    실제 접속은 연결 주기 점검(`stacks.connections.recheck_all`)과 프로젝트 대상 점검이 한다.
    여기서 다시 접속하면 같은 판정을 두 곳이 따로 내고,
    어느 프로젝트에도 속하지 않은 버킷에 쓰게 된다.
    """
    from api_gateway.models import Connection

    rows = list((await db.execute(
        select(Connection.last_status).where(Connection.provider == provider)
    )).scalars().all())
    if not rows:
        return Check(key, label, "fail", f"등록한 {noun} 연결이 없습니다",
                     f"자격증명 연결에서 {noun} 연결을 등록합니다.")
    failed = sum(1 for st in rows if st == "fail")
    if failed:
        return Check(key, label, "warn", f"{noun} 연결 {len(rows)}개 중 {failed}개 점검 실패",
                     "자격증명 연결에서 점검 결과를 확인합니다.")
    return Check(key, label, "ok", f"{noun} 연결 {len(rows)}개 · 점검 실패 없음")


async def _check_storage(db: AsyncSession) -> Check:
    """등록한 NCP 저장소 연결을 센다. 프로젝트는 이 중에서 저장소 대상을 고른다."""
    return await _check_connections(
        db, key="storage", label=_STORAGE_LABEL,
        provider=platform_credentials.STORAGE_PROVIDER, noun="저장소")


async def _check_analysis(db: AsyncSession) -> Check:
    """등록한 MI 연결을 센다. 분석 연동이 꺼져 있으면 `warn`으로 표시한다."""
    if not settings.maiu_enabled:
        return Check("maiu", _ANALYSIS_LABEL, "warn",
                     "분석 연동이 꺼져 있어 시험용 분석 결과를 씁니다",
                     "실제 분석을 쓰려면 최고관리자가 서버 설정 MAIU_ENABLED를 켜야 합니다.")
    return await _check_connections(
        db, key="maiu", label=_ANALYSIS_LABEL,
        provider=platform_credentials.ANALYSIS_PROVIDER, noun="분석")


def _parse_beat(raw: str) -> tuple[float, float | None]:
    """성공 기록 값을 `(epoch, 주기 초)`로 읽는다. 주기가 없는 이전 형식이면 주기는 None이다."""
    parts = raw.split()
    return float(parts[0]), (float(parts[1]) if len(parts) > 1 else None)


def _check_workers() -> Check:
    """주기 태스크가 각자 주기 안에 성공하는지 확인한다. 생산자는 `worker_io.celery_app`이다.

    태스크마다 마지막 성공이 자기 주기의 `_BEAT_OVERDUE_FACTOR`배를 넘으면 실패다.
    주기를 모르는 기록(이전 형식)은 늦음을 판정하지 않고 최근 실행 여부에만 센다.
    """
    from api_gateway import redis_infra

    client = redis_infra.get_redis()
    if not hasattr(client, "scan_iter"):
        return Check("workers", "워커", "warn", "Redis가 없어 확인할 수 없습니다")
    now = time.time()
    recent, waiting, overdue, unknown = [], [], [], []
    try:
        for raw in client.scan_iter(match=f"{_BEAT_PREFIX}*"):
            name = raw if isinstance(raw, str) else raw.decode()
            if name == _BACKUP_KEY:
                continue
            v = client.get(name)
            if v is None:
                continue
            at, period = _parse_beat(v if isinstance(v, str) else v.decode())
            short = name[len(_BEAT_PREFIX):].rsplit(".", 1)[-1]
            age = now - at
            if period is not None and age > period * _BEAT_OVERDUE_FACTOR:
                overdue.append(short)
            elif age < _BEAT_STALE_S:
                recent.append(short)
            elif period is not None:
                waiting.append(short)
            else:
                unknown.append(short)
    except Exception as e:  # noqa: BLE001
        return Check("workers", "워커", "warn", f"확인 실패 · {_why('workers', e)}")
    if not (recent or waiting or overdue or unknown):
        # 기동 직후이거나 워커가 실행되지 않은 경우다. 둘을 구분할 수 없어 함께 안내한다.
        return Check("workers", "워커", "warn", "성공 기록이 아직 없습니다",
                     "기동 직후면 잠시 후 다시 확인합니다. "
                     "계속 비어 있으면 워커가 실행되지 않은 것입니다")
    if overdue:
        # 태스크 이름은 운영자가 쓸 정보가 아니라 서버 로그에만 남긴다.
        log.warning("연동 진단 주기 태스크 지연: %s", ", ".join(sorted(overdue)))
        return Check("workers", "워커", "fail",
                     f"예약 작업 {len(overdue)}개가 주기의 {_BEAT_OVERDUE_FACTOR}배가 지나도록 "
                     "성공하지 않았습니다",
                     "워커 로그에서 그 작업의 오류를 확인합니다. 작업 이름은 서버 로그에 있습니다")
    if not recent and not waiting:
        return Check("workers", "워커", "fail",
                     f"{_BEAT_STALE_S}초 안에 성공한 태스크가 하나도 없습니다",
                     "워커가 중단됐을 가능성이 큽니다. 워커가 중단돼도 화면은 정상으로 보입니다")
    # 문구에는 정상인지 읽히도록 결과를 쓴다.
    note = f"예약 작업 {len(recent)}개가 최근 {_BEAT_STALE_S // 60}분 안에 실행됨"
    if waiting:
        note += f" · 주기가 긴 작업 {len(waiting)}개는 다음 실행 대기"
    return Check("workers", "워커", "ok", note)


def _check_worker_nodes() -> Check:
    """워커 세 종류가 각각 동작 중인지 확인한다. 생산자는 두 Celery 앱의 기동·하트비트 신호다.

    주기 태스크 성공 기록은 beat를 품은 짧은 작업 워커만 남긴다. 품질검수 워커와 긴 작업 워커가
    멈춰도 그 기록은 정상이라 따로 본다. 기록이 아직 없는 워커는 주의, 기록이 오래된 워커는 실패다.
    """
    from api_gateway import redis_infra

    label = "워커 프로세스"
    client = redis_infra.get_redis()
    if not hasattr(client, "get"):
        return Check("worker_nodes", label, "warn", "Redis가 없어 확인할 수 없습니다")
    now = time.time()
    stale, missing, alive = [], [], []
    try:
        for role, name in _NODE_ROLES:
            raw = client.get(f"{WORKER_NODE_KEY_PREFIX}{role}")
            if raw is None:
                missing.append(name)
            elif now - float(raw) > _NODE_STALE_S:
                stale.append(name)
            else:
                alive.append(name)
    except Exception as e:  # noqa: BLE001
        return Check("worker_nodes", label, "warn", f"확인 실패 · {_why('worker_nodes', e)}")
    if stale:
        return Check("worker_nodes", label, "fail",
                     f"{'·'.join(stale)}가 {_NODE_STALE_S}초 넘게 응답하지 않았습니다",
                     "그 워커 컨테이너의 상태와 로그를 확인합니다. "
                     "멈춘 워커의 큐는 처리되지 않습니다")
    if missing:
        return Check("worker_nodes", label, "warn", f"{'·'.join(missing)}의 동작 기록이 없습니다",
                     "기동 직후면 잠시 후 다시 확인합니다. "
                     "계속 비어 있으면 그 워커가 실행되지 않은 것입니다")
    return Check("worker_nodes", label, "ok", f"{'·'.join(alive)} 모두 응답합니다")


def _check_queues() -> Check:
    """브로커 큐마다 기다리는 메시지 수를 센다. 판정은 하지 않고 수만 보여 준다.

    처리량보다 투입이 많은 시간대에는 큐가 길어도 정상이라 판정하지 않는다.
    워커가 멈췄을 때 어느 큐에 일이 쌓이는지 같이 보는 용도다.
    받아 가서 처리 중인 메시지는 큐에 없으므로 세지 않는다.
    """
    from api_gateway import redis_infra

    label = "작업 대기열"
    client = redis_infra.get_redis()
    if not hasattr(client, "llen"):
        return Check("queues", label, "warn", "Redis가 없어 확인할 수 없습니다")
    try:
        counts = [(name, int(client.llen(queue))) for queue, name in _QUEUE_LABELS]
    except Exception as e:  # noqa: BLE001
        return Check("queues", label, "warn", f"확인 실패 · {_why('queues', e)}")
    return Check("queues", label, "ok",
                 "대기 중인 작업 · " + " · ".join(f"{name} {n}건" for name, n in counts))


def _check_backup() -> Check:
    """DB 백업이 최근에 성공했는지 확인한다. 하트비트 생산자는 `infra/backup/backup.sh`이다.

    운영은 백업을 늘 켜므로 기록이 없거나 오래됐으면 이상이다.
    개발은 백업을 켜지 않는 것이 기본이라 기록이 없으면 확인하지 않는다.
    """
    from api_gateway import redis_infra

    label = "DB 백업"
    client = redis_infra.get_redis()
    try:
        raw = client.get(_BACKUP_KEY)
    except Exception as e:  # noqa: BLE001
        return Check("backup", label, "warn", f"확인 실패 · {_why('backup', e)}")
    if raw is None:
        if settings.environment in ("local", "test"):
            return Check("backup", label, "ok", "로컬 환경이라 확인하지 않습니다")
        # 기동 직후 첫 백업이 끝나기 전에도 기록이 없다. 그래서 실패가 아니라 주의로 둔다.
        return Check("backup", label, "warn", "백업 성공 기록이 아직 없습니다",
                     "기동하면 첫 백업이 바로 실행됩니다. 몇 분 뒤에도 비어 있으면 "
                     "pg-backup 로그에서 버킷 이름과 키를 확인합니다")
    age_s = time.time() - float(raw)
    age_h = int(age_s // 3600)
    if age_s >= _BACKUP_STALE_S:
        return Check("backup", label, "fail", f"마지막 백업이 {age_h}시간 전입니다",
                     "백업은 12시간마다 실행됩니다. pg-backup 로그에서 실패 사유를 확인합니다")
    return Check("backup", label, "ok", f"마지막 백업 {age_h}시간 전 · 12시간마다 실행")


def _check_tls(scheme: str | None) -> Check:
    """앞단이 TLS를 종료했는지 확인한다.

    `scheme`은 uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`만 반영한 요청 scheme이다.
    헤더 원문을 읽으면 신뢰 대역 밖에서 꾸민 값도 https로 판정한다.
    """
    if settings.environment in ("local", "test"):
        return Check("tls", "TLS", "ok", "로컬 환경이라 확인하지 않습니다")
    if (scheme or "").lower() == "https":
        return Check("tls", "TLS", "ok", "https로 서비스되고 있습니다")
    return Check(
        "tls", "TLS", "warn",
        "http로 접근되고 있습니다. 비밀번호가 암호화되지 않은 채 전송됩니다",
        "앞단(LB 또는 nginx)에서 TLS를 끝내고 X-Forwarded-Proto를 넘깁니다. "
        "web이 그 앞단을 믿도록 WEB_TRUSTED_PROXIES에 주소를 적습니다",
    )


def _check_slot_counter() -> Check:
    """분석 호출 분배 카운터가 공유 백엔드(Redis)를 쓰는지 확인한다.

    `maiu.slot_counter`는 Redis에 연결하지 못하면 알림 없이 프로세스 안 카운터(LRU)로 바뀌고,
    그 카운터는 프로세스가 끝날 때까지 유지된다. 레플리카가 둘이면 정상 건과 재시도 건(10:1)의
    분배를 레플리카마다 따로 센다.

    진단은 상태를 바꾸지 않는다. 이 프로세스에 카운터가 아직 없으면 새로 만들지 않고
    Redis 연결 가능 여부로 판정한다.
    """
    from api_gateway import redis_infra
    from api_gateway.services.maiu import slot_counter

    label = "분석 호출 분배"
    # 읽기만 한다. `get_counter()`를 부르면 이 프로세스에 카운터를 새로 만든다.
    current = slot_counter._counter
    if isinstance(current, slot_counter._LRUSlotCounter):
        return Check("slot_counter", label, "warn", "서버마다 호출 순서를 따로 세고 있습니다",
                     "레플리카가 둘 이상이면 정상 건과 재시도 건의 분배를 레플리카마다 "
                     "따로 셉니다. Redis 연결을 확인하고 워커를 다시 시작해주세요.")
    # 문구에는 구현(카운터) 대신 결과를 쓴다.
    if current is not None:
        return Check("slot_counter", label, "ok", "여러 서버가 호출 순서를 공유합니다")
    if not hasattr(redis_infra.get_redis(), "ping"):
        return Check("slot_counter", label, "warn",
                     "Redis가 없어 처음 호출할 때부터 서버마다 순서를 따로 셉니다",
                     "REDIS_URL을 확인해주세요. 운영 환경에서는 실제 Redis 서버가 필요합니다.")
    return Check("slot_counter", label, "ok",
                 "Redis에 연결할 수 있어 여러 서버가 호출 순서를 공유합니다")


def _is_private(addr: str) -> bool:
    """사설·루프백 주소이면 True를 돌려준다. 주소로 읽을 수 없는 값은 False다."""
    try:
        return ipaddress.ip_address(addr).is_private
    except ValueError:
        return False


def _check_client_ip(request: Request) -> Check:
    """클라이언트 주소를 실제 사용자 주소로 읽는지 확인한다.

    앞단 프록시 주소를 사용자 주소로 읽고 있으면 `warn`을 돌려준다.
    신뢰 목록(`FORWARDED_ALLOW_IPS`)에 앞단이 없으면 모든 요청이 프록시 주소 하나에서 온 것으로 보여
    로그인 한도가 플랫폼 전체를 한 버킷으로 센다.
    플랫폼 개요를 연 사람의 요청으로 판정하고, 주소 값은 개인정보라서 결과에 싣지 않는다.
    """
    label = "클라이언트 주소"
    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",")
            if h.strip()]
    ip = audit.client_ip(request)
    if not hops:
        return Check("client_ip", label, "ok", "프록시 없이 직접 접속")
    hint = ("FORWARDED_ALLOW_IPS에 앞단 프록시 대역을 더합니다. 그러지 않으면 로그인 한도가 "
            "모든 사용자를 한 주소로 셉니다")
    if ip not in hops:
        # 서버가 앞단을 신뢰하지 않아 헤더를 버린 경우다. client 주소가 프록시 자신의 주소로 남는다.
        return Check("client_ip", label, "warn", "모든 요청이 앞단 프록시 주소로 보입니다", hint)
    # 고른 주소가 사설 주소이고 그 왼쪽에 공인 주소가 있으면
    # 신뢰 목록에 없는 LB의 주소를 사용자 주소로 읽은 상태다.
    if _is_private(ip) and any(not _is_private(h) for h in hops[: hops.index(ip)]):
        return Check("client_ip", label, "warn", "앞단 프록시(LB) 주소를 사용자로 읽고 있습니다",
                     hint)
    return Check("client_ip", label, "ok", "프록시가 넘긴 사용자 주소를 읽고 있습니다")


def _carried(key: str, label: str) -> Check:
    """요청이 필요한 항목은 주기 점검에서 마지막 스냅숏의 판정을 이어 쓴다.

    이어 쓸 판정이 없으면 `unchecked`로 표시한다.
    주기 점검에는 요청이 없어 `X-Forwarded-Proto`도 없다. 그 상태로 TLS를 판정하면 주기 점검은
    늘 `warn`, 요청과 함께 한 점검은 `ok`가 되어 실제로 없는 전이가 반복 기록된다.
    """
    prev = next((c for c in (last_snapshot() or {}).get("checks", []) if c.get("key") == key),
                None)
    if prev is None:
        # 이어 쓸 판정이 없는 상태는 확인 전이다. `warn`으로 두면 개요가 늘 주의를 표시한다.
        return Check(key, label, "unchecked", "아직 확인하지 않았습니다",
                     "플랫폼 개요를 열면 그 요청으로 확인합니다. "
                     "15분마다 도는 자동 점검으로는 이 항목을 볼 수 없습니다.")
    return Check(key, label, prev.get("status", "warn"), prev.get("detail", ""),
                 "자동 점검은 이 항목을 볼 수 없어 "
                 "마지막으로 요청과 함께 점검한 결과를 이어 씁니다.")


async def run(db: AsyncSession, *, request: Request | None = None) -> Diagnostics:
    """전체 진단을 돌린다. 한 항목이 실패해도 나머지 항목의 결과를 모두 낸다.

    Args:
        request: 점검을 부른 요청. 없으면(주기 점검) 요청이 필요한 항목
            (클라이언트 주소·TLS)은 마지막 스냅숏의 판정을 이어 쓴다(`_carried`).
    """
    checks = [await _check_db(db), _check_redis(), await _check_storage(db)]
    checks.append(await _check_analysis(db))
    checks.append(_check_workers())
    checks.append(_check_worker_nodes())
    checks.append(_check_queues())
    checks.append(_check_backup())
    checks.append(_check_slot_counter())
    if request is not None:
        checks.extend(request_checks(request))
    else:
        checks.append(_carried("client_ip", "클라이언트 주소"))
        # 로컬은 요청과 관계없이 TLS를 확인하지 않으므로 이어 쓰지 않고 바로 판정한다.
        local = settings.environment in ("local", "test")
        checks.append(_check_tls(None) if local else _carried("tls", "TLS"))
    return Diagnostics(checks=checks)


def request_checks(request: Request) -> list[Check]:
    """요청이 있어야 판정할 수 있는 항목(클라이언트 주소·TLS)을 그 요청으로 판정한다.

    요청 헤더만 읽고 외부 시스템에 요청하지 않는다. 그래서 화면을 열 때마다 불러도 된다.
    """
    # uvicorn이 신뢰 프록시의 `X-Forwarded-Proto`를 반영해 둔 scope의 scheme을 읽는다.
    return [_check_client_ip(request), _check_tls(request.scope.get("scheme"))]


def with_request_checks(checks: list[dict], request: Request) -> list[dict]:
    """스냅숏의 요청 의존 항목을 이 요청의 판정으로 바꾼 목록을 돌려준다. 스냅숏은 바꾸지 않는다.

    주기 점검에는 요청이 없어 클라이언트 주소와 TLS가 확인 전으로 남는다.
    그래서 개요를 연 요청으로 판정한다.
    두 항목은 `fail`이 되지 않으므로 스냅숏의 실패 수(`failed`)는 그대로다.
    """
    live = {c.key: c for c in request_checks(request)}
    out = []
    for c in checks:
        fresh = live.pop(c.get("key"), None)
        out.append(c if fresh is None else
                   {"key": fresh.key, "label": fresh.label, "status": fresh.status,
                    "detail": fresh.detail, "hint": fresh.hint})
    return out


# 주기 점검(`worker_io.tasks.check_integrations`). 화면은 `run()`을 부르지 않으므로
# 주기 점검이 없으면 외부 권한 변경으로 자격증명이 무효가 되어도 드러나지 않는다.
# Redis 스냅숏은 화면이 마지막 점검 결과를 표시하는 데 쓴다.
# domain_events에는 상태가 바뀐 순간만 남긴다. 15분마다 `ok`를 남기면 감사 로그가 채워진다.

_SNAPSHOT_KEY = "reborn:diagnostics:last"
#: 스냅숏이 오래됐다고 보는 기준(초). 이보다 오래되면 화면이 "점검이 멈췄다"고 표시한다
#: (워커가 중단된 경우).
SNAPSHOT_STALE_S = 3600


def _snapshot_of(diag: Diagnostics) -> dict:
    """진단 결과를 스냅숏 dict로 만든다. 화면과 전이 비교가 함께 쓴다.

    `hint`는 전이 비교에 쓰지 않지만 화면이 스냅숏을 그대로 표시하므로 함께 담는다.
    """
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "failed": diag.failed,
        "checks": [
            {"key": c.key, "label": c.label, "status": c.status, "detail": c.detail,
             "hint": c.hint}
            for c in diag.checks
        ],
    }


def last_snapshot() -> dict | None:
    """마지막 점검 결과를 읽는다. 외부 시스템에 요청하지 않는다.

    아직 한 번도 돌지 않았으면 `None`을 돌려준다.
    Redis가 없거나 읽기에 실패해도 `None`을 돌려준다. 여기서 예외를 올리면 화면 전체가 실패한다.
    """
    from api_gateway import redis_infra

    try:
        raw = redis_infra.get_redis().get(_SNAPSHOT_KEY)
    except Exception:  # noqa: BLE001 — 진단을 보는 화면이 진단 때문에 죽으면 안 된다
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except (ValueError, AttributeError):
        return None


async def record(db: AsyncSession, diag: Diagnostics) -> list[dict]:
    """점검 결과를 남기고 상태가 바뀐 항목을 돌려준다. commit은 호출부가 한다.

    전이는 `ok`와 `ok`가 아닌 상태 사이의 변화만 센다.
    `warn`에서 `fail`로 바뀐 경우는 기록하지 않는다.

    Returns:
        `[{key, label, before, after, detail}]`. 바뀐 항목만 담고, 없으면 빈 리스트다.
    """
    from api_gateway import redis_infra

    prev = last_snapshot()
    snap = _snapshot_of(diag)

    try:
        redis_infra.get_redis().set(_SNAPSHOT_KEY, json.dumps(snap, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        # 저장하지 못해도 점검 결과는 유효하다. 다만 이번 회차는 전이를 판정하지 못할 수 있다.
        log.warning("diagnostics snapshot 저장 실패: %s", type(e).__name__)

    if prev is None:
        return []  # 첫 실행은 비교 대상이 없으므로 바뀐 항목 없이 끝낸다.

    before = {c["key"]: c.get("status") for c in prev.get("checks", [])}
    # `unchecked`는 연동 상태가 아니라 점검 기록이 없다는 뜻이므로 전이로 세지 않는다.
    changed = [
        {"key": c["key"], "label": c["label"], "before": before[c["key"]],
         "after": c["status"], "detail": c["detail"]}
        for c in snap["checks"]
        if c["key"] in before
        and "unchecked" not in (before[c["key"]], c["status"])
        and (before[c["key"]] == "ok") != (c["status"] == "ok")
    ]
    if not changed:
        return []

    audit.emit(
        db,
        event_type="system.integration_changed",
        target_kind="system",
        payload={"changed": changed},
        kind="audit",
    )
    return changed


async def run_and_record(
    db: AsyncSession, *, request: Request | None = None,
) -> tuple[Diagnostics, list[dict]]:
    """점검하고 결과를 스냅숏과 전이 기록으로 남긴다. 주기 태스크가 부른다."""
    diag = await run(db, request=request)
    changed = await record(db, diag)
    return diag, changed
