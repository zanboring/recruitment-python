"""请求体长度上限测试（W-2）。

**背景**：`app/schemas/job.py` 与 `app/schemas/knowledge.py` 此前**完全没有** max_length
（`grep -rn max_length app/schemas/` 只命中 `auth.py`）。后果有两层：
1. 超长字符串能一路入库（SQLite 不校验 VARCHAR 长度，MySQL 才会报错 —— 两种库行为不一致）；
2. 岗位描述与知识条目会被**拼进 LLM prompt**，等于把 token 成本交给外部输入决定。

**判据**：超限必须 422（明确拒绝），**不能悄悄截断** —— 截断会让调用方以为存进去了。
"""
import pytest
from pydantic import ValidationError

from app.schemas.job import JOB_DESC_MAX, JobCreateRequest, JobQueryDTO, JobUpdateRequest
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate
from tests.helpers import admin_token, auth_headers, create_job


def _job(**overrides):
    data = {"title": "Java 后端工程师", "source_site": "boss"}
    data.update(overrides)
    return data


# --------------------------------------------------------------------------
# 1. 岗位：模型层
# --------------------------------------------------------------------------


def test_标题超长被拒():
    with pytest.raises(ValidationError) as ei:
        JobCreateRequest(**_job(title="标" * 201))
    assert ei.value.errors()[0]["type"] == "string_too_long"


def test_标题恰好上限通过():
    """上限是「≤」，正好 200 必须放行 —— 否则就是白白砍掉合法数据。"""
    assert JobCreateRequest(**_job(title="标" * 200)).title == "标" * 200


def test_岗位描述超长被拒():
    with pytest.raises(ValidationError) as ei:
        JobCreateRequest(**_job(job_desc="描" * 5001))
    assert ei.value.errors()[0]["type"] == "string_too_long"


def test_技能超长被拒():
    with pytest.raises(ValidationError):
        JobCreateRequest(**_job(skills="技" * 501))


def test_公司名与城市超长被拒():
    with pytest.raises(ValidationError):
        JobCreateRequest(**_job(company_name="公" * 201))
    with pytest.raises(ValidationError):
        JobCreateRequest(**_job(city="城" * 51))


def test_更新请求同样受限():
    with pytest.raises(ValidationError):
        JobUpdateRequest(job_desc="描" * 5001)
    assert JobUpdateRequest(title="标" * 200).title == "标" * 200


def test_查询参数超长被拒():
    with pytest.raises(ValidationError):
        JobQueryDTO(keyword="k" * 101)
    assert JobQueryDTO(keyword="k" * 100).keyword == "k" * 100


def test_文本字段都带上限():
    """回归闸门：以后往这两个 schema 加文本字段却忘了 max_length，这里会红。"""
    cases = [
        (
            JobCreateRequest,
            ["title", "company_name", "source_site", "city", "experience",
             "education", "salary_unit", "skills", "job_desc", "url"],
        ),
        (
            JobUpdateRequest,
            ["title", "company_name", "source_site", "job_status", "city", "experience",
             "education", "salary_unit", "skills", "job_desc", "url"],
        ),
        (KnowledgeBaseCreate, ["question", "answer", "tags", "source"]),
        (KnowledgeBaseUpdate, ["question", "answer", "tags"]),
    ]
    missing = []
    for model, names in cases:
        for name in names:
            metadata = model.model_fields[name].metadata
            if not any(getattr(m, "max_length", None) for m in metadata):
                missing.append(f"{model.__name__}.{name}")
    assert not missing, f"以下字段缺少 max_length：{missing}"


# --------------------------------------------------------------------------
# 2. 知识库：模型层
# --------------------------------------------------------------------------


def test_知识问题超长被拒():
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(question="问" * 501, answer="答")


def test_知识答案超长被拒():
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(question="问", answer="答" * 5001)


def test_知识标签与来源超长被拒():
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(question="问", answer="答", tags="标" * 301)
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(question="问", answer="答", source="s" * 21)


def test_知识恰好上限通过():
    item = KnowledgeBaseCreate(question="问" * 500, answer="答" * 5000)
    assert len(item.question) == 500 and len(item.answer) == 5000


# --------------------------------------------------------------------------
# 3. 接口层：必须是 422（而不是 500 / 静默截断）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_创建岗位超长标题返回422(client, db_session):
    token = await admin_token(client, db_session)
    resp = await create_job(client, token, title="标" * 201)
    assert resp.status_code == 422
    # 明确拒绝的语义：报文里要指出是哪一类错误
    assert "string_too_long" in resp.text or "too_long" in resp.text


@pytest.mark.asyncio
async def test_创建知识条目超长答案返回422(client, db_session):
    token = await admin_token(client, db_session)
    resp = await client.post(
        "/api/knowledge",
        json={"question": "问", "answer": "答" * 5001},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_正常长度仍可创建(client, db_session):
    """防止「一刀切」误伤：正常长度必须照常 200。"""
    token = await admin_token(client, db_session)
    resp = await create_job(client, token, title="Python 后端工程师", job_desc="负责后端开发")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_用户直接提交超长描述仍返回422(client, db_session):
    """W-6 回归：抽取链路改成「截断」后，**用户直传**超长必须仍然 422。

    两条路径的语义不能混：
    - 用户直传超长 → 用户有能力改小重提，422 是正确反馈（本节全部用例守住这条）；
    - 系统抽取超长 → 无「改小重提」入口，由 url_import_service 截断 + 留痕
      （见 tests/test_job_checker.py::test_链接导入_超长描述被截断且留痕）。
    这个用例存在的意义：证明「为抽取链路放宽 schema」这种改法一旦出现就会立刻红。
    """
    token = await admin_token(client, db_session)
    resp = await create_job(client, token, job_desc="描" * (JOB_DESC_MAX + 1))
    assert resp.status_code == 422
