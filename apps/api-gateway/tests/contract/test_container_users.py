"""운영 이미지가 root가 아닌 고정 사용자로 실행되는지 Dockerfile을 읽어 확인하는 테스트.

이미지 빌드는 서버 테스트에서 돌리지 않으므로 마지막 `USER` 지시어와 사용자 생성 줄을 문자열로 대조한다.
마지막 `USER`가 빠지거나 root로 돌아가면 그 컨테이너가 root로 뜬다.
"""
import re
from pathlib import Path

import pytest

_DOCKER = Path(__file__).resolve().parents[4] / "infra" / "docker"

#: Dockerfile 이름과 마지막 `USER` 값.
_EXPECTED = {
    "api.Dockerfile": "app",
    "worker-io.Dockerfile": "app",
    "worker-jobs.Dockerfile": "app",
    "worker-gpu.Dockerfile": "app",
    "web.Dockerfile": "101:101",
    "pgbackup.Dockerfile": "postgres",
}


def _last_user(text: str) -> str | None:
    """Dockerfile의 마지막 `USER` 값을 돌려준다. 없으면 None이다."""
    users = re.findall(r"^USER\s+(\S+)\s*$", text, re.M)
    return users[-1] if users else None


@pytest.mark.parametrize(("name", "user"), sorted(_EXPECTED.items()))
def test_image_runs_as_a_fixed_non_root_user(name: str, user: str):
    """각 이미지의 마지막 `USER`가 정한 비 root 사용자인지 확인한다."""
    assert _last_user((_DOCKER / name).read_text()) == user, name


@pytest.mark.parametrize("name", [n for n, u in _EXPECTED.items() if u == "app"])
def test_app_user_has_fixed_ids_and_owns_only_app_dir(name: str):
    """파이썬 이미지가 uid·gid 10001의 app 사용자를 만들고 `/app` 디렉터리만 넘기는지 확인한다.

    소스를 그 사용자 소유로 복사하면 침해된 프로세스가 앱 코드를 고쳐 쓸 수 있다.
    """
    text = (_DOCKER / name).read_text()
    assert "groupadd --system --gid 10001 app" in text
    assert "useradd --system --uid 10001 --gid app" in text
    assert "install -d -o app -g app /app\n" in text
    assert not re.search(r"^COPY\s+--chown", text, re.M), "소스를 실행 사용자 소유로 복사한다"


def test_web_nginx_writes_only_its_own_folders():
    """web 이미지가 pid 파일·임시 폴더·생성 설정 폴더만 nginx 사용자에게 주는지 확인한다."""
    text = (_DOCKER / "web.Dockerfile").read_text()
    assert "pid /tmp/nginx.pid;" in text
    assert "chown -R 101:101 /var/cache/nginx" in text
    assert "install -d -o 101 -g 101 /etc/nginx/generated" in text
    assert "chown -R 101:101 /etc/nginx " not in text and "chown -R 101:101 /etc/nginx\n" not in text
