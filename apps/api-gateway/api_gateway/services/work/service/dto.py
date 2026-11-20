"""WorkItem ORM을 계약 DTO로 바꾸는 변환. 큐 코어와 검수가 함께 쓴다."""
from reborn_contracts import WorkItemDTO

from api_gateway.models import WorkItem


def _to_dto(w: WorkItem) -> WorkItemDTO:
    """WorkItem 모델을 WorkItemDTO로 바꾼다."""
    return WorkItemDTO(
        work_item_id=w.work_item_id,
        video_id=w.video_id,
        project_id=w.project_id,
        clip_id=w.clip_id,
        status=w.status,
        claimed_by=w.claimed_by,
        claimed_at=w.claimed_at,
        submitted_at=w.submitted_at,
        review_claimed_by=w.review_claimed_by,
        rejection_count=w.rejection_count,
        payload=w.payload,
        draft=w.draft,
        review_draft=w.review_draft,
        created_at=w.created_at,
    )
