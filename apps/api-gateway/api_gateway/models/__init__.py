"""ORM 모델의 공개 목록.

alembic `env.py`는 이 패키지에서 `Base`를 가져온다. 새 모델도 여기서 import한다.
그래야 `Base.metadata`에 등록되어 alembic이 그 테이블을 스키마 비교에 넣는다.
"""
from api_gateway.models.analysis_job import AnalysisJob, AnalysisJobStatus
from api_gateway.models.base import Base
from api_gateway.models.clip import Clip
from api_gateway.models.connection import Connection
from api_gateway.models.event import DomainEvent
from api_gateway.models.export import Export
from api_gateway.models.ingest_batch import IngestBatch
from api_gateway.models.label import Label, Review
from api_gateway.models.media_run import MediaStageRun
from api_gateway.models.org import Organization, Project, ProjectMember
from api_gateway.models.user import PasswordResetToken, User
from api_gateway.models.video import Video
from api_gateway.models.work import CaptionQcRun, WorkItem

__all__ = [
    "Base", "Clip", "DomainEvent", "Export", "Label",
    "AnalysisJob", "AnalysisJobStatus",
    "IngestBatch",
    "MediaStageRun", "Organization", "Project", "ProjectMember",
    "CaptionQcRun", "Connection", "PasswordResetToken", "Review", "User", "Video", "WorkItem",
]
