from pydantic import BaseModel, Field, field_serializer, model_validator
from typing import Optional
from datetime import datetime

# Java 版前端发送的 camelCase 查询参数 → 本服务原生 snake_case 字段名
_CAMEL_TO_SNAKE = {
    "companyName": "company_name",
    "sourceSite": "source_site",
    "minSalary": "min_salary",
    "maxSalary": "max_salary",
    "pageNum": "page_num",
    "pageSize": "page_size",
}


class JobQueryDTO(BaseModel):
    """岗位查询条件。

    除原生 snake_case 外，还接受 Java 版前端发送的 camelCase
    （companyName / pageSize …），由下方 before-validator 统一归一化。

    为什么不用 `Field(alias=...)`：这些字段是 Optional，而 Optional 会被展开成
    Union —— Pydantic 对 Union 成员上的 alias / validation_alias 会发
    UnsupportedFieldAttributeWarning（实测功能仍生效，但会产生大量告警噪音）。
    用 model_validator 做键名映射既兼容两种命名，又不产生任何告警。
    """

    keyword: Optional[str] = None
    city: Optional[str] = None
    company_name: Optional[str] = None
    source_site: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    status: Optional[str] = None
    page_num: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_case(cls, data):
        """把 camelCase 入参补成 snake_case；已经是 snake_case 的不覆盖。"""
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for camel, snake in _CAMEL_TO_SNAKE.items():
            if camel in normalized and snake not in normalized:
                normalized[snake] = normalized[camel]
        return normalized


class JobCreateRequest(BaseModel):
    company_id: Optional[int] = None
    title: str
    company_name: Optional[str] = None
    source_site: str
    city: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    salary_unit: Optional[str] = "元"
    skills: Optional[str] = None
    job_desc: Optional[str] = None
    url: Optional[str] = None


class JobUpdateRequest(BaseModel):
    company_id: Optional[int] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    source_site: Optional[str] = None
    job_status: Optional[str] = None
    city: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    salary_unit: Optional[str] = None
    skills: Optional[str] = None
    job_desc: Optional[str] = None
    url: Optional[str] = None


class JobResponse(BaseModel):
    id: int
    company_id: Optional[int]
    title: str
    company_name: Optional[str]
    source_site: str
    job_key: str
    job_status: str
    city: Optional[str]
    experience: Optional[str]
    education: Optional[str]
    min_salary: Optional[float]
    max_salary: Optional[float]
    salary_unit: Optional[str]
    skills: Optional[str]
    job_desc: Optional[str]
    url: Optional[str]
    publish_time: Optional[datetime]
    created_at: datetime

    @field_serializer("publish_time", "created_at")
    def serialize_datetime(self, value: Optional[datetime]) -> Optional[str]:
        if value:
            return value.isoformat()
        return None

    model_config = {"from_attributes": True}
