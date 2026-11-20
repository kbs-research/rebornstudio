"""테스트 오브젝트 스토리지. 인프로세스 moto를 띄우고 그 주소와 버킷을 정한다.

실 스토리지는 NCP뿐이지만 테스트는 외부 NCP를 호출하지 않는다.
외부 NCP를 호출하면 CI 결과가 NCP 장애나 시크릿 유무에 따라 달라진다.
moto가 S3 호환 서버를 프로세스 안(임의 포트)에 띄운다. 그래서 CI·로컬 모두 별도 인프라가 필요 없다.

앱은 저장소 자격증명을 환경 변수에서 읽지 않는다. 이 서버를 가리키는 NCP 저장소 연결을
`tests/_storage.py`가 DB에 등록하고, 테스트 프로젝트가 그 연결을 저장소 대상으로 고른다.
"""
import boto3
from moto.server import ThreadedMotoServer

_moto = ThreadedMotoServer(port=0, verbose=False)
_moto.start()
_port = _moto._server.socket.getsockname()[1]

#: moto 서버 주소. 테스트 저장소 연결의 주소다.
ENDPOINT = f"http://127.0.0.1:{_port}"
#: 테스트 저장소 연결의 버킷. 운영 버킷 이름과 무관하게 이 값과 아래 `create_bucket`만 맞으면 된다.
BUCKET = "test-bucket"
#: moto는 아무 키나 받는다. 연결에 저장하는 키다.
ACCESS_KEY = SECRET_KEY = "testing"

# 버킷은 us-east-1 클라이언트로 만든다. S3 API는 그 외 리전에 LocationConstraint를 요구하고
# moto도 이를 흉내 낸다. 앱 클라이언트의 서명 리전(kr-standard)과는 무관하다.
boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name="us-east-1",
).create_bucket(Bucket=BUCKET)
