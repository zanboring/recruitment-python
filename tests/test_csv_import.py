# -*- coding: utf-8 -*-
"""CSV 导入测试：表头解析 / 幂等去重 / 容错。"""
import pytest

from app.services import export_service


CSV_SAMPLE = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,SpringBoot,offline,test,负责后端开发\n"
    "Python工程师,测试公司2,深圳,18000,30000,3-5年,本科,Python,FastAPI,offline,test,负责AI服务\n"
)


@pytest.mark.asyncio
async def test_csv_import_success(db_session):
    result = await export_service.import_jobs_from_csv(
        db_session, CSV_SAMPLE.encode("utf-8")
    )
    assert result["success"] == 2
    assert result["fail"] == 0


@pytest.mark.asyncio
async def test_csv_import_idempotent(db_session):
    data = CSV_SAMPLE.encode("utf-8-sig")  # 带 BOM 也应能解析
    first = await export_service.import_jobs_from_csv(db_session, data)
    second = await export_service.import_jobs_from_csv(db_session, data)
    assert first["success"] == 2
    # 二次导入全部去重
    assert second["success"] == 0
    assert second["skip"] == 2


@pytest.mark.asyncio
async def test_csv_import_empty(db_session):
    with pytest.raises(ValueError):
        await export_service.import_jobs_from_csv(db_session, b"")


# ---------------------------------------------------------------------------
# 状态语义：导入不是「观察」，不能改变已存在记录的状态
#
# 业务决策（2026-11-08）：状态只能由「**观察到它还在**」驱动。
# 爬虫重爬到 = 它现在确实挂着；扫到一次 = 它还活着。
# 而「我手上有一份旧导出表」只说明曾经有过，**不构成它还在的证据**。
#
# 这条原则与行业无关 —— 换成商品、房源、设备台账都一样：
# 一份历史快照不该让已消失的实体重新出现。
# ---------------------------------------------------------------------------

CSV_ONE_JOB = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,2026-01-01,负责后端开发\n"
)


def _job_key_of_csv_row() -> str:
    """按导入器同一套指纹规则算出 job_key（不要手写字符串）。"""
    from app.crawlers.cleaner import generate_job_key

    return generate_job_key("offline", "Java开发工程师", "测试公司", "长沙")


@pytest.mark.asyncio
async def test_csv导入不复活已下架岗位(db_session):
    """已下架的岗位，被旧导出表再导一次，状态必须仍是 CLOSED。

    为什么这条现在就要锁：当前导入器遇到同 job_key 直接 skip，**碰巧**是对的。
    但 qoder 复审提出的方案是把导入器汇入 `save_job`，而 `save_job` 命中存量时
    无条件写 `job_status = "ACTIVE"` —— 一旦照那个方案改，用户导一份旧表
    就会把整批已下架岗位复活，而且没有任何测试会红。
    """
    from app.models.job import Job

    existing = Job(
        title="Java开发工程师",
        company_name="测试公司",
        city="长沙",
        source_site="offline",
        job_key=_job_key_of_csv_row(),
        job_status="CLOSED",
        min_salary=15000,
        max_salary=25000,
    )
    db_session.add(existing)
    await db_session.commit()
    job_id = existing.id

    result = await export_service.import_jobs_from_csv(
        db_session, CSV_ONE_JOB.encode("utf-8")
    )

    assert result["success"] == 0, "同一岗位不该被重复插入"
    assert result["skip"] == 1

    fresh = await db_session.get(Job, job_id)
    assert fresh is not None
    assert fresh.job_status == "CLOSED", (
        "导入把已下架岗位复活了：导入只说明「曾经有过」，不说明「它还在」"
    )


@pytest.mark.asyncio
async def test_csv导入不覆盖存量字段(db_session):
    """旧导出表不能覆盖库里更新的值（薪资 / 标题等）。

    与上一条同源：导入是「补充新记录」，不是「用旧快照回写」。
    """
    from app.models.job import Job

    existing = Job(
        title="Java开发工程师",
        company_name="测试公司",
        city="长沙",
        source_site="offline",
        job_key=_job_key_of_csv_row(),
        job_status="ACTIVE",
        min_salary=30000,      # 库里是更新后的值
        max_salary=50000,
    )
    db_session.add(existing)
    await db_session.commit()
    job_id = existing.id

    await export_service.import_jobs_from_csv(db_session, CSV_ONE_JOB.encode("utf-8"))

    fresh = await db_session.get(Job, job_id)
    assert float(fresh.min_salary) == 30000, "旧导出表覆盖了库里更新的薪资"
    assert float(fresh.max_salary) == 50000