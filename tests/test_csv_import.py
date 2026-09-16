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