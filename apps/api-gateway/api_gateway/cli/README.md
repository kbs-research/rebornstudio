# api_gateway/cli

## 목적

운영자가 서버에서 실행하는 관리 명령을 둡니다. 화면으로 할 수 없는 복구와 키 교체 작업만 둡니다.
api 이미지에 들어 있으므로 운영 서버에서 api 컨테이너로 실행합니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `rotate_credentials_key.py` | 연결 시크릿을 새 암호화 키(`CREDENTIALS_KEY`)로 다시 암호화합니다. 기본은 건수만 세는 드라이런이고 `--apply`는 이전 키(`CREDENTIALS_KEY_PREVIOUS`) 암호문만 바꿉니다. 출력은 건수 JSON 한 줄이고, 어느 키로도 못 푸는 행이 있으면 종료 코드 1입니다. 절차는 `infra/deploy/README.md`의 "자격증명 암호화 키 교체" 절에 있습니다 |
| `recover_admin.py` | 최고관리자의 1회용 비밀번호 재설정 링크를 발급합니다. 최고관리자가 비밀번호를 잃어 화면에서 링크를 발급할 사람이 없을 때 씁니다. 토큰은 주소 조각(`#token=`)에 실어 웹 서버 접근 로그에 남지 않게 합니다 |

```bash
infra/deploy/dc.sh exec api uv run --package api-gateway \
    python -m api_gateway.cli.recover_admin --email admin@example.com --base-url https://studio.example.com
infra/deploy/dc.sh exec -T api uv run --package api-gateway \
    python -m api_gateway.cli.rotate_credentials_key [--apply]
```

## 설계 이유

- 명령은 `scripts/`가 아니라 패키지 안에 둡니다. api 이미지는 `apps/api-gateway`만 담으므로 저장소의 `scripts/`는 운영 서버 컨테이너에서 실행할 수 없습니다.
- 재설정 링크 발급은 화면과 같은 함수(`services/auth/admin.issue_reset_token`)가 합니다. 유효 시간, 1회 사용, 이전 링크 무효화 규칙이 화면 발급과 갈라지지 않습니다.
- 비밀번호를 직접 받아 바꾸지 않고 링크를 발급합니다. 비밀번호가 셸 기록과 터미널에 남지 않고, 새 비밀번호의 규칙 검사도 화면이 그대로 합니다.
- 서버 명령으로 발급한 토큰은 발급자(`issued_by`)가 비어 있고, 감사 기록에는 `via: server_command`가 남습니다.
- 대상은 활성 최고관리자로 한정합니다. 일반 사용자의 링크는 최고관리자가 화면에서 발급합니다. 이메일을 주지 않으면 활성 최고관리자가 한 명일 때만 그 계정을 고릅니다.
- 키 교체는 새 키와 이전 키를 함께 읽는 방식(MultiFernet)으로 합니다. 서비스를 멈추지 않고 키를 바꿀 수 있고, 재암호화 전에도 이전 키 시크릿을 읽습니다. 재암호화는 `services/crypto.rekey_connections`가 행마다 비교 후 갱신으로 쓰므로 실행 중 화면에서 바꾼 시크릿을 덮지 않습니다.

## 의존성

- 부르는 것: `api_gateway.database`, `api_gateway.models.User`, `api_gateway.services.auth.admin`, `api_gateway.services.crypto`
- 부르는 쪽: 운영자(`infra/deploy/dc.sh exec api …`)

## 남은 일

- 없음
