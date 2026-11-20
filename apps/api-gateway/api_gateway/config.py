"""환경변수와 `.env`에서 읽는 런타임 설정."""
import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 저장소 루트의 `.env` 경로. 이 파일은 `<루트>/apps/api-gateway/api_gateway/config.py`에 있다.
# 이미지 안에서는 `/app/.env`를 가리킨다.
# `.dockerignore`가 `.env`를 빼므로 이미지에는 이 파일이 없다.
_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _env_file() -> Path | None:
    """Settings가 읽을 `.env` 경로를 정한다.

    실행 위치(CWD)와 무관하게 저장소 루트의 `.env`를 읽는다. `REBORN_ENV_FILE`이 있으면 그 경로를
    쓰고, 빈 문자열이면 파일을 읽지 않는다. 테스트는 빈 문자열로 로컬 `.env`를 막는다
    (tests/conftest.py).
    파일이 없으면 pydantic-settings가 오류 없이 건너뛴다. 그래서 컨테이너와 prod는 환경변수만 쓴다.
    환경변수는 언제나 `.env`보다 우선한다.
    """
    override = os.environ.get("REBORN_ENV_FILE")
    if override is None:
        return _ROOT_ENV_FILE
    return Path(override) if override.strip() else None


# 비밀값 검사 실패 문구에 붙이는 안내. `ENVIRONMENT`의 기본값이 prod라서
# 로컬에서 `.env` 없이 실행하면 이 검사에 걸린다.
# 그 경우 원인이 비밀값이 아니라 환경 이름이라는 것을 같은 줄에서 알린다.
_LOCAL_HINT = " (ENVIRONMENT defaults to prod; set ENVIRONMENT=local for local development)"

# 운영 환경에서 쓰면 기동을 막는 기본 비밀값.
_INSECURE_DEFAULTS = {
    "dev-jwt-secret-change-in-prod",
    "dev-jwt-refresh-secret-change-in-prod",
    "minioadmin",
}


class Settings(BaseSettings):
    """API와 워커가 함께 쓰는 런타임 설정.

    `.env` 경로는 `_env_file`이 정한다.
    필드를 더하면 사전 `docs/env-catalog.md`에 같은 이름을 적는다.
    `scripts/ci/check-env-catalog.py`가 빠진 이름을 잡는다.
    """

    # 검사 실패 문구에 입력값을 싣지 않는다. 실으면 기동 로그에 시크릿의 앞뒤 일부가 남는다.
    model_config = SettingsConfigDict(
        env_file=_env_file(), extra="ignore", hide_input_in_errors=True)

    # 실행 환경 이름. 기본값을 운영(prod)으로 두어 배포가 이 값을 빠뜨려도 비밀값 검사가 켜진다.
    # 로컬 개발은 `.env`(`ENVIRONMENT=local`)나 개발 compose, 테스트는 conftest(`test`)가 값을 준다.
    environment: str = "prod"
    # 앱 로그 레벨. 운영에서 로그가 많으면 `WARNING`, 자세히 볼 때는 `DEBUG`로 둔다.
    # `logging_setup`이 같은 환경변수(`LOG_LEVEL`)를 직접 읽는다.
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://reborn:reborn@postgres:5432/rebornstudio"
    redis_url: str = "redis://redis:6379/0"
    # 오브젝트 스토리지 동작값.
    # 주소·버킷·키는 프로젝트가 고른 자격증명 연결(`platform_credentials`)에 있다.
    # 여기에는 계정과 무관한 서명 유효 시간과 업로드 상한만 둔다.
    s3_presign_ttl_seconds: int = 3600
    s3_max_upload_bytes: int = 5 * 1024 * 1024 * 1024  # 5 GiB

    # MI 요청 전에 ffprobe로 규격을 검사하는 게이트의 기준.
    # MI는 시간당 과금이라서 규격 위반 파일은 분석에 보내기 전에 거른다.
    # 크기 상한은 10 GiB, 길이 상한은 MI 업로드 규격표의 6시간이다.
    probe_max_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB
    probe_max_duration_seconds: float = 6 * 3600  # 6시간. MI 업로드 규격표의 상한
    # 허용 코덱 목록(쉼표 구분). 빈 값이면 코덱 검사를 건너뛴다.
    # 목록 밖 코덱은 거부하지 않고 변환 대상이 된다(`probe.REDEEMABLE_VIOLATIONS`).
    # 분석에 보내는 사본의 코덱은 `transcode.needs_transcode`가 h264 기준으로 정한다.
    probe_codec_whitelist: str = "h264,hevc,vp9,mpeg4,mpeg2video"
    # MI 업로드 규격표의 컨테이너 기준.
    # ffprobe `format_name`은 여러 이름을 쉼표로 이어 준다(`mov,mp4,m4a,…`, `matroska,webm`).
    # 그래서 목록과 하나라도 겹치면 통과로 본다.
    probe_container_whitelist: str = "mp4,mov,matroska,webm"
    probe_min_height: int = 360      # 해상도 하한. 업스케일로는 정보가 늘지 않아 미만이면 거부한다
    probe_max_height: int = 1080     # 해상도 상한. 초과하면 720p 사본을 만들어 보낸다
    # ffprobe 실행 시간 상한(초). ffprobe는 presigned URL을 Range 요청으로 읽는다.
    # 대상이 응답하지 않을 때 워커가 무한히 기다리지 않게 한다.
    probe_timeout_seconds: int = 60
    # 원본 체크섬 백필의 회차당 한도(개수와 시간 예산).
    # 파일 크기 차이가 커서 같은 건수라도 처리 시간이 수십 배 달라진다.
    # 실행이 beat 주기(600초)를 넘기면 다음 실행이 advisory lock에 막혀 그 주기를 건너뛴다.
    # 그래서 예산을 넘기면 처리 중인 건까지만 끝내고 멈춘다.
    # 초기 적재 때는 환경변수로 둘 다 키운다. 평상시에는 새로 등록된 영상만 처리한다.
    checksum_batch: int = 20
    checksum_budget_seconds: float = 300.0

    # DB 커넥션 풀 크기. api 레플리카 하나가 최대 pool_size+max_overflow개를 연다.
    # 레플리카 상한을 곱한 값이 DB max_connections의 70%를 넘지 않게 잡는다.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # 인코딩 1건이 쓸 스레드 수. 0이면 ffmpeg가 자동으로 정한다.
    # 컨테이너 안의 `nproc`은 cgroup 할당 대신 호스트 코어 수를 보고해서, 자동값은 할당보다 많은
    # 스레드를 만들고 문맥 전환 비용으로 인코딩이 느려진다.
    # 권장값은 `GPU_WORKER_CPUS ÷ GPU_WORKER_CONCURRENCY`다. 처리량이 모자라는 때는 큐가 찼을
    # 때라서 그 경우에 맞춘다. 큐가 얕으면 잡 하나가 자기 몫의 스레드만 써서 조금 느리다.
    transcode_threads: int = 0

    # 트랜스코딩 전역 in-flight 상한. 재생본·장면 분할·publish가 단계마다 따로 센다.
    # worker-gpu 한 대의 동시 실행 수(기본 2)에 맞춘다. 상한보다 많이 보낸 잡은 큐에서 기다리고,
    # 투입 리스(`transcode._QUEUE_WAIT_S`, 4시간)가 그 대기를 덮는다. 대기가 리스를 넘으면 회수되어
    # 새 토큰으로 다시 투입하므로 상한을 실행 수보다 크게 잡아도 처리량은 늘지 않는다.
    transcode_max_inflight: int = 2
    # 비식별화 전역 in-flight 상한. 디스패치가 이 수까지만 잡을 보낸다.
    # 모델이 워커 프로세스 안에서 GPU 메모리를 쓰므로 GPU 한 장이면 1이다.
    # worker-gpu는 인코딩 하나와 비식별화 하나를 같이 돌리도록 실행 수를 잡는다
    # (GPU_WORKER_CONCURRENCY).
    # 자원(GPU 메모리)을 쓰는 방식이 달라서 트랜스코딩 상한과 따로 둔다.
    deident_max_inflight: int = 1

    # JWT 설정. 토큰 폐기(revocation) 목록은 Redis에 둔다(`services/auth/jwt_revocation`).
    # dev 기본값도 32바이트 이상으로 둔다. 짧으면 JWT InsecureKeyLength 경고가 난다.
    jwt_secret: str = "dev-jwt-secret-CHANGE-IN-PROD-padding-xxxxxxxx"
    jwt_refresh_secret: str = "dev-jwt-refresh-secret-CHANGE-IN-PROD-padding-xxxxxxxx"
    jwt_access_ttl_minutes: int = 15      # prod 권장값. 탈취된 토큰의 노출 시간을 줄인다
    # refresh 토큰 하나의 수명(일). 교체할 때마다 새로 세지만 세션 절대 수명을 넘지 않는다.
    jwt_refresh_ttl_days: int = 14
    # 로그인 세션의 절대 수명(일). 로그인 시각(`auth_time` 클레임)부터 센다.
    # 교체가 refresh 토큰의 만료를 계속 미루므로, 상한이 없으면 탈취한 토큰으로
    # 세션을 무기한 잇는다.
    # 지나면 refresh가 401이고 사용자는 다시 로그인한다.
    jwt_session_max_days: int = Field(default=14, ge=1)

    # CORS 허용 origin 목록(쉼표 구분). FE를 별도 origin으로 띄울 때 쓴다.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # 인증 경로의 분당 실패 한도(60초 창). 경로마다 버킷이 따로다(`services/auth/rate_limit.py`).
    # 틀린 비밀번호와 쓸 수 없는 재설정 링크는 IP별로, 비밀번호 변경 실패는 사용자별로 센다.
    # 공인 IP 하나 뒤에서 여러 작업자가 일하는 사무실을 전제로 IP로는 실패만 센다.
    # 한도를 넘으면 창이 끝날 때까지 맞는 비밀번호도 429다.
    # 계정 단위 무차별 대입은 login_throttle의 연속 실패 잠금이 따로 막는다.
    rate_limit_auth_per_min: int = 10
    # 쿠키가 없거나 무효인 refresh의 IP별 한도. 유효한 쿠키의 refresh는 세지 않는다.
    # 세션이 끝난 사람이 탭을 열 때마다 401이 하나씩 나므로 사무실 인원보다 넉넉히 둔다.
    rate_limit_refresh_per_min: int = 300
    # 로그인 세션(refresh family) 하나의 refresh 한도. 탭들은 갱신을 한 번으로 합치므로
    # 새 탭과 새로고침을 1초에 한 번씩 1분 동안 이어도 넘지 않는 값이다.
    rate_limit_refresh_session_per_min: int = 60
    # CSP 위반 보고 수집(`POST /csp-report`) 한도. 인증 없는 경로라 주소별로 센다.
    # 보고를 하나씩 보내는 브라우저는 썸네일이 여럿 막힌 화면 하나에서 수십 건을 한꺼번에 보낸다.
    rate_limit_csp_report_per_min: int = 60
    # 요청 본문 최대 크기. POST/PUT 본문이 이보다 크면 413으로 거부한다.
    # 영상은 S3 presigned PUT으로 올리므로 FastAPI 본문을 거치지 않는다.
    max_request_body_bytes: int = 2 * 1024 * 1024  # 2 MiB (JSON 페이로드용)

    # 실제 MI를 부를지 정하는 동작 모드. False면 FakeMaiuClient가 응답한다.
    # True면 NaverMaiuClient를 쓰고, 자격증명·워크스페이스·분석 프로젝트는 프로젝트가 고른
    # 분석 연결에서 온다(`platform_credentials`). 계정 값은 여기 두지 않는다.
    # 주의: 연결을 고르지 않은 프로젝트를 가짜로 돌리는 규칙으로 바꾸지 않는다. 운영에서 연결을
    # 빠뜨린 프로젝트가 가짜 분석 결과를 받게 된다.
    maiu_enabled: bool = False
    # local/test 밖에서 `maiu_enabled=False`로 기동하려면 참으로 둔다. 기본은 거짓이라 운영이
    # 가짜 MI로 뜨지 않는다(`_enforce_prod_secrets`). 시연용 서버처럼 가짜 결과가 의도일 때만 켠다.
    allow_fake_maiu: bool = False
    maiu_scene_range: str = "MEDIUM"       # EXTRA_SHORT|SHORT|MEDIUM|LONG
    maiu_person_count: int = 0             # 0~10. 0이면 인물 분석을 건너뛰어 가장 빠르다
    # 신 MI 경로(개편된 `assets/{id}/index` 모델)를 쓰면 True.
    # 에셋마다 최신 인덱스가 1건이라 index_id가 필요 없다.
    # False면 구 경로(`video-assets`/`analyze`/`indexes/{id}`)를 쓴다.
    # 구 경로로 되돌릴 때는 env `MAIU_USE_INDEX_API=false`를 준다.
    # 구 경로는 MI가 폐기할 예정인 API다.
    maiu_use_index_api: bool = True
    maiu_http_timeout: float = 30.0
    maiu_sweep_stale_minutes: int = 10     # *ing 상태를 멈춘 것으로 보는 임계(분)
    # 비용 예측 단가(원/시간). 1시간 영상을 장면 요약까지 처리하면 약 1만원으로 본다.
    # 실제 과금 정책에 맞춰 조정한다.
    maiu_cost_per_hour_krw: int = 10_000

    # 연결 자격증명 암호화 키(Fernet). 운영자가 화면에서 넣는 클라우드 시크릿을
    # 이 키로 암호화해 DB에 저장한다(쓰기 전용).
    # 키 자체는 env 비밀이라 prod 필수 목록(`_enforce_prod_secrets`)에 들어 있다.
    # dev 기본값은 고정값이라 로컬에서 재시작해도 복호화할 수 있다.
    credentials_key: str = "dev-credentials-key-CHANGE-IN-PROD-0000000000"
    # 키를 바꾸는 동안 복호에만 쓰는 이전 키 목록(쉼표 구분).
    # 새 시크릿은 늘 `credentials_key`로 암호화한다. 저장된 시크릿을 모두 새 키로
    # 다시 암호화한 뒤(`api_gateway.cli.rotate_credentials_key`) 비운다.
    # 키 문자열에는 쉼표를 쓰지 않는다. 쉼표가 있으면 키가 둘로 나뉘어 복호에 실패한다.
    credentials_key_previous: str = ""

    # 연결 주소 검사가 막는 내부 호스트(루프백·링크 로컬·메타데이터·점 없는 이름) 가운데 예외로 받을
    # 호스트 이름이나 IP(쉼표 구분, 정확히 일치). 사내 모델 서버를 점 없는 이름으로 부를 때 적는다.
    # 주소 검사는 `services.stacks.connections.address_problem`에 있다.
    connection_host_allowlist: str = ""

    @property
    def credentials_key_previous_list(self) -> list[str]:
        """`credentials_key_previous` 쉼표 문자열을 키 목록으로 바꾼다.

        앞뒤 공백과 빈 항목은 뺀다.
        """
        return [k.strip() for k in self.credentials_key_previous.split(",") if k.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        """`cors_origins` 쉼표 문자열을 origin 목록으로 바꾼다. 앞뒤 공백과 빈 항목은 뺀다."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("environment")
    @classmethod
    def _env_lower(cls, v: str) -> str:
        """environment 값을 소문자로 정규화한다."""
        return v.lower()

    @model_validator(mode="after")
    def _enforce_prod_secrets(self) -> "Settings":
        """local/test 밖에서는 비밀값과 CORS 설정, MI 동작 모드를 검사한다.

        비밀값이 비었거나 기본값이거나 `dev-`로 시작하거나 32자 미만이면
        ValueError로 기동을 막는다.
        CORS origin에 localhost가 있어도 ValueError로 기동을 막는다.
        `maiu_enabled`가 거짓이면 `allow_fake_maiu`를 함께 주지 않는 한 기동을 막는다.
        가짜 MI 클라이언트는 오류 없이 가짜 분석 결과를 돌려주므로 값이 빠진 운영이 조용히 가짜
        결과를 라벨링과 납품으로 흘려보낸다.
        """
        if self.environment in ("local", "test"):
            return self
        for key in ("jwt_secret", "jwt_refresh_secret", "credentials_key"):
            value = getattr(self, key)
            if not value or value in _INSECURE_DEFAULTS or value.startswith("dev-"):
                raise ValueError(
                    f"{key} must be set (not a default) in {self.environment}{_LOCAL_HINT}")
            if len(value) < 32:
                raise ValueError(f"{key} must be >=32 chars in {self.environment}{_LOCAL_HINT}")
        # prod는 web nginx가 /api를 떼고 프록시하는 same-origin 구성이다.
        # 그래서 CORS origin이 비어 있는 것이 정상이다.
        # 별도 origin을 쓸 때만 채우고 localhost는 넣을 수 없다.
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins_list):
            raise ValueError("cors_origins must not contain localhost in non-local env")
        if not self.maiu_enabled and not self.allow_fake_maiu:
            raise ValueError(
                f"maiu_enabled must be true in {self.environment} "
                "(set ALLOW_FAKE_MAIU=true to run the fake MI client on purpose)")
        return self


settings = Settings()
