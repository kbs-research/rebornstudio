"""MAIU 외부 API 어댑터의 인터페이스와 클라이언트 선택. reconciler가 이 인터페이스에 의존한다.

`FakeMaiuClient`는 id만 보고 MAIU 상태를 흉내 낸다.
api와 워커가 다른 프로세스여도 같은 결과를 돌려준다.
`naver_client.NaverMaiuClient`는 실제 Naver Cloud MAIU 어댑터이고 `maiu_enabled`일 때 쓴다.

5단계 호출과 반환값은 다음과 같다.
- `register_asset(uri)`: `asset_id`. 잡은 바로 `registering`이 된다.
- `check_registration(asset_id)`: `registered`·`pending`·`failed` 중 하나.
- `submit_analysis(asset_id)`: `analysis_id`. 잡은 바로 `analyzing`이 된다.
- `check_analysis(analysis_id)`: `analyzed`·`pending`·`failed` 중 하나.
- `get_summary(analysis_id)`: segments와 영상 메타. 발화·키워드는 segment 안에 있다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from api_gateway.config import settings
from api_gateway.services import platform_credentials

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from api_gateway.models import AnalysisJob

#: MI가 등록을 받는 영상 키 확장자. 다른 확장자의 키는 형식 오류(400, 코드 10022)로 거절된다.
INPUT_EXTS = frozenset({"mp4", "mkv", "mov", "webm"})


class MaiuClient(Protocol):
    """MAIU 클라이언트가 구현하는 인터페이스."""

    async def dispatch(self, *, request: dict) -> str:
        """`register_asset`을 한 번 부르고 `asset_id`를 돌려준다. `dispatch` 호출부 호환용이다."""

    async def register_asset(self, *, video_uri: str) -> str:
        """영상 URI를 MAIU에 등록하고 `asset_id`를 돌려준다."""
        ...

    async def check_registration(self, asset_id: str) -> str:
        """에셋 등록 상태를 조회한다('registered' | 'pending' | 'failed')."""
        ...

    async def submit_analysis(self, asset_id: str, options: dict | None = None) -> str:
        """등록된 에셋의 분석을 요청하고 `analysis_id`를 돌려준다."""
        ...

    async def check_analysis(self, analysis_id: str) -> str:
        """분석 상태를 조회한다('analyzed' | 'pending' | 'failed')."""
        ...

    async def get_summary(self, analysis_id: str) -> dict:
        """분석 결과 요약 dict를 돌려준다. 발화·키워드·인물은 segment 안에 있다."""
        ...
    # raw 메타데이터 조회. reconciler가 영상·클립의 `extra` 컬럼에 저장할 때 쓴다.
    async def get_import_status(self, asset_id: str) -> dict:
        """import-status 응답의 `result` 전체를 돌려준다(`uploadStatus`, `metaData`)."""
        ...

    async def get_analyze_status(self, asset_id: str) -> dict:
        """analyze-status 응답의 `result` 전체를 돌려준다(`status`, `indexId`)."""
        ...

    async def get_index_detail(self, asset_id: str, index_id: str) -> dict:
        """indexes/{id} 응답의 `result` 전체를 돌려준다(`analysisSetting`, `indexDetails`)."""
        ...

    async def cancel_analysis(self, asset_id: str) -> None:
        """진행 중이거나 실패한 분석 작업을 NCP 쪽에서 정리한다. 재요청 전에 필요하다."""
        ...
    # 장면 요약: VLM이 장면 단위로 만든 자연어 요약.
    async def request_scene_summary(
        self, analysis_id: str, scene_ids: list[int] | None = None
    ) -> str:
        """장면 요약 생성을 요청하고 `sceneSummaryId`를 돌려준다. 기본 대상은 전체 장면이다."""
        ...

    async def check_scene_summary_status(self, analysis_id: str, summary_id: str) -> str:
        """장면 요약 상태를 대문자 문자열로 조회한다."""
        ...

    async def get_scene_summaries(self, analysis_id: str) -> list[dict]:
        """완료된 장면 요약 목록 [{scene,startMs,endMs,description}]을 돌려준다."""
        ...


#: 분석 연동이 꺼진 서버의 점검 결과 문장. 연결 점검과 분석 대상 점검이 같은 문장을 쓴다.
#: 연동이 꺼진 서버는 가짜 클라이언트가 응답하므로 외부에 접속하지 않고 이 결과를 돌려준다.
FAKE_MODE_DETAIL = "분석 연동이 꺼져 있어 시험용 분석 결과를 씁니다"
#: 분석 연동이 꺼진 서버의 점검 안내. 서버 환경 변수 이름은 화면에 싣지 않는다.
FAKE_MODE_HINT = "실제 분석을 쓰려면 최고관리자가 서버에서 켜야 합니다."

#: 가짜 클라이언트가 만드는 에셋 id의 접두어. 이 접두어의 id는 등록이 끝난 에셋이다.
FAKE_ASSET_PREFIX = "fake-asset-"
#: 가짜 클라이언트가 만드는 분석 id의 접두어. 이 접두어의 id는 분석이 끝난 분석이다.
FAKE_ANALYSIS_PREFIX = "fake-analysis-"


def _is_fake_asset(asset_id: str) -> bool:
    """가짜 클라이언트가 발급한 에셋 id이면 참."""
    return str(asset_id).startswith(FAKE_ASSET_PREFIX)


def _is_fake_analysis(analysis_id: str) -> bool:
    """가짜 클라이언트가 발급한 분석 id이면 참. 쌍 형식(`분석:index`)도 받는다."""
    return str(analysis_id).split(":")[0].startswith(FAKE_ANALYSIS_PREFIX)


class FakeMaiuClient:
    """MAIU를 흉내 내는 클라이언트. reconciler 검증과 로컬 개발에 쓴다.

    등록한 에셋은 바로 `registered`가 되고, 요청한 분석은 바로 `analyzed`가 된다.
    상태와 결과는 id의 접두어로 정한다. api가 등록하고 워커가 조회하는 구성에서는 두 프로세스가
    서로 다른 인스턴스를 쓴다. 그래서 메모리에 기록하면 워커가 에셋을 찾지 못해
    잡이 `registering`에 머문다.
    접두어가 없는 id에는 실제 MI에 없는 에셋과 같은 결과를 돌려준다.
    """

    def __init__(self) -> None:
        """인스턴스 상태를 초기화한다. `force_*_fail` 집합으로 실패 경우를 주입할 수 있다.

        `_assets`는 이 인스턴스가 등록한 영상 URI 기록이고 상태 판정에는 쓰지 않는다.
        """
        self._assets: dict[str, dict] = {}
        # 테스트가 실패 경우를 주입하는 집합.
        self.force_register_fail: set[str] = set()  # video_uri
        self.force_analysis_fail: set[str] = set()  # asset_id
        # 워크스페이스가 없는 상태를 흉내 내는 플래그.
        # 진단이 없는 워크스페이스와 읽지 못한 워크스페이스를 구분하는지 테스트할 때 쓴다.
        self.workspace_missing = False
        # 워크스페이스의 연동 버킷 이름. 벤더가 가진 값이라 테스트가 따로 정한다.
        # 서버 설정을 그대로 돌려주면 항상 일치해서 연동 버킷이 어긋난 상태를 테스트할 수 없다.
        self.linked_bucket: str | None = None
        self._projects: list[dict] = []
        self._next_project = 1


    async def get_workspace(self) -> dict:
        """가짜 워크스페이스 정보를 돌려준다. 연동 버킷 값은 `linked_bucket`으로 정한다.

        `workspace_missing`이 참이면 실제 MI의 404처럼 예외를 던진다.
        빈 dict를 돌려주면 없는 워크스페이스와 비어 있는 워크스페이스가 같은 값이 되어
        진단이 둘을 구분하는지 테스트할 수 없다.
        """
        if self.workspace_missing:
            raise RuntimeError("workspace not found (fake 404)")
        # `linked_bucket`이 `None`이면 연동 버킷이 없는 워크스페이스로 보고 빈 문자열을 돌려준다.
        return {"workspaceName": "fake", "bucketName": self.linked_bucket or ""}

    async def list_workspaces(self) -> list[dict]:
        """가짜 워크스페이스 목록을 돌려준다. `workspace_missing`이 참이면 빈 목록이다."""
        return [] if self.workspace_missing else [{"workspaceName": "fake"}]

    async def list_projects(self) -> list[dict]:
        """가짜 프로젝트 목록을 돌려준다."""
        return list(self._projects)

    async def create_project(self, name: str) -> dict:
        """가짜 프로젝트 목록에 프로젝트를 더한다. 테스트가 선택지를 채울 때 쓴다.

        이름이 중복이면 실제 MI(10026)처럼 예외를 던진다.
        """
        if any(p["name"] == name for p in self._projects):
            raise RuntimeError(f"project name duplicated (fake 10026): {name}")
        made = {"projectId": str(self._next_project), "name": name, "isBaseProject": False}
        self._next_project += 1
        self._projects.append(made)
        return made

    async def list_assets(self, *, page_size: int = 1) -> list[dict]:
        """이 인스턴스가 등록한 가짜 에셋 목록의 첫 페이지를 돌려준다."""
        return [{"assetId": k} for k in list(self._assets)[:page_size]]

    async def dispatch(self, *, request: dict) -> str:
        """`register_asset`처럼 `asset_id`를 돌려준다. `dispatch` 호출부와 호환하려고 둔다."""
        uri = request.get("video_uri") or "fake://unknown"
        return await self.register_asset(video_uri=uri)

    async def register_asset(self, *, video_uri: str) -> str:
        """가짜 `asset_id`를 만들어 돌려준다. id는 uuid4라 호출마다 다르다.

        실제 MI처럼 저장소 키의 확장자가 `INPUT_EXTS`에 없으면 형식 오류로 거절한다.
        그래서 확장자 없는 업로드 원본을 보내는 버그가 테스트에서 드러난다.
        """
        if video_uri in self.force_register_fail:
            raise RuntimeError(f"register failed for {video_uri}")
        if video_uri.startswith(("s3://", "ncp://")):
            name = video_uri.rsplit("/", 1)[-1]
            ext = name.rpartition(".")[2].lower() if "." in name else ""
            if ext not in INPUT_EXTS:
                raise RuntimeError(
                    "MAIU POST /import/object-storage → 400 "
                    '{"code":"10022","message":"source 파일 형식을 확인 후 다시 시도해 주세요."}'
                )
        asset_id = f"{FAKE_ASSET_PREFIX}{uuid4().hex[:12]}"
        self._assets[asset_id] = {"uri": video_uri}
        return asset_id

    async def check_registration(self, asset_id: str) -> str:
        """가짜 에셋 id이면 `registered`, 아니면 `unknown`을 돌려준다."""
        return "registered" if _is_fake_asset(asset_id) else "unknown"

    async def submit_analysis(self, asset_id: str, options: dict | None = None) -> str:
        """가짜 `analysis_id`를 만들어 돌려준다. id는 uuid4이고 바로 `analyzed`로 읽힌다."""
        if asset_id in self.force_analysis_fail:
            raise RuntimeError(f"analysis failed for {asset_id}")
        return f"{FAKE_ANALYSIS_PREFIX}{uuid4().hex[:12]}"

    async def check_analysis(self, analysis_id: str) -> str:
        """가짜 분석 id이면 `analyzed`, 아니면 `unknown`을 돌려준다."""
        return "analyzed" if _is_fake_analysis(analysis_id) else "unknown"

    async def get_summary(self, analysis_id: str) -> dict:
        """`naver_client._to_summary`와 같은 키 구조의 고정 요약 dict를 돌려준다.

        Raises:
            RuntimeError: 가짜 클라이언트가 발급한 분석 id가 아닌 경우.
        """
        if not _is_fake_analysis(analysis_id):
            raise RuntimeError(f"unknown analysis_id: {analysis_id}")
        return _fixed_summary()

    async def get_import_status(self, asset_id: str) -> dict:
        """고정된 import-status 응답을 돌려준다(`uploadStatus`는 `COMPLETED`, `metaData` 포함).

        가짜 에셋 id가 아니면 실제 MI에 없는 에셋처럼 빈 dict를 돌려준다.
        """
        if not _is_fake_asset(asset_id):
            return {}
        return {
            "videoId": asset_id, "uploadStatus": "COMPLETED",
            "uploadResult": "업로드 완료",
            "metaData": {
                "resolution": "1280x720", "durationMs": 60_000,
                "size": 1_234_567, "format": "mp4",
            },
        }

    async def get_analyze_status(self, asset_id: str) -> dict:
        """고정된 analyze-status 응답을 돌려준다(`ANALYZE_COMPLETED`, `indexId`는 `None`)."""
        # 가짜 클라이언트는 `submit_analysis`가 이미 `analysis_id`를 정했으므로 `indexId`를 비운다.
        # 그러면 reconciler가 `maiu_analysis_id`를 덮어쓰지 않는다.
        # 쌍 형식 id는 `NaverMaiuClient`만 쓴다.
        return {"status": "ANALYZE_COMPLETED", "analysisStatusMessage": None, "indexId": None}

    async def cancel_analysis(self, asset_id: str) -> None:
        """아무 일도 하지 않는다. 가짜 분석은 정리할 상태가 없다."""
        return None

    # 장면 요약: 결과가 고정되어 있고 바로 완료된다.
    # `get_summary`의 scene 1·2에 대응하는 설명을 돌려준다.

    async def request_scene_summary(
        self, analysis_id: str, scene_ids: list[int] | None = None
    ) -> str:
        """`analysis_id`로 만든 가짜 `sceneSummaryId`를 돌려준다."""
        return f"fake-summary-{analysis_id.split(':')[0]}"

    async def check_scene_summary_status(self, analysis_id: str, summary_id: str) -> str:
        """항상 `SUMMARIZE_COMPLETED`를 돌려준다."""
        return "SUMMARIZE_COMPLETED"

    async def get_scene_summaries(self, analysis_id: str) -> list[dict]:
        """`get_summary`의 scene 1·2에 대응하는 고정 자연어 요약 2건을 돌려준다."""
        return [
            {"scene": 1, "startMs": 0, "endMs": 30_000,
             "description": "앵커가 스튜디오에서 9시 뉴스 오프닝 인사를 전한다."},
            {"scene": 2, "startMs": 30_000, "endMs": 60_000,
             "description": "기자가 현장에서 리포트를 진행하며 상황을 설명한다."},
        ]

    async def get_index_detail(self, asset_id: str, index_id: str) -> dict:
        """`get_summary` 데이터를 raw index 응답 형태로 감싸 돌려준다."""
        # 테스트가 이 응답을 `_to_summary`에 넘기면 `get_summary`와 같은 요약이 나온다.
        s = _fixed_summary()
        return {
            "indexId": int(index_id) if str(index_id).isdigit() else 1001,
            "createdTime": s["index_meta"]["createdTime"],
            "createdUserName": s["index_meta"]["createdUserName"],
            "updatedTime": s["index_meta"]["updatedTime"],
            "updatedUserName": s["index_meta"]["updatedUserName"],
            "analysisSetting": s["analysis_setting"],
            "indexDetails": {
                "durationMs": s["durationMs"], "fps": s["fps"],
                "frameCount": s["frameCount"], "videoUrl": s["videoUrl"],
                "segments": s["segments"],
            },
        }


def _fixed_summary() -> dict:
    """가짜 분석의 고정 요약을 돌려준다. 장면 두 개와 영상 메타를 담는다."""
    # 메타 필드도 `naver_client._to_summary`의 결과처럼 모두 채운다.
    return {
        "segments": [
            {
                "start": 0, "end": 900,
                "startMs": 0, "endMs": 30_000,
                "scene": 1, "shot": 1,
                "sceneThumbnailUrl": "fake://thumb/1.jpg",
                "speeches": [
                    {"id": 1, "startMs": 0, "endMs": 30_000,
                     "text": "안녕하세요 9시 뉴스입니다", "pid": "anchor", "name": "앵커"},
                ],
                "keywords": [
                    {"category": "행동", "group": "방송", "text": "뉴스", "score": 90},
                ],
                "people": [],
            },
            {
                "start": 900, "end": 1800,
                "startMs": 30_000, "endMs": 60_000,
                "scene": 2, "shot": 2,
                "sceneThumbnailUrl": "fake://thumb/2.jpg",
                "speeches": [
                    {"id": 2, "startMs": 30_000, "endMs": 60_000,
                     "text": "현장 리포트입니다", "pid": "reporter1", "name": "기자"},
                ],
                "keywords": [
                    {"category": "장소", "group": "야외", "text": "현장", "score": 80},
                ],
                "people": [],
            },
        ],
        "durationMs": 60_000,
        "fps": 30.0,
        "frameCount": 1800,
        "videoUrl": "fake://video.mp4",
        "indexId": 1001,
        "analysis_setting": {
            "analysisEngine": "Fake v1.0",
            "sceneRange": "MEDIUM",
            "analysisPersonCount": 0,
            "analysisLanguage": "Ko-Ko",
        },
        "index_meta": {
            "createdTime": "2026-01-01T00:00:00",
            "createdUserName": "fake",
            "updatedTime": "2026-01-01T00:00:00",
            "updatedUserName": "fake",
        },
    }


# 주입한 클라이언트 또는 가짜 클라이언트 싱글톤. 테스트는 `set_client`로 바꾼다.
_client_singleton: MaiuClient | None = None
#: 실제 클라이언트를 자격증명·워크스페이스·MI 프로젝트별로 둔 캐시.
_clients: dict[tuple, MaiuClient] = {}
#: 캐시에 두는 클라이언트 수의 상한. 키가 바뀔 때마다 옛 항목이 남으므로 상한을 넘으면 모두 비운다.
_CLIENT_CACHE_MAX = 64
#: 잡 payload 안에서 요청 당시의 MI 선택을 담는 키.
JOB_TARGET_KEY = "maiu"


def get_client(target: platform_credentials.AnalysisTarget | None = None) -> MaiuClient:
    """`maiu_enabled` 설정에 따라 `NaverMaiuClient` 또는 `FakeMaiuClient`를 돌려준다.

    실제 클라이언트의 자격증명은 `platform_credentials.analysis_for(target)`가 준다.
    프로젝트가 고른 MI 연결·워크스페이스·MI 프로젝트가 모두 있어야 한다. 기본값은 없다.
    클라이언트는 자격증명·워크스페이스·MI 프로젝트 묶음별로 캐시한다.
    키를 바꾸면 다음 호출부터 새 클라이언트를 쓴다.
    주입한 클라이언트와 가짜 클라이언트는 `target`과 관계없이 하나를 쓴다.

    Raises:
        platform_credentials.CredentialsMissingError: 분석 연동이 켜져 있는데 MI 선택이 비었거나
            고른 연결을 읽지 못한 경우.
    """
    global _client_singleton
    if getattr(settings, "maiu_enabled", False):
        # 주입한 클라이언트가 있으면 그 클라이언트를 쓴다. 실제 클라이언트는 `_clients`에만 둔다.
        if _client_singleton is not None:
            return _client_singleton
        creds = platform_credentials.analysis_for(target)
        cached = _clients.get(creds.client_key)
        if cached is None:
            # VPC 밖에서 import만으로 실패하지 않게 지연 import한다.
            from api_gateway.services.maiu.naver_client import NaverMaiuClient
            if len(_clients) >= _CLIENT_CACHE_MAX:
                _clients.clear()
            cached = NaverMaiuClient(
                access_key=creds.access_key, secret_key=creds.secret_key,
                base_url=creds.base_url, workspace=creds.workspace, project_id=creds.project_id,
            )
            _clients[creds.client_key] = cached
        return cached
    if _client_singleton is None:
        _client_singleton = FakeMaiuClient()
    return _client_singleton


async def client_for(
    db: AsyncSession, target: platform_credentials.AnalysisTarget | None,
) -> MaiuClient:
    """MI 선택에 맞는 클라이언트를 돌려준다. 고른 연결이 메모리에 없으면 DB에서 먼저 다시 읽는다.

    Raises:
        platform_credentials.CredentialsMissingError: MI 선택이 비었거나 고른 연결을 읽지 못한 경우.
    """
    await platform_credentials.ensure_loaded(db, target)
    return get_client(target)


def target_of_job(job: AnalysisJob) -> platform_credentials.AnalysisTarget | None:
    """잡에 기록한 요청 당시의 MI 선택을 돌려준다. 기록이 없는 잡이면 `None`이다.

    폴링·취소·결과 조회는 이 값으로 클라이언트를 고른다. 프로젝트 설정을 다시 읽으면
    요청 뒤에 선택이 바뀐 잡이 다른 MI 프로젝트에서 에셋을 찾아 404를 받는다.
    기록이 없는 잡은 분석 연동이 켜진 서버에서 `analysis_not_configured` 오류가 난다.
    """
    raw = (job.payload or {}).get(JOB_TARGET_KEY)
    if not isinstance(raw, dict):
        return None
    return platform_credentials.AnalysisTarget(
        connection_id=raw.get("connection_id") or None,
        workspace=raw.get("workspace") or None,
        project_id=raw.get("project_id") or None,
    )


def pinned_target(
    target: platform_credentials.AnalysisTarget | None,
) -> platform_credentials.AnalysisTarget:
    """잡에 기록할 MI 선택을 실제로 쓰는 값으로 채워 돌려준다.

    요청 시점에 실제로 쓴 연결 id·워크스페이스·MI 프로젝트를 남긴다.
    그러면 이후 프로젝트 설정이 바뀌어도 폴링이 같은 MI 프로젝트를 조회한다.
    분석 연동이 꺼져 있거나 자격증명이 없으면 받은 값을 그대로 돌려준다.
    """
    given = target or platform_credentials.AnalysisTarget()
    if not getattr(settings, "maiu_enabled", False):
        return given
    creds = platform_credentials.analysis_for_or_none(target)
    if creds is None:
        return given
    return platform_credentials.AnalysisTarget(
        connection_id=creds.connection_id, workspace=creds.workspace or None,
        project_id=creds.project_id or None,
    )


async def client_for_job(db: AsyncSession, job: AnalysisJob) -> MaiuClient:
    """잡에 기록한 MI 선택으로 클라이언트를 돌려준다.

    Raises:
        platform_credentials.CredentialsMissingError: 기록이 없거나 기록한 연결을 읽지 못한 경우.
    """
    return await client_for(db, target_of_job(job))


def set_client(client: MaiuClient | None) -> None:
    """클라이언트를 주입하고 캐시를 비운다. 테스트가 `force_*_fail`을 쓸 가짜를 넣을 때 부른다."""
    global _client_singleton
    _client_singleton = client
    _clients.clear()
