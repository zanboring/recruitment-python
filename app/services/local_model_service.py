from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.job import Job

FAQ = {
    "项目介绍": "这是一个智能招聘数据分析平台，包含数据爬取、存储、分析、AI问答全流程。后端基于FastAPI + SQLAlchemy，前端使用Vue3 + Tailwind CSS，AI模块对接智谱GLM-4和Ollama本地模型。",
    "技术选型": "后端：FastAPI + SQLAlchemy + MySQL；前端：Vue3 + Tailwind CSS；AI：GLM-4-Flash + Ollama；爬虫：Playwright；部署：Docker + Uvicorn。",
    "核心功能": "岗位管理（CRUD）、数据可视化分析、AI智能问答、岗位推荐（Jaccard相似度）、薪资预测、BOSS直聘爬虫采集。",
    "架构设计": "前后端分离，RESTful API，JWT认证，三级AI降级（Ollama→GLM-4→规则引擎），异步数据库操作，定时任务调度。",
    "推荐算法": "基于Jaccard相似度的多因子加权推荐，技能匹配权重70%，教育匹配20%，经验匹配10%，最大候选集500条。",
    "爬虫模块": "基于Playwright爬取BOSS直聘，支持反爬策略、数据清洗、技能提取、高级岗位过滤、无效岗位过滤、岗位下架自动标记。",
    "AI模块": "对接智谱GLM-4 API，支持SSE流式输出，Ollama本地模型备选，规则引擎兜底；知识库语义检索（embedding-3 向量化 + 余弦相似度，失败降级关键词）。",
    "部署方案": "Docker部署，MySQL数据库，Uvicorn ASGI服务器，环境变量配置，支持Linux和Windows环境。",
}


class LocalModelService:
    @staticmethod
    async def chat(message: str, db: AsyncSession = None) -> str:
        message_lower = message.lower()

        for key, answer in FAQ.items():
            if key in message:
                return answer

        if "薪资" in message and ("分析" in message or "统计" in message):
            return await LocalModelService._salary_analysis(db)
        if "岗位" in message and ("统计" in message or "分析" in message or "数量" in message):
            return await LocalModelService._job_analysis(db)
        if "技能" in message and ("需求" in message or "排行" in message or "分析" in message or "标签" in message):
            return await LocalModelService._skill_analysis(db)
        if "城市" in message and ("分布" in message or "分析" in message):
            return await LocalModelService._city_analysis(db)
        if "学历" in message and ("要求" in message or "分布" in message or "分析" in message):
            return await LocalModelService._education_analysis(db)

        return "我是一个招聘数据分析助手，可以回答关于招聘数据、薪资分析、岗位推荐等问题。请问您想了解什么？"

    @staticmethod
    async def _salary_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据，请连接数据库后重试。"
        result = await db.execute(
            select(func.avg(Job.min_salary), func.avg(Job.max_salary), func.count()).where(Job.job_status == "ACTIVE")
        )
        row = result.first()
        if not row or row[2] == 0:
            return "当前没有活跃岗位数据。"
        avg_min = int(row[0]) if row[0] else 0
        avg_max = int(row[1]) if row[1] else 0
        return f"当前共有 {row[2]} 个在招岗位，平均薪资范围：{avg_min}-{avg_max} 元/月。"

    @staticmethod
    async def _job_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据，请连接数据库后重试。"
        result = await db.execute(
            select(func.count()).where(Job.job_status == "ACTIVE")
        )
        active_count = result.scalar() or 0

        result = await db.execute(
            select(func.count()).where(Job.job_status == "NEW")
        )
        new_count = result.scalar() or 0

        result = await db.execute(
            select(func.count()).where(Job.job_status == "OFFLINE")
        )
        offline_count = result.scalar() or 0

        return f"岗位统计：总计 {active_count + new_count + offline_count} 个，活跃 {active_count} 个，新发布 {new_count} 个，已下架 {offline_count} 个。"

    @staticmethod
    async def _skill_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据，请连接数据库后重试。"
        result = await db.execute(
            select(Job.skills, func.count()).where(Job.job_status == "ACTIVE", Job.skills.isnot(None), Job.skills != "")
            .group_by(Job.skills).order_by(func.count().desc()).limit(10)
        )
        rows = result.all()
        if not rows:
            return "当前没有技能标签数据。"
        skills_str = "; ".join([f"{row[0].split(',')[0] if ',' in row[0] else row[0]}({row[1]})" for row in rows])
        return f"技能需求TOP10：{skills_str}。"

    @staticmethod
    async def _city_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据，请连接数据库后重试。"
        result = await db.execute(
            select(Job.city, func.count()).where(Job.job_status == "ACTIVE", Job.city.isnot(None))
            .group_by(Job.city).order_by(func.count().desc()).limit(10)
        )
        rows = result.all()
        if not rows:
            return "当前没有城市数据。"
        cities_str = "; ".join([f"{row[0]}({row[1]}个岗位)" for row in rows])
        return f"城市分布TOP10：{cities_str}。"

    @staticmethod
    async def _education_analysis(db: AsyncSession) -> str:
        if not db:
            return "暂无数据，请连接数据库后重试。"
        result = await db.execute(
            select(Job.education, func.count()).where(Job.job_status == "ACTIVE", Job.education.isnot(None))
            .group_by(Job.education).order_by(func.count().desc())
        )
        rows = result.all()
        if not rows:
            return "当前没有学历要求数据。"
        edu_str = "; ".join([f"{row[0]}({row[1]}个岗位)" for row in rows])
        return f"学历要求分布：{edu_str}。"