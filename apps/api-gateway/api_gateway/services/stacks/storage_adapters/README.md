# services/stacks/storage_adapters

저장소 백엔드를 계약 모양으로 옮기는 어댑터입니다. 계약은 [`reborn_contracts.storage_backend`](../../../../../../packages/reborn-contracts/python/src/reborn_contracts/storage_backend.py)에 있습니다.
운영에서 쓰는 어댑터는 NCP 어댑터 하나입니다. 저장소 접근 관문은 `media/storage.py` 하나이고, 그 안의 `_adapter(store)`가 어댑터를 돌려줍니다. 그래서 호출부는 어댑터를 몰라도 됩니다.

## 주요 파일

| 파일 | 역할 |
|---|---|
| `ncp.py` | `NcpStorage`. NCP Object Storage(S3 호환). 파트 번호 방식 재개 업로드, `s3://`, 서버 쪽 복사 지원. 복사는 boto3 관리형 복사라 1 GiB 이상은 UploadPartCopy로 나눠 단일 CopyObject의 5 GiB 상한을 넘는 객체도 옮깁니다 |
| `dummy_gcs.py` | GCS 방식 더미. 로컬 디스크에 쓰고, 오프셋 방식 재개 업로드, `gs://`, 서버 쪽 복사 없음 |
| `__init__.py` | 패키지 표시 |

## 설계 이유

- S3 호환 백엔드(AWS S3·NCP 등)는 엔드포인트만 바꾸면 boto3로 같은 코드가 동작합니다. 계약이 필요한 차이는 S3 호환이 아닌 백엔드에서 드러납니다. `head`·`delete`·`copy`는 거의 같고, presign은 조금 다르고, 재개 업로드는 크게 다릅니다. S3 멀티파트는 파트 번호를 쓰고, GCS resumable은 세션 URI와 오프셋을 씁니다.
- 그래서 더미는 일부러 NCP와 반대 방식으로 만듭니다. 두 구현이 모두 멀티파트였다면 계약이 다른 방식도 감당하는지 시험하지 못합니다. 더미는 진짜 GCS가 아니고 계약의 모양만 시험합니다. 네트워크와 자격증명 없이 실행되므로 오프라인 개발에도 쓸 수 있습니다.
- 어댑터는 판정하지 않습니다. 버킷 선택(`media.transcode.derived_bucket`)과 매체 선택(`media.resolver`)은 도메인의 판정입니다. 어댑터가 판정을 하면 백엔드별 분기가 도메인으로 퍼집니다.
- 어댑터는 DB, 설정, 도메인을 import하지 않습니다. NCP 어댑터는 boto3 클라이언트를 인자로 받습니다. 클라이언트는 `media/storage.py`의 `_client()`가 만든 것을 그대로 넘깁니다. boto3 클라이언트를 따로 만들면 NCP가 요구하는 체크섬 설정(`request_checksum_calculation`)이 빠집니다. 그러면 moto 테스트는 통과하고 운영에서만 AccessDenied가 납니다.
- `head`는 boto3 응답 dict가 아니라 `ObjectInfo`를 돌려줍니다. 도메인이 백엔드 고유의 키(`ContentLength`·`ETag`)를 읽으면 백엔드를 바꿀 수 없기 때문입니다.

## 의존성

- 부르는 것: `reborn_contracts.storage_backend`, 표준 라이브러리, botocore 예외(`ncp.py`)
- 부르는 쪽: `media/storage.py`(`_adapter`), 계약 테스트 `tests/stacks/test_storage_contract.py`. 계약 테스트는 두 구현을 같은 테스트로 실행하고, NCP는 인프로세스 moto로 시험합니다. 선언한 능력(`SERVER_SIDE_COPY` 등)이 있으면 그 동작을 확인하고, 없으면 능력이 없다는 것을 확인합니다.

## 남은 일

- `media/storage.py`의 일부 경로(멀티파트 시작·파트 서명, 폴더 나열, 객체 읽기)는 아직 어댑터를 거치지 않고 boto3를 직접 부릅니다. 관문 안쪽만 바꾸면 되므로 호출부는 바뀌지 않습니다.
- 멀티파트 복사는 파트마다 `CopySourceIfMatch`(원본 ETag)를 보냅니다. 이 동작은 moto로만 확인했고, NCP 버킷에서 5 GiB를 넘는 객체로는 확인하지 않았습니다.
- 업로드 방식(`begin_upload`의 `kind`)을 API에 드러내는 일이 남았습니다. 지금 화면의 업로드 큐는 S3 멀티파트를 전제로 합니다.
- 실제 GCS에서 S3 호환 XML API로 어디까지 되는지는 확인하지 않았습니다. 확인하려면 GCS 자격증명이 필요합니다.
