"""网页链接导入：粘贴招聘页 URL → 打开页面 → 提取正文 → LLM 结构化 → 入库。

**为什么需要它（数据来源多元化）**：爬虫能抓列表、视觉能读截图，但现实中更常见的是
「我收藏了一个招聘链接」——把 URL 粘进来系统就能入库并保留链接与 HTML 快照，
后续由 job_checker 周期性核查该岗位是否还在，形成「采集 → 入库 → 存活追踪」闭环。

实现要点：
- 用 Playwright 打开真实浏览器渲染页面（招聘详情页普遍是前端渲染，裸 HTTP 拿不到内容），
  与 BossCrawler 共用同一套浏览器能力；页面地址与渲染后的 HTML 快照一并落库
  （Job.url / Job.detail_html），供核查与合规留痕；
- 正文提取后走 GLM 文本结构化（复用视觉识别的 JSON 容错工具），无 key 时降级为
  规则兜底（仅记录标题/公司/城市等粗字段）——识别链路不强依赖密钥；
- 入库走既有 create_job 的 job_key 去重口径，同一岗位从链接 / 视觉 / CSV 进入不重复。
"""
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.vision_service import _parse_json_reply, _normalize_job

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "你是招聘信息提取助手。阅读下面的招聘网页正文，提取岗位信息，严格输出 JSON（不要 Markdown 代码块）。"
    "字段："
    "  title: 岗位名称；"
    "  company_name: 公司名称；"
    "  city: 工作城市；"
    "  salary_min_k: 最低薪资（单位 K，整数，无法识别为 0）；"
    "  salary_max_k: 最高薪资（单位 K，整数）；"
    "  experience: 经验要求原文；"
    "  education: 学历要求；"
    "  skills: 技能要点，逗号分隔；"
    "  description: 岗位职责摘要（200 字内）。"
    "无法识别的字段留空字符串或 null。只输出 JSON 对象本身。\n\n以下是页面正文：\n"
)


def extract_page_text(html: str, max_len: int = 8000) -> str:
    """从渲染后的 HTML 提取可读正文（去标签/脚本/样式/空白）。"""
    if not html:
        return ""
    # 先剔除脚本与样式，避免把 JS/CSS 内容当正文
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


async def open_page_text(url: str) -> tuple:
    """用 Playwright 打开 URL，返回 (正文文本, 渲染后完整 HTML)。

    返回 HTML 快照用于持久化（核查时可比对“内容变化”）；正文用于 LLM 抽取。
    打开失败/渲染失败抛 RuntimeError，由路由层转业务错误。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright 未安装：pip install playwright && playwright install chromium"
        )

    async with async_playwright() as p:
        launch_kwargs = {"headless": True}
        proxy = settings.crawl_proxy
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = await p.chromium.launch(**launch_kwargs)
        try:
            page = await browser.new_page(user_agent=settings_crawler_ua(), viewport={"width": 1440, "height": 900})
            await page.goto(url, timeout=settings.crawl_timeout * 1000, wait_until="domcontentloaded")
            # 给前端渲染留时间（详情页多为 SPA/异步加载）
            await page.wait_for_timeout(3000)
            html = await page.content()
        finally:
            await browser.close()

    if not html:
        raise RuntimeError("页面加载失败：未获取到内容")
    return extract_page_text(html), html


def settings_crawler_ua() -> str:
    """取一个稳定的 PC UA（避免每次导入都换 UA 被风控）。"""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


async def extract_job_from_text(page_text: str) -> dict:
    """LLM 结构化抽取；无 key 时降级为规则粗提取（不抛异常）。"""
    if not settings.zhipuai_api_key:
        logger.warning("未配置 ZHIPUAI_API_KEY：链接导入走规则粗提取")
        return _rule_extract(page_text)

    import httpx

    payload = {
        "model": settings.zhipuai_model,  # glm-4-flash（文本模型，便宜免费）
        "messages": [
            {"role": "user", "content": EXTRACT_PROMPT + page_text},
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.zhipuai_api_key}",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(settings.zhipuai_api_url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return _normalize_job(_parse_json_reply(content))
    except Exception as e:
        logger.warning("链接导入 LLM 抽取失败，降级规则提取：%s", e)
        return _rule_extract(page_text)


_RULE_PATTERNS = {
    # 前瞻匹配到「下一个字段名：」之前（字段名用长名优先，兼容“经验要求：”），
    # 兼容字段之间用空格/标点分隔；无后续字段则匹配到结尾。
    "title": [r"岗位名称[:：]\s*(.+?)(?=\s*(?:公司名称|公司|工作城市|城市|薪资|经验要求|经验|学历要求|学历)[:：]|$)", r"职位[:：]\s*(.+?)(?=\s*(?:公司名称|公司|工作城市|城市|薪资|经验要求|经验|学历要求|学历)[:：]|$)"],
    "company_name": [r"公司名称[:：]\s*(.+?)(?=\s*(?:岗位|职位|工作城市|城市|薪资|经验要求|经验|学历要求|学历)[:：]|$)", r"公司[:：]\s*(.+?)(?=\s*(?:岗位|职位|工作城市|城市|薪资|经验要求|经验|学历要求|学历)[:：]|$)"],
    "city": [r"工作城市[:：]\s*(.+?)(?=\s*(?:经验要求|经验|学历要求|学历|薪资)[:：]|$)", r"城市[:：]\s*(.+?)(?=\s*(?:经验要求|经验|学历要求|学历|薪资)[:：]|$)", r"地点[:：]\s*(.+?)(?=\s*(?:经验要求|经验|学历要求|学历|薪资)[:：]|$)"],
    "experience": [r"经验要求[:：]\s*(.+?)(?=\s*(?:学历要求|学历|薪资|工作城市|城市)[:：]|$)", r"经验[:：]\s*(.+?)(?=\s*(?:学历要求|学历|薪资|工作城市|城市)[:：]|$)"],
    "education": [r"学历要求[:：]\s*(.+?)(?=\s*(?:经验要求|经验|薪资|工作城市|城市)[:：]|$)", r"学历[:：]\s*(.+?)(?=\s*(?:经验要求|经验|薪资|工作城市|城市)[:：]|$)"],
}


def _rule_extract(text: str) -> dict:
    """无 LLM 时的兜底：正则粗提关键字段，保证至少能入一条粗记录。"""
    result = {"title": "", "company_name": "", "city": "", "experience": "",
              "education": "", "skills": "", "description": "", "source_site": "url"}
    for field, patterns in _RULE_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, text)
            if m and m.group(1).strip():
                result[field] = m.group(1).strip()[:100]
                break
    salary = re.search(r"(\d{2})\s*[-~—]\s*(\d{2})\s*K", text, re.IGNORECASE) or \
        re.search(r"(\d{2})\s*[-~—]\s*(\d{2})\s*k", text, re.IGNORECASE)
    if salary:
        result["min_salary"] = int(salary.group(1)) * 1000
        result["max_salary"] = int(salary.group(2)) * 1000
    return result


async def import_job_from_url(db: AsyncSession, url: str, validate: bool = True) -> dict:
    """链接导入主编排：打开页面 → 抽取 → （可选）入库。

    返回 {"result": {...}, "saved": bool, "job_id": id|None, "page_html": bool}
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("请输入合法的 http/https 链接")

    page_text, page_html = await open_page_text(url)
    if not page_text:
        raise RuntimeError("未能从页面提取到文本内容")

    job_data = await extract_job_from_text(page_text)

    if not validate:
        return {"result": job_data, "saved": False, "job_id": None,
                "page_saved": False, "url": url}

    from app.schemas.job import JobCreateRequest
    from app.services.job_service import JobService

    job_data.update({"source_site": "url", "url": url})
    try:
        create_req = JobCreateRequest(
            title=job_data.get("title") or "",
            company_name=job_data.get("company_name") or "",
            source_site="url",
            city=job_data.get("city") or "",
            experience=job_data.get("experience") or "",
            education=job_data.get("education") or "",
            min_salary=job_data.get("min_salary") or None,
            max_salary=job_data.get("max_salary") or None,
            skills=job_data.get("skills") or "",
            job_desc=job_data.get("description") or "",
            url=url,
        )
        created = await JobService.create_job(db, create_req)
        # 补存渲染后的 HTML 快照（核查“内容是否变化”用）；create_job 未覆盖该列，
        # 这里直接 update。
        from sqlalchemy import update
        from app.models.job import Job
        await db.execute(
            update(Job).where(Job.id == created.id).values(detail_html=page_html[:60000])
        )
        await db.commit()
        return {"result": job_data, "saved": True, "job_id": created.id,
                "page_saved": True, "url": url}
    except ValueError as e:
        raise RuntimeError(str(e))
