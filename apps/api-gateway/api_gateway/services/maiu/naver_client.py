"""Naver Cloud Media Intelligence(구 Media AI Understanding)의 실제 어댑터.

reconciler의 `MaiuClient` 인터페이스를 구현한다.
모든 호출에 NCP API Gateway HMAC-SHA256 v2 서명을 붙인다.

API 경로는 신·구 두 가지이고 `index_api`(`settings.maiu_use_index_api`)로 고른다.
신 경로(기본값)는 분석 리소스를 `assets`로, 분석을 `/index`(에셋당 최신 인덱스 1건)로 다룬다.
구 경로는 `video-assets` 리소스와 `/analyze`·`/indexes/{id}`를 쓴다.
구 경로는 `MAIU_USE_INDEX_API=false`로 고르며 MI가 2027년에 폐기할 예정이다.
분기는 경로 빌더(`_asset_seg`, `_analysis_request_path` 등)에 모아 둔다.

기능별 경로는 다음과 같다. 구는 `index_api=False`, 신은 `index_api=True`(기본값)다.
- 등록: 구 `POST …/video-assets/import/object-storage`, 신 `POST …/assets/import/object-storage`
- 등록 상태: 구 `GET …/video-assets/{id}/import-status`, 신 `GET …/assets/{id}/import-status`
- 분석 요청: 구 `POST …/video-assets/{id}/analyze`, 신 `POST …/assets/{id}/index`
- 분석 상태: 구 `GET …/video-assets/{id}/analyze-status`, 신 `GET …/assets/{id}/index/status`
- 인덱스 조회: 구 `GET …/video-assets/{id}/indexes/{idx}`, 신 `GET …/assets/{id}/index`(idx 없음)
- 분석 취소: 구 `DELETE …/video-assets/{id}/analyze`, 신 `POST …/assets/{id}/index/cancel`
- 장면 요약: 구 `…/assets/{id}/indexes/{idx}/scene-summaries`, 신 `…/assets/{id}/scene-summary`
  신 모델의 장면 요약 상태 조회는 `/status` 접미사를 붙인다. 결과는 접미사 없이 기본 경로를 GET한다.
  공개 문서는 결과 경로를 `/result`로 적지만 실제로 동작하는 결과 경로는 기본 경로다.
  문서상 취소 경로는 `/cancel` 접미사다.

상태 판정은 부분 일치로 한다(`classify_stage_status`).
- `uploadStatus`(`UPLOAD_COMPLETED`, `COMPLETED`, `FAILED` 등)는
  `pending`·`registered`·`failed`로 바꾼다.
- `analyzeStatus`(`BEFORE_ANALYZE`, `WAIT_ANALYZE`, `ANALYZING`, `ANALYZE_COMPLETED`,
  `ANALYZE_FAIL`, `ANALYZE_CANCEL`)는 `pending`·`analyzed`·`failed`로 바꾼다.

VPC 전용 API라서 NCP VPC 안에서만 호출할 수 있다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote

import httpx

from api_gateway.config import settings
from api_gateway.services import platform_credentials
from api_gateway.services.transient import TRANSIENT_STATUS

log = logging.getLogger(__name__)

# 완료와 실패로 판정하는 `uploadStatus` 값.
# 문서는 `UPLOAD_COMPLETED`, 실제 응답은 `COMPLETED`라서 둘 다 둔다.
_UPLOAD_OK = {"COMPLETED", "UPLOAD_COMPLETED"}
_UPLOAD_FAIL = {"FAIL", "UPLOAD_FAIL"}
# 진행 중으로 판정하는 `uploadStatus` 값. 실제 응답에서 확인한 값이다.
_UPLOAD_PENDING = frozenset({"UPLOADING"})


def classify_stage_status(
    status: str | None, *, ok: set[str], fail: set[str], pending: frozenset[str] = frozenset(),
) -> str:
    """MAIU 단계 상태 문자열을 `ok`·`failed`·`pending` 중 하나로 판정한다.

    판정은 이 함수에만 둔다. 벤더가 `FAIL`·`FAILED`·`UPLOAD_FAILED`처럼 여러 값을 돌려주므로
    정확한 값 대신 부분 일치로 본다.
    정확한 값만 나열하면 `FAILED`를 놓쳐 실패한 에셋을 계속 폴링한다.
    `pending` 인자는 그 단계에서 진행 중으로 문서화된 값이고, 경고 없이 `pending`으로 판정한다.
    단계마다 값이 달라 호출부가 넘긴다(등록 `_UPLOAD_PENDING`, 분석 `_ANALYZE_PENDING`).
    그 밖의 모르는 값도 `pending`으로 두되 경고 로그를 남긴다. 모르는 값 때문에 계속 기다리는 잡을
    로그로 찾는다.
    """
    s = (status or "").upper()
    if not s:
        return "pending"
    if s in ok:
        return "ok"
    if s in fail or "FAIL" in s or "CANCEL" in s:
        return "failed"
    if "COMPLET" in s or s == "DONE":
        return "ok"
    # 벤더가 돌려주는 진행 중 상태다. 경고 없이 `pending`으로 둔다.
    # 경고를 남기면 폴링 주기(5초)마다 불필요한 경고가 쌓인다.
    if s in pending:
        return "pending"
    log.warning("MAIU 미지 상태값 '%s' — pending으로 둔다(어휘 갱신 필요)", s)
    return "pending"
# 완료와 실패로 판정하는 `analyzeStatus` 값.
_ANALYZE_OK = {"COMPLETED", "ANALYZE_COMPLETED"}
_ANALYZE_FAIL = {"FAIL", "ANALYZE_FAIL", "CANCELED", "ANALYZE_CANCEL"}
# 진행 중으로 판정하는 `analyzeStatus` 값.
# 분석 요청 직후 `BEFORE_ANALYZE`·`WAIT_ANALYZE`를 거쳐 `ANALYZING`이 된다.
_ANALYZE_PENDING = frozenset({"BEFORE_ANALYZE", "WAIT_ANALYZE", "ANALYZING"})
# 완료와 실패로 판정하는 장면 요약 상태 값.
_SUMMARY_OK = {"SUMMARIZE_COMPLETED", "COMPLETED"}
_SUMMARY_FAIL = {"SUMMARIZE_FAIL", "FAIL"}
# 진행 중으로 판정하는 장면 요약 상태 값.
# 요약 요청 직후 `BEFORE`·`WAIT_SUMMARIZE`를 거쳐 `SUMMARIZING`이 된다.
_SUMMARY_PENDING = frozenset({"BEFORE", "BEFORE_SUMMARIZE", "WAIT_SUMMARIZE", "SUMMARIZING"})

#: 재시도 대상 HTTP 상태 코드(429 스로틀, 408·425 타이밍, 5xx 서버 오류).
#: 판정 기준은 `services/transient.py` 한 곳에 둔다.
_TRANSIENT_STATUS = TRANSIENT_STATUS


#: 목록 API 한 페이지의 항목 수. MI 목록 API는 1~1,000을 받는다.
_LIST_PAGE_SIZE = 100
#: 목록 API를 읽는 최대 페이지 수. 100페이지면 항목 1만 개다.
_LIST_MAX_PAGES = 100


#: 한 reconciler 사이클이 같이 쓰는 httpx 클라이언트. 사이클 밖에서는 None이다.
_shared_http: ContextVar[httpx.AsyncClient | None] = ContextVar("maiu_shared_http", default=None)


@asynccontextmanager
async def shared_session() -> AsyncIterator[None]:
    """블록 안의 MI 요청이 httpx 클라이언트 하나를 같이 쓰게 하고, 블록이 끝나면 닫는다.

    reconciler 사이클(`reconciler.jobs.run_once`)이 감싼다. 요청마다 클라이언트를 만들면
    요청마다 TLS 연결을 새로 맺는다. 사이클 하나가 수십 건을 조회하므로 연결 비용이 그만큼 쌓인다.
    클라이언트는 사이클마다 새로 만든다. 워커는 태스크마다 새 이벤트 루프에서 돌고 httpx 연결은
    자기를 만든 루프에 묶이므로, 사이클을 넘겨 쓰면 다음 루프에서 실패한다.
    이미 공유 클라이언트가 있으면 그 클라이언트를 그대로 쓴다.
    """
    if _shared_http.get() is not None:
        yield
        return
    async with httpx.AsyncClient(timeout=settings.maiu_http_timeout) as cli:
        token = _shared_http.set(cli)
        try:
            yield
        finally:
            _shared_http.reset(token)


def _retry_after_s(value: str | None) -> int | None:
    """Retry-After 헤더의 초 값을 읽는다. 날짜 형식처럼 초로 읽을 수 없으면 None이다."""
    try:
        return int(str(value).strip()) if value is not None else None
    except ValueError:
        return None


class MaiuTransientError(RuntimeError):
    """재시도하면 될 일시 오류. 호출자는 이 오류로 작업을 실패로 확정하지 않는다.

    폴링은 여러 번 호출하므로 정상 경로에서도 스로틀과 타임아웃이 난다.
    이 오류를 분석 실패로 처리하면 오래 걸리는 영상일수록 실패로 끝난다.
    일반 `RuntimeError`는 상태 코드를 메시지 문자열에만 담아 호출자가 일시 오류를 구분할 수 없다.
    그래서 별도 타입을 둔다.
    """

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None,
    ) -> None:
        """메시지와 HTTP 상태 코드, 응답의 Retry-After 초(있으면)를 받는다."""
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NaverMaiuClient:
    """HMAC-SHA256 v2 서명을 붙이는 NCP MAIU REST 클라이언트."""

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        workspace: str,
        project_id: str,
        base_url: str | None = None,
        default_bucket: str | None = None,
        timeout: float | None = None,
        use_index_api: bool | None = None,
    ) -> None:
        """NCP 자격증명과 MAIU 엔드포인트를 설정한다. 필수 값이 없으면 RuntimeError를 낸다.

        자격증명·워크스페이스·분석 프로젝트는 호출부가 고른 연결에서 읽어 넘긴다
        (`platform_credentials.analysis_for`). 서버 환경 설정에서 채우지 않는다.
        `base_url`을 비우면 NCP 공용 주소를 쓴다. `default_bucket`은 버킷이 없는 URI에만 쓴다.

        `use_index_api`는 신 경로(`assets/{id}/index`)와 구 경로(`video-assets`) 중 하나를 고른다.
        `None`이면 `settings.maiu_use_index_api`를 따른다.
        테스트는 두 모드를 명시적으로 넘겨 경로를 고정한다.
        """
        self.index_api = (
            settings.maiu_use_index_api if use_index_api is None else use_index_api
        )
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = (base_url or platform_credentials.DEFAULT_MI_BASE_URL).rstrip("/")
        self.workspace = workspace
        self.project_id = project_id
        # 버킷이 없는 URI에 쓰는 값. 실제로는 resolver가 항상 `s3://버킷/키`를 준다.
        self.default_bucket = default_bucket or ""
        self.timeout = timeout or settings.maiu_http_timeout
        for name, val in (
            ("access_key", self.access_key),
            ("secret_key", self.secret_key),
            ("workspace", self.workspace),
            ("project_id", self.project_id),
        ):
            if not val:
                raise RuntimeError(f"NaverMaiuClient: {name} is required")
        # 워크스페이스·프로젝트 값은 프로젝트 관리자가 저장한다. 경로 한 칸으로만 쓰도록 인코딩한다.
        # 인코딩하지 않으면 `/`·`?`·`#`이 든 값으로 조직 키가 서명한 요청이 다른 MI 경로로 나간다.
        # `.`·`..`은 인코딩해도 상위 경로를 가리키므로 받지 않는다.
        for name, val in (("workspace", self.workspace), ("project_id", self.project_id)):
            if str(val) in (".", ".."):
                raise RuntimeError(f"NaverMaiuClient: {name} is not a path segment")

    # --- HMAC v2 서명 -----------------------------------------------------

    def _sign(self, method: str, path_with_query: str, timestamp_ms: str) -> str:
        """NCP API Gateway HMAC-SHA256 v2 서명 문자열을 만든다."""
        msg = f"{method} {path_with_query}\n{timestamp_ms}\n{self.access_key}"
        sig = hmac.new(
            self.secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.b64encode(sig).decode("utf-8")

    def _headers(self, method: str, path_with_query: str) -> dict[str, str]:
        """요청에 필요한 NCP 인증 헤더(timestamp, access-key, signature)를 만든다."""
        ts = str(int(time.time() * 1000))
        return {
            "x-ncp-apigw-timestamp": ts,
            "x-ncp-iam-access-key": self.access_key,
            "x-ncp-apigw-signature-v2": self._sign(method, path_with_query, ts),
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, *, json: dict | None = None
    ) -> dict[str, Any]:
        """HMAC 서명 헤더를 붙여 MAIU API를 호출하고 응답 dict를 돌려준다.

        Raises:
            MaiuTransientError: 스로틀·타임아웃·5xx처럼 재시도하면 될 오류인 경우.
                호출자는 이 오류로 작업을 최종 실패로 확정하지 않는다.
            RuntimeError: 그 밖의 4xx인 경우. 요청·자격증명·리소스가 잘못된 경우다.
        """
        url = f"{self.base_url}{path}"
        headers = self._headers(method, path)
        shared = _shared_http.get()
        try:
            if shared is not None:
                # 사이클이 연 클라이언트를 같이 쓴다(`shared_session`).
                r = await shared.request(method, url, headers=headers, json=json,
                                         timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as cli:
                    r = await cli.request(method, url, headers=headers, json=json)
        except httpx.TransportError as e:  # 연결 실패나 타임아웃. 응답 코드 없이 일시 오류다
            raise MaiuTransientError(f"MAIU {method} {path} → transport error: {e}") from e
        if r.status_code in _TRANSIENT_STATUS:
            raise MaiuTransientError(
                f"MAIU {method} {path} → {r.status_code} {r.text[:300]}", status=r.status_code,
                retry_after=_retry_after_s(r.headers.get("Retry-After")),
            )
        if r.status_code >= 400:
            raise RuntimeError(f"MAIU {method} {path} → {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def _ws(self) -> str:
        """워크스페이스까지의 경로를 돌려준다. 프로젝트 목록 조회와 생성이 이 경로를 쓴다.

        `_wp()`는 이미 `/projects/{id}`를 포함한다. `_wp()`에 `/projects`를 또 붙이면
        `.../projects/101/projects`가 되어 MI가 404를 돌려준다.
        가짜 클라이언트는 URL을 쓰지 않으므로 이 버그는 경로 문자열을 검사하는 테스트만 잡아낸다.
        """
        return f"/api/v1/workspaces/{quote(str(self.workspace), safe='')}"

    def _wp(self) -> str:
        """워크스페이스/프로젝트 경로 접두사를 돌려준다."""
        return f"{self._ws()}/projects/{quote(str(self.project_id), safe='')}"

    # --- 경로 빌더 (게이트: 구 `video-assets` vs 신 `assets/index`) ----------
    #
    # 경로 표기가 두 가지라 한 곳에서만 고른다. 메서드 본문마다 분기를 두면
    # 어느 모드가 어느 경로를 쓰는지 추적하기 어렵다.
    # 분기를 여기 모아 두고 테스트가 두 모드의 경로를 각각 고정한다.

    def _asset_seg(self) -> str:
        """분석 리소스 세그먼트를 돌려준다. 신 MI는 `assets`, 구는 `video-assets`다."""
        return "assets" if self.index_api else "video-assets"

    def _asset_base(self, asset_id: str) -> str:
        """`.../{assets|video-assets}/{asset_id}` 경로를 돌려준다.

        등록 상태 조회와 삭제가 함께 쓴다.
        """
        return f"{self._wp()}/{self._asset_seg()}/{asset_id}"

    def _register_path(self) -> str:
        """에셋 등록(Object Storage 가져오기) 경로를 돌려준다.

        두 모드 모두 `import/object-storage`다.
        """
        return f"{self._wp()}/{self._asset_seg()}/import/object-storage"

    def _analysis_request_path(self, asset_id: str) -> str:
        """분석 요청 경로를 돌려준다.

        신: `assets/{id}/index`(POST), 구: `video-assets/{id}/analyze`.
        """
        return f"{self._asset_base(asset_id)}/index" if self.index_api \
            else f"{self._asset_base(asset_id)}/analyze"

    def _analysis_status_path(self, asset_id: str) -> str:
        """분석 상태 경로를 돌려준다. 신: `.../index/status`, 구: `.../analyze-status`."""
        return f"{self._asset_base(asset_id)}/index/status" if self.index_api \
            else f"{self._asset_base(asset_id)}/analyze-status"

    def _index_get_path(self, asset_id: str, index_id: str) -> str:
        """인덱스 조회 경로를 돌려준다.

        신: `.../index`(index_id 불필요), 구: `.../indexes/{index_id}`.
        """
        return f"{self._asset_base(asset_id)}/index" if self.index_api \
            else f"{self._asset_base(asset_id)}/indexes/{index_id}"

    def _cancel_path(self, asset_id: str) -> str:
        """분석 취소 경로를 돌려준다. 신: `.../index/cancel`(POST), 구: `.../analyze`(DELETE).

        HTTP 메서드는 `cancel_analysis`가 모드별로 고른다(신 POST, 구 DELETE).
        """
        return f"{self._asset_base(asset_id)}/index/cancel" if self.index_api \
            else f"{self._asset_base(asset_id)}/analyze"

    # --- 5단계 호출 -------------------------------------------------------

    async def get_workspace(self) -> dict:
        """워크스페이스 정보를 돌려준다. 응답에 연동 버킷 이름이 있다.

        MI는 워크스페이스마다 오브젝트 스토리지 버킷 하나를 연동한다.
        다른 버킷의 키를 보내면 `버킷 연동이 유효하지 않습니다`(400/10041)로 거절될 수 있다.
        연동 버킷을 설정값으로 추정하면 콘솔에서 연동을 바꾼 뒤 경고가 틀리므로 MI에서 직접 읽는다.

        다른 버킷의 키로도 등록이 성공하는 경우가 있어 MI의 동작이 일정하지 않다.
        그래서 프로젝트가 고른 저장소 버킷을 워크스페이스 연동 버킷과 같게 둔다
        (`project_stack` 점검).

        Returns:
            dict: `{"workspaceName", "bucketName", "monthlyAnalysisCount", ...}`.
        """
        return await self._request("GET", self._ws())

    async def list_workspaces(self) -> list[dict]:
        """계정의 워크스페이스 목록을 돌려준다.

        워크스페이스 존재 여부를 반환값으로 확인할 수 있는 유일한 API다.
        상세 조회(`get_workspace`)는 없는 워크스페이스에 404 오류를 낸다.
        그 404를 "없음"으로 읽으려면 오류 문자열에 의존해야 한다.
        """
        return await self._list_all("/api/v1/workspaces", ("workspaces", "content"))

    async def list_projects(self) -> list[dict]:
        """워크스페이스 안의 프로젝트 목록을 돌려준다. `projectId`·`name`·`isBaseProject`가 있다."""
        return await self._list_all(f"{self._ws()}/projects", ("projects", "content"))

    async def _list_all(self, path: str, keys: tuple[str, ...]) -> list[dict]:
        """목록 API를 페이지마다 읽어 항목을 모두 돌려준다.

        MI 목록 API는 `pageNum`이 필수다. 빼면 요청 파라미터 오류(400, 코드 10001)로 거절한다.
        응답의 `totalCount`만큼 모일 때까지 다음 페이지를 읽는다.
        `totalCount`가 없으면 첫 페이지가 전부라고 본다.
        응답이 어긋나 같은 페이지를 계속 받아도 페이지 수 상한(`_LIST_MAX_PAGES`)에서 멈춘다.
        """
        out: list[dict] = []
        for page in range(1, _LIST_MAX_PAGES + 1):
            resp = await self._request(
                "GET", f"{path}?pageNum={page}&pageSize={_LIST_PAGE_SIZE}")
            result = resp.get("result") or resp
            items = next((result[k] for k in keys if result.get(k)), [])
            out.extend(items)
            total = result.get("totalCount")
            if not items or total is None or len(out) >= int(total):
                break
        return out

    async def list_assets(self, *, page_size: int = 1) -> list[dict]:
        """MI 프로젝트의 에셋 목록 첫 페이지를 돌려준다. 프로젝트 접근 권한 점검이 쓴다.

        목록 경로는 에셋 등록과 같은 `…/projects/{id}/{assets|video-assets}`다.
        그래서 이 호출이 거절되면 등록도 거절된다. 점검에는 첫 페이지 한 건이면 충분하다.
        """
        path = f"{self._wp()}/{self._asset_seg()}?pageNum=1&pageSize={page_size}"
        resp = await self._request("GET", path)
        result = resp.get("result") or resp
        return result.get("assets") or result.get("videoAssets") or result.get("content") or []

    async def dispatch(self, *, request: dict) -> str:
        """`register_asset`에 위임한다. `dispatch` 호출부와 호환하려고 둔다."""
        return await self.register_asset(video_uri=request["video_uri"])

    async def register_asset(self, *, video_uri: str) -> str:
        """`s3://bucket/key`를 MI에 등록하고 `asset_id`를 돌려준다.

        경로는 `…/{assets|video-assets}/import/object-storage`다.
        응답의 ID 키는 모드와 버전에 따라 다르다.
        신 MI는 `result.assetId`, 구는 `result.videoId`다. 문서는 `result.id`로 적는다.
        문서와 실제 응답이 달라서 세 이름을 모두 받는다.
        """
        bucket, key = _parse_uri(video_uri, default_bucket=self.default_bucket)
        body = {"bucketName": bucket, "key": key}
        resp = await self._request("POST", self._register_path(), json=body)
        result = resp.get("result") or {}
        asset_id = result.get("assetId") or result.get("videoId") or result.get("id")
        if asset_id is None:
            raise RuntimeError(f"MAIU register_asset: no assetId/videoId in {resp}")
        return str(asset_id)

    async def check_registration(self, asset_id: str) -> str:
        """등록 상태를 조회해 `registered`·`pending`·`failed` 중 하나를 돌려준다."""
        resp = await self._request("GET", f"{self._asset_base(asset_id)}/import-status")
        status = (resp.get("result") or {}).get("uploadStatus")
        verdict = classify_stage_status(
            status, ok=_UPLOAD_OK, fail=_UPLOAD_FAIL, pending=_UPLOAD_PENDING)
        return "registered" if verdict == "ok" else verdict

    async def submit_analysis(self, asset_id: str, options: dict | None = None) -> str:
        """분석을 요청하고 `{asset_id}:` 형식의 쌍 id를 돌려준다.

        실제 `indexId`는 분석이 끝난 뒤 analyze-status 응답에 나타난다.
        그래서 여기서는 `asset_id`만으로 쌍을 만든다(`{asset_id}:`).
        `get_summary`는 index_id가 비어 있으면 analyze-status에서 지연 조회한다.

        Args:
            options: 클립 길이 설정을 담은 요청 본문 일부
                (`scene_options.SceneOptions.to_maiu_body()`). 없으면 환경 기본값
                (`maiu_scene_range`)을 쓴다. 옵션을 기록하지 않은 잡이 이 경우다.
                프로젝트 설정은 직접 읽지 않는다. 분석에 쓴 값이 요청 시점에 잡에 기록한 값과
                같아야 나중에 근거를 확인할 수 있다.
        """
        body: dict[str, Any] = {
            **(options or {"sceneRange": settings.maiu_scene_range}),
            "analysisPersonCount": settings.maiu_person_count,
        }
        await self._request("POST", self._analysis_request_path(asset_id), json=body)
        return f"{asset_id}:"

    async def check_analysis(self, analysis_id: str) -> str:
        """분석 상태를 조회해 `analyzed`·`pending`·`failed` 중 하나를 돌려준다.

        상태 필드는 두 모드 모두 `result.status`다(구 analyze-status와 신 index/status의 키가 같다).
        `ANALYZE_COMPLETED`·`ANALYZE_FAIL` 등은 `classify_stage_status`가 부분 일치로 판정한다.
        """
        asset_id, _ = _split_analysis_id(analysis_id)
        resp = await self._request("GET", self._analysis_status_path(asset_id))
        status = (resp.get("result") or {}).get("status")
        verdict = classify_stage_status(
            status, ok=_ANALYZE_OK, fail=_ANALYZE_FAIL, pending=_ANALYZE_PENDING)
        return "analyzed" if verdict == "ok" else verdict

    async def _resolve_index_id(self, asset_id: str) -> str:
        """`indexId`를 analyze-status, `/indexes` 목록 순서로 지연 조회한다.

        인덱스가 없으면 RuntimeError를 던진다.

        구 모델 전용이라 경로를 구 `video-assets`로 고정한다.
        신 모델(`index_api`)은 에셋당 최신 인덱스가 1건이라 index_id가 필요 없다.
        """
        st = await self._request(
            "GET", f"{self._wp()}/video-assets/{asset_id}/analyze-status"
        )
        index_id = str((st.get("result") or {}).get("indexId") or "")
        if index_id:
            return index_id
        lst = await self._request("GET", f"{self._wp()}/video-assets/{asset_id}/indexes")
        idxs = (lst.get("result") or {}).get("indexes") or []
        if not idxs:
            raise RuntimeError(f"MAIU: no indexes for asset {asset_id}")
        return str(idxs[0]["indexId"])

    async def get_summary(self, analysis_id: str) -> dict:
        """인덱스 상세를 reconciler가 쓰는 segments와 raw 메타 dict로 바꿔 돌려준다.

        신 모델은 에셋당 최신 인덱스가 1건이라 `.../assets/{id}/index`를 바로 조회한다.
        index_id를 지연 조회(analyze-status, indexes 목록)하는 것은 구 모델뿐이다.
        """
        asset_id, index_id = _split_analysis_id(analysis_id)
        if self.index_api:
            detail = await self._request("GET", self._index_get_path(asset_id, ""))
        else:
            if not index_id:
                index_id = await self._resolve_index_id(asset_id)
            detail = await self._request("GET", self._index_get_path(asset_id, index_id))
        return _to_summary(detail)

    # --- 장면 요약 (VLM이 장면 단위로 만든 자연어 요약) ----------------------

    async def _scene_summary_base(self, analysis_id: str) -> str:
        """장면 요약 엔드포인트의 경로 접두사를 돌려준다.

        신 모델은 `.../assets/{id}/scene-summary`다. 에셋당 최신 인덱스가 자동으로 대상이라
        index_id가 경로에 없다. 상태 조회는 `/status` 접미사를 붙인다.
        결과는 접미사 없이 이 경로를 GET한다. 문서의 `/result` 경로는 errorCode 300을 돌려준다
        (`get_scene_summaries`). 문서상 취소 경로는 `/cancel` 접미사다.

        구 모델은 `.../assets/{id}/indexes/{idx}/scene-summaries`다.
        장면 요약은 구 모델에서도 `video-assets`가 아닌 `/assets/` 아래에 있다.
        `video-assets` 아래 경로는 404(errorCode 300)를 돌려준다.
        `assets` 아래 경로는 요약이 없을 때 10081을 돌려준다. 경로 자체는 유효하다.
        """
        asset_id, index_id = _split_analysis_id(analysis_id)
        if self.index_api:
            return f"{self._wp()}/assets/{asset_id}/scene-summary"
        if not index_id:
            index_id = await self._resolve_index_id(asset_id)
        return f"{self._wp()}/assets/{asset_id}/indexes/{index_id}/scene-summaries"

    async def request_scene_summary(
        self, analysis_id: str, scene_ids: list[int] | None = None
    ) -> str:
        """장면 요약 생성을 요청(POST)하고 `sceneSummaryId`(str)를 돌려준다.

        `scene_ids`가 없으면 전체 장면이 대상이다(최초 요청은 전체).
        """
        base = await self._scene_summary_base(analysis_id)
        # 대상 장면 필드 이름이 모드마다 다르다. 신: `scenes`, 구: `sceneIds`.
        scene_key = "scenes" if self.index_api else "sceneIds"
        body = {scene_key: scene_ids} if scene_ids else {}
        resp = await self._request("POST", base, json=body)
        result = resp.get("result") or {}
        sid = result.get("sceneSummaryId") or result.get("id")
        if sid is None:
            raise RuntimeError(f"MAIU request_scene_summary: no sceneSummaryId in {resp}")
        return str(sid)

    async def check_scene_summary_status(self, analysis_id: str, summary_id: str) -> str:
        """장면 요약 상태를 조회(GET)해 대문자 상태 문자열을 돌려준다.

        상태는 `BEFORE`나 `WAIT_SUMMARIZE`에서 `SUMMARIZING`을 거쳐
        `SUMMARIZE_COMPLETED` 또는 `SUMMARIZE_FAIL`로 바뀐다.
        신 모델은 `{base}/status`를 쓴다. 요약이 에셋 단위라 경로에 summary_id가 없다.
        그래서 `summary_id` 인자는 무시한다. 구 모델은 `{base}/{summary_id}/status`를 쓴다.
        """
        base = await self._scene_summary_base(analysis_id)
        path = f"{base}/status" if self.index_api else f"{base}/{summary_id}/status"
        resp = await self._request("GET", path)
        return ((resp.get("result") or {}).get("status") or "").upper()

    async def get_scene_summaries(self, analysis_id: str) -> list[dict]:
        """완료된 장면 요약을 기본 경로 GET으로 받아 [{scene,startMs,endMs,description}]로 돌려준다.

        `SUMMARIZE_COMPLETED` 상태에서만 부른다. 두 모델 모두 기본 경로를 GET한다.
        신은 `.../scene-summary`, 구는 `.../indexes/{idx}/scene-summaries`다.
        API 문서는 결과 경로를 `/result`로 적지만 그 경로는 errorCode 300을 돌려준다.
        """
        base = await self._scene_summary_base(analysis_id)
        resp = await self._request("GET", base)
        return _to_scene_summaries(resp)

    # --- raw 메타데이터 조회 (reconciler가 video·clip 메타 저장에 쓴다) ------

    async def get_import_status(self, asset_id: str) -> dict:
        """import-status 응답의 result 전체를 돌려준다(uploadStatus, metaData: resolution/durationMs/size/format)."""  # noqa: E501
        resp = await self._request("GET", f"{self._asset_base(asset_id)}/import-status")
        return resp.get("result") or {}

    async def get_analyze_status(self, asset_id: str) -> dict:
        """분석 상태 응답의 `result` 전체를 돌려준다(status, indexId, analysisStatusMessage).

        신: `.../index/status`, 구: `.../analyze-status`.
        두 응답 모두 `result.status`와 `indexId`가 같은 형태로 들어 있다.
        """
        resp = await self._request("GET", self._analysis_status_path(asset_id))
        return resp.get("result") or {}

    async def get_index_detail(self, asset_id: str, index_id: str) -> dict:
        """인덱스 응답의 `result` 전체를 그대로 돌려준다(analysisSetting, indexDetails).

        신 모델은 `.../assets/{id}/index`(index_id 불필요)를,
        구 모델은 `.../video-assets/{id}/indexes/{id}`를 쓴다.
        """
        resp = await self._request("GET", self._index_get_path(asset_id, index_id))
        return resp.get("result") or {}

    async def delete_asset(self, asset_id: str) -> None:
        """에셋을 삭제한다.

        경로는 모드를 따른다. 구 모델은 `DELETE /video-assets/{id}`, 신 모델은
        `DELETE …/assets/{id}`다(`_asset_base`).
        MAIU에는 있는데 서버 기록에는 없는 고아 에셋을 정리할 때만 쓴다.
        고아 에셋은 분석 요청이 나가지 않아 콘솔에 `인덱싱 전`으로 남는다.
        404는 이미 지워진 에셋이라 성공으로 본다.

        Raises:
            RuntimeError: 404가 아닌 오류로 실패한 경우.
        """
        try:
            await self._request("DELETE", self._asset_base(asset_id))
        except RuntimeError as e:
            if "404" not in str(e):
                raise

    async def cancel_analysis(self, asset_id: str) -> None:
        """분석 작업을 취소한다.

        `ANALYZE_FAIL`·`CANCEL` 뒤 재요청하기 전에 NCP 쪽 상태를 정리하는 데 쓴다.
        HTTP 메서드가 모드마다 다르다. 신: `POST .../index/cancel`, 구: `DELETE .../analyze`.
        `DELETE …/index/cancel`은 errorCode 300(URL not found)을 돌려준다.
        `POST …/index/cancel`이 돌려주는 code 10038은 취소할 수 없는 상태라는 뜻이다.
        404(이미 종료되어 취소할 수 없음)는 무시한다. 재분석 진행에 영향이 없다.
        """
        method = "POST" if self.index_api else "DELETE"
        try:
            await self._request(method, self._cancel_path(asset_id))
        except RuntimeError as e:
            # 404는 이미 종료된 분석이라 정상으로 본다.
            if "404" not in str(e):
                raise


# --- helpers --------------------------------------------------------------

def _parse_uri(uri: str, *, default_bucket: str = "") -> tuple[str, str]:
    """`s3://bucket/key`, `ncp://bucket/key` 또는 키만 받아 (bucket, key)로 나눈다.

    Raises:
        RuntimeError: 경로가 비었거나, 키만 받았는데 `default_bucket`도 없는 경우.
    """
    if not uri:
        raise RuntimeError("MAIU: cannot parse uri None — video has no analysis input")
    for scheme in ("s3://", "ncp://"):
        if uri.startswith(scheme):
            rest = uri[len(scheme):]
            bucket, _, key = rest.partition("/")
            return bucket, key
    if not default_bucket:
        raise RuntimeError(f"MAIU: cannot parse uri {uri!r} without default_bucket")
    return default_bucket, uri.lstrip("/")


def _split_analysis_id(analysis_id: str) -> tuple[str, str]:
    """`asset_id:index_id` 쌍을 나눠 (asset_id, index_id)를 돌려준다."""
    asset_id, _, index_id = analysis_id.partition(":")
    return asset_id, index_id


def _to_scene_summaries(resp: dict) -> list[dict]:
    """scene-summaries 응답을 [{scene,startMs,endMs,description}]로 바꾼다.

    `result`로 감싼 응답과 감싸지 않은 응답을 모두 받는다.
    `sceneSummary.description`은 4~6문장의 자연어 요약이다. 빠진 항목은 `None`으로 채운다.
    """
    result = resp.get("result") if "result" in resp else resp
    result = result or {}
    out = []
    for s in result.get("sceneSummaries") or []:
        ss = s.get("sceneSummary") or {}
        out.append({
            "scene": s.get("scene"),
            "startMs": s.get("startMs"),
            "endMs": s.get("endMs"),
            "description": ss.get("description"),
        })
    return out


def _to_summary(detail: dict) -> dict:
    """MAIU index 상세 응답을 reconciler가 쓰는 dict로 바꾼다. raw 메타를 빠짐없이 옮긴다.

    `detail`은 `NaverMaiuClient.get_index_detail`이 돌려주는 result일 수도 있고,
    문서 예시처럼 `{result: {...}}` 전체 응답일 수도 있다. 둘 다 받는다.

    결과에는 다음 필드가 들어간다.
    `segments`는 raw segment 필드 전부다(start·end 프레임, ms, scene·shot,
    speeches, keywords, people, sceneThumbnailUrl).
    `analysis_setting`은 analysisEngine·sceneRange·analysisPersonCount·analysisLanguage다.
    `indexId`는 응답 최상위의 indexId다.
    `index_meta`는 생성·수정 시각과 사용자다.
    `durationMs`·`fps`·`frameCount`·`videoUrl`은 indexDetails의 상위 메타다.
    """
    result = detail.get("result") if "result" in detail else detail
    result = result or {}
    idx = result.get("indexDetails") or {}
    segments = []
    for s in idx.get("segments") or []:
        segments.append({
            "start": s.get("start"),
            "end": s.get("end"),
            "startMs": s.get("startMs", 0),
            "endMs": s.get("endMs", 0),
            "scene": s.get("scene"),
            "shot": s.get("shot"),
            "sceneThumbnailUrl": s.get("sceneThumbnailUrl"),
            "speeches": s.get("speeches") or [],
            "keywords": s.get("keywords") or [],
            "people": s.get("people") or [],
        })
    return {
        "segments": segments,
        "durationMs": idx.get("durationMs"),
        "fps": idx.get("fps"),
        "frameCount": idx.get("frameCount"),
        "videoUrl": idx.get("videoUrl"),
        "indexId": result.get("indexId"),
        "analysis_setting": result.get("analysisSetting") or {},
        "index_meta": {
            "createdTime": result.get("createdTime"),
            "createdUserName": result.get("createdUserName"),
            "updatedTime": result.get("updatedTime"),
            "updatedUserName": result.get("updatedUserName"),
        },
    }
