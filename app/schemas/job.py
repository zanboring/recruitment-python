from pydantic import BaseModel, field_serializer
from typing import Optional
from datetime import datetime


class JobQueryDTO(BaseModel):
    keyword: Optional[str] = None
    city: Optional[str] = None
    company_name: Optional[str] = None
    source_site: Optional[str] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    status: Optional[str] = None
    page_num: int = 1
    page_size: int = 20


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
