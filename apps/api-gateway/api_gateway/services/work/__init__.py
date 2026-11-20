"""작업 파이프라인 바운디드 컨텍스트. 라벨링·검수 단계를 다룬다.

`service`는 work_item의 claim·submit(큐 코어)을 맡는다.
`review`는 검수 판정을, `deident_reports`는 비식별화 신고 처리를 맡는다.
`repeated_rejects`는 반려 누적 목록을 맡는다.

서브모듈은 직접 import 한다. 호출부를 읽기 쉽도록 별칭을 붙인다.

    from api_gateway.services.work import service as work_service
    from api_gateway.services.work import review as review_service

주의: 이 파일은 서브모듈을 eager import 하지 않는다(`media/__init__.py`와 같은 방식).
`work.deident_reports` → `media.clip`, `media.video` → `work.service`처럼 서로 import하므로
여기서 미리 import하면 순환 import가 생긴다.
"""
