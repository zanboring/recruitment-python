from pydantic import BaseModel, Field, field_serializer, model_validator
from typing import Optional
from datetime import datetime

# ---------------------------------------------------------------------------
# 字段长度上限 —— **唯一真源**
#
# 两个使用方，语义**不同**，不要混为一谈：
#   1. 本文件的 `Field(max_length=...)`：**用户直接提交**超限 → 422，
#      让用户改小重提（这是校验，不能放宽）；
#   2. **系统自己抽取**的文本（链接导入的 LLM 结果等）→ 按同一组常量**截断 + 留痕**，
#      见 `app/services/url_import_service.py::_clamp_extracted`。
#      对模型产物返回 422 是说不通的：页面明明抓成功了，用户却「确认入库」失败。
#
# 为什么必须只有一处数字：一旦 service 里再硬编码一份，就会出现
# 「schema 改了、截断没改」→ 抽取结果照样 422 的回归。
# ---------------------------------------------------------------------------
JOB_TITLE_MAX = 200
JOB_COMPANY_NAME_MAX = 200
JOB_SOURCE_SITE_MAX = 50
JOB_CITY_MAX = 50
JOB_EXPERIENCE_MAX = 50
JOB_EDUCATION_MAX = 50
JOB_STATUS_MAX = 20
JOB_SALARY_UNIT_MAX = 10
JOB_SKILLS_MAX = 500
JOB_DESC_MAX = 5000
JOB_URL_MAX = 500
JOB_KEYWORD_MAX = 100

# schema 字段名 → 上限。需要「按字段截断」的调用方直接查这张表，
# 避免自己维护第二份映射。
JOB_TEXT_LIMITS = {
    "title": JOB_TITLE_MAX,
    "company_name": JOB_COMPANY_NAME_MAX,
    "source_site": JOB_SOURCE_SITE_MAX,
    "city": JOB_CITY_MAX,
    "experience": JOB_EXPERIENCE_MAX,
    "education": JOB_EDUCATION_MAX,
    "job_status": JOB_STATUS_MAX,
    "salary_unit": JOB_SALARY_UNIT_MAX,
    "skills": JOB_SKILLS_MAX,
    "job_desc": JOB_DESC_MAX,
    "url": JOB_URL_MAX,
}

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

    keyword: Optional[str] = Field(None, max_length=JOB_KEYWORD_MAX)
    city: Optional[str] = Field(None, max_length=JOB_CITY_MAX)
    company_name: Optional[str] = Field(None, max_length=JOB_COMPANY_NAME_MAX)
    source_site: Optional[str] = Field(None, max_length=JOB_SOURCE_SITE_MAX)
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    status: Optional[str] = Field(None, max_length=JOB_STATUS_MAX)
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
    """岗位创建入参。

    **字段上限的取值依据**（全为「用户可直接提交」的文本）：
    - 与数据库列宽对齐的：`title` / `company_name` = String(200)、`city` / `experience` /
      `education` / `source_site` = String(50)、`skills` = String(500)、`url` = String(500)；
    - 文本大字段（`job_desc`，库里是 Text）按业务合理性封顶 5000 —— 长于这个的
      「岗位描述」已无信息价值，且它会被拼进 LLM prompt，等于把成本交给外部输入控制。

    **超限直接 422，不做截断**：截断会让调用方以为「存进去了」，实际内容已被悄悄改写，
    比明确拒绝更难排查。（`source_site` 上限 50 也顺带挡住了靠塞超长值绕过滤的玩法。）
    """

    company_id: Optional[int] = None
    title: str = Field(..., max_length=JOB_TITLE_MAX)
    company_name: Optional[str] = Field(None, max_length=JOB_COMPANY_NAME_MAX)
    source_site: str = Field(..., max_length=JOB_SOURCE_SITE_MAX)
    city: Optional[str] = Field(None, max_length=JOB_CITY_MAX)
    experience: Optional[str] = Field(None, max_length=JOB_EXPERIENCE_MAX)
    education: Optional[str] = Field(None, max_length=JOB_EDUCATION_MAX)
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    salary_unit: Optional[str] = Field("元", max_length=JOB_SALARY_UNIT_MAX)
    skills: Optional[str] = Field(None, max_length=JOB_SKILLS_MAX)
    job_desc: Optional[str] = Field(None, max_length=JOB_DESC_MAX)
    url: Optional[str] = Field(None, max_length=JOB_URL_MAX)


class JobUpdateRequest(BaseModel):
    """岗位局部更新入参；字段上限与 `JobCreateRequest` 一致（同为用户可提交文本）。"""

    company_id: Optional[int] = None
    title: Optional[str] = Field(None, max_length=JOB_TITLE_MAX)
    company_name: Optional[str] = Field(None, max_length=JOB_COMPANY_NAME_MAX)
    source_site: Optional[str] = Field(None, max_length=JOB_SOURCE_SITE_MAX)
    job_status: Optional[str] = Field(None, max_length=JOB_STATUS_MAX)
    city: Optional[str] = Field(None, max_length=JOB_CITY_MAX)
    experience: Optional[str] = Field(None, max_length=JOB_EXPERIENCE_MAX)
    education: Optional[str] = Field(None, max_length=JOB_EDUCATION_MAX)
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    salary_unit: Optional[str] = Field(None, max_length=JOB_SALARY_UNIT_MAX)
    skills: Optional[str] = Field(None, max_length=JOB_SKILLS_MAX)
    job_desc: Optional[str] = Field(None, max_length=JOB_DESC_MAX)
    url: Optional[str] = Field(None, max_length=JOB_URL_MAX)


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
