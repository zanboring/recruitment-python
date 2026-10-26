from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class KnowledgeBaseCreate(BaseModel):
    question: str
    answer: str
    tags: str = ""
    source: str = "manual"
    quality_score: int = 0


class KnowledgeBaseUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    tags: Optional[str] = None
    quality_score: Optional[int] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    question: str
    answer: str
    tags: str
    source: str
    usage_count: int
    status: int
    quality_score: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}