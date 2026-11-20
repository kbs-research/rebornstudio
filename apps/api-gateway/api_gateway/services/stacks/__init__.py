"""교체 가능한 외부 스택 바운디드 컨텍스트. 외부 연결 설정과 저장소 어댑터를 둔다.

서브모듈:
- `connections`: 조직별 외부 연결(저장소·캡션 검수 모델 등) 설정, 자격증명 점검, 주기 재점검.
- `storage_adapters`: 저장소 백엔드를 `reborn_contracts.storage_backend` 계약으로 옮기는 어댑터.

어댑터는 형식 변환만 하고 판정은 소유 도메인에 둔다.

서브모듈을 직접 import한다.

    from api_gateway.services.stacks import connections as connections_service
    from api_gateway.services.stacks.storage_adapters.ncp import NcpStorage

주의: 여기서 서브모듈을 eager import하지 않는다. 이유는 `media/__init__.py` 주석에 있다.
`media.storage`는 `storage_adapters.ncp`만 쓴다. 패키지를 열 때 `connections`까지 불러오면
`caption_qc`와 `ops.diagnostics`도 함께 import된다.
"""

__all__ = ["connections", "storage_adapters"]
