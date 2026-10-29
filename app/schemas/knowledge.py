from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class KnowledgeBaseCreate(BaseModel):
    """知识条目创建入参。

    **字段上限的取值依据**：
    - `question` = String(500)、`tags` = String(300)、`source` = String(20)，与列宽对齐；
    - `answer` 库里是 Text，按业务合理性封顶 5000 —— 知识条目会被**召回后拼进 LLM prompt**，
      不设上限等于让外部输入决定 token 成本与上下文长度。

    **超限返回 422，不截断**：截断会让用户以为「存进去了」，实际答案已被改写。
    """

    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=5000)
    tags: str = Field("", max_length=300)
    source: str = Field("manual", max_length=20)
    quality_score: int = 0


class KnowledgeBaseUpdate(BaseModel):
    """知识条目局部更新入参；上限与创建一致。"""

    question: Optional[str] = Field(None, max_length=500)
    answer: Optional[str] = Field(None, max_length=5000)
    tags: Optional[str] = Field(None, max_length=300)
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