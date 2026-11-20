"""승인분을 납품 패키지로 묶어 스토리지에 올리는 납품물 생성 바운디드 컨텍스트.

서브모듈:
- `export_service`: 납품 요청·처리(`create`·`process_pending`)와 완료 목록·요약.
- `export_package`: 패키지 내용, 납품 행 검사(`check_row`), 제외 사유, 무결성 검사.

reporting 컨텍스트에서 쓰기(`exports` 테이블)를 담당한다.
읽기 전용 지표는 `analytics`가 담당한다.

서브모듈을 직접 import한다.

    from api_gateway.services.exports import export_service
    from api_gateway.services.exports.export_package import check_row

주의: 여기서 서브모듈을 eager import하지 않는다. 이유는 `media/__init__.py` 주석에 있다.
`caption_qc.runner`는 `export_package`만 쓴다. 이 패키지를 import할 때 `export_service`까지
불러오면 스토리지와 분석 모듈도 함께 로드된다.
"""

__all__ = ["export_package", "export_service"]
