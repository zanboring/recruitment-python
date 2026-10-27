"""前程无忧（51job）爬虫。

## 为什么用 JSON 接口而不是解析 HTML

51job 的搜索结果页是**前端渲染**的：直接抓 HTML 拿到的 DOM 里没有岗位卡片，
真实数据由页面 JS 再调一次搜索接口后填充。因此按 HTML 选择器解析会得到
「看起来正常但永远是空列表」的结果 —— 属于最难排查的静默失败。

所以这里直接请求它前端自己在用的搜索接口
（``https://we.51job.com/api/job/search-pc``），拿 JSON 再结构化。
顺带的好处是字段是语义化的（``provinceName`` / ``provideSalaryString`` …），
不必依赖易变的 CSS 类名。

## 城市编码的来源与纪律

``CITY_AREA_MAP`` 里的编码**不是猜的**，取自 51job 搜索页 URL 的 ``jobArea``
查询参数，且经三个相互独立的公开来源交叉核对一致（腾讯云社区 / 博客园 /
CSDN 上的 51job 采集实践文章，均为 2020 年之后、站点改版后的编码）。

与 ``app/crawlers/city_map.py`` 同一纪律：**未收录城市必须显式报错**。
给一个「查不到就回退默认城市」的兜底，代价是把别的城市的岗位记在目标城市名下，
数据看起来是好的、只是城市是错的，比直接失败难排查得多。
如需新增城市，请到 51job 站点筛选城市后从 URL 的 ``jobArea=`` 取值，不要凭猜测填写。
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.crawlers.base import BaseCrawler
from app.crawlers.browser import launch
from app.crawlers.city_map import UnsupportedCityError
from app.crawlers.cleaner import clean_job_data, deduplicate_jobs, generate_job_key
from app.crawlers.throttle import await_domain_slot, parse_retry_after
from app.utils.salary import parse_salary_to_monthly as _parse_salary_monthly

logger = logging.getLogger("job51_crawler")

SOURCE_SITE = "51job"

# 51job 搜索接口。api_key=51job 是它前端公开使用的固定值（非私密凭据）。
SEARCH_API = "https://we.51job.com/api/job/search-pc"
PAGE_SIZE = 20

CITY_AREA_MAP = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "天津": "050000",
    "重庆": "060000",
    "南京": "070200",
    "苏州": "070300",
    "杭州": "080200",
    "宁波": "080300",
    "成都": "090200",
    "福州": "110200",
    "济南": "120200",
    "青岛": "120300",
    "合肥": "150200",
    "郑州": "170200",
    "武汉": "180200",
    "长沙": "190200",
    "西安": "200200",
    "哈尔滨": "220200",
    "沈阳": "230200",
    "大连": "230300",
    "长春": "240200",
    "昆明": "250200",
    "东莞": "030800",
}

# 接口字段可能随版本调整，用别名表兜住，避免字段改名就整片解析失败。
_ITEM_ALIASES = {
    "title": ("jobName", "jobTitle", "title"),
    "company_name": ("companyName", "company", "fullCompanyName"),
    "salary": ("provideSalaryString", "salaryString", "salary"),
    "area": ("workAreaString", "jobAreaString", "areaString", "workArea"),
    "experience": ("workYearString", "workYear", "experienceString"),
    "education": ("degreeString", "degree", "educationString"),
    "url": ("jobHref", "jobHrefUrl", "jobUrl", "url"),
    "welfare": ("attributeText", "welfare", "jobWelfare"),
    "description": ("jobDescribe", "jobDescription", "description"),
}


class UnsupportedAreaError(UnsupportedCityError):
    """51job 未收录该城市 —— 继续爬会拿到别的城市的岗位，故直接拒绝。"""


def supported_cities() -> List[str]:
    """返回 51job 已收录的城市清单（供接口暴露与前端下拉使用）。"""
    return sorted(CITY_AREA_MAP)


def get_area_code(city_name: str) -> Optional[str]:
    """城市名 → 51job 的 ``jobArea`` 编码；未收录或为空返回 ``None``。

    **不要**给这里加默认值回退，原因见模块文档。
    """
    if not city_name:
        return None
    return CITY_AREA_MAP.get(str(city_name).strip())


def require_area_code(city_name: str) -> str:
    """取编码，未收录时抛出带支持清单的明确错误。"""
    if not str(city_name or "").strip():
        raise UnsupportedAreaError(
            f"必须指定城市。51job 当前已收录：{', '.join(supported_cities())}"
        )
    code = get_area_code(city_name)
    if code is None:
        raise UnsupportedAreaError(
            f"城市「{city_name}」未收录，无法爬取 51job —— 继续执行会抓到其他城市的数据。"
            f"当前已收录：{', '.join(supported_cities())}。"
            f"如需支持，请在 app/crawlers/job51.py 的 CITY_AREA_MAP 中补充编码"
            f"（从 51job 搜索页 URL 的 jobArea= 参数取值）。"
        )
    return code


# --------------------------------------------------------------------------
# 薪资解析
# --------------------------------------------------------------------------

# 薪资解析：实现已统一到 app/utils/salary.py，这里只做转发。
# 历史教训：本模块与 cleaner 曾各持一套实现 —— 这边认「1-1.5万」，那边只认
# 「8K-12K」，于是 BOSS 的「15-25K·13薪」全部解析成 0，岗位能入库、列表能显示，
# 只是薪资列全空、薪资分布图与高薪过滤静默失效。两处维护必然漂移，故收敛为一份。


def parse_salary_to_monthly(raw) -> Tuple[int, int]:
    """把薪资文本解析成**月薪**区间（元）。

    实现见 ``app.utils.salary``（与 ``cleaner.parse_salary`` 共用一份）。
    保留本函数名是因为既有测试与调用方都引用它；**不要在这里再写一套解析**。
    """
    return _parse_salary_monthly(raw)


# --------------------------------------------------------------------------
# 响应解析
# --------------------------------------------------------------------------


def extract_items(payload) -> Optional[List[Dict]]:
    """从接口响应里取出岗位数组；结构不符返回 ``None``。

    51job 的响应层级是 ``resultbody.job.items``，但线上版本改过名，
    故对常见变体都做兼容；取不到就返回 ``None`` 让调用方区分
    「结构变了」与「这页真的没有岗位」。
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None

    result_body = payload.get("resultbody") or payload.get("resultBody") or {}
    if not isinstance(result_body, dict):
        return None

    for candidate in (
        (result_body.get("job") or {}).get("items") if isinstance(result_body.get("job"), dict) else None,
        result_body.get("items"),
        (result_body.get("jobList") or {}).get("items") if isinstance(result_body.get("jobList"), dict) else None,
    ):
        if isinstance(candidate, list):
            return candidate
    return None


def _pick(item: Dict, field: str) -> str:
    for key in _ITEM_ALIASES[field]:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


class Job51Crawler(BaseCrawler):
    """前程无忧爬虫。"""

    def __init__(self):
        super().__init__(SOURCE_SITE)

    def build_search_url(self, keyword: str, area_code: str, page_num: int) -> str:
        """拼搜索接口 URL。

        ``timestamp`` 是接口要求的毫秒时间戳；``accountId``/``requestId`` 留空
        即可（站点前端也是这样发的）。
        """
        return (
            f"{SEARCH_API}?api_key=51job"
            f"&jobArea={area_code}"
            f"&keyword={keyword}"
            f"&pageNum={page_num}"
            f"&pageSize={PAGE_SIZE}"
            f"&sortType=0"
            f"&searchType=2"
            f"&source=1"
        )

    async def crawl(self, keyword: str, city: str = "") -> List[Dict]:
        # 未收录城市直接抛错：这类失败重试不会变好，且静默回退会污染城市维度数据
        area_code = require_area_code(city)

        results: List[Dict] = []

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error(
                "Playwright 未安装，无法爬取 51job。请执行 "
                "pip install playwright && playwright install chromium"
            )
            raise

        async with async_playwright() as playwright:
            browser, context = await launch(playwright, self)
            try:
                page = await context.new_page()
                page_num = 1

                while len(results) < settings.crawl_max_jobs:
                    url = self.build_search_url(keyword, area_code, page_num)

                    # 域名级节流：同一域名串行 + 保底间隔，跨任务生效
                    # （BaseCrawler 的组内延时管不到「另一个任务此刻也在打同一个域名」）
                    await await_domain_slot(url, settings.crawl_domain_min_interval)

                    status = 200
                    headers: Dict[str, str] = {}
                    try:
                        response = await page.goto(url, timeout=settings.crawl_timeout * 1000)
                        if response is not None:
                            status = response.status
                            headers = dict(response.headers or {})
                    except Exception as exc:
                        logger.error("51job 第 %s 页请求失败：%s", page_num, exc)
                        break

                    # 对方明确说了要等多久，就听它的。
                    # 这里选择「停止本次采集」而不是原地 sleep：Retry-After 可能是几分钟
                    # 甚至一小时，让后台任务一直挂着不如直接收工，交给下一轮定时任务；
                    # 反正等待期间我们不会再发任何请求，语义上是尊重的。
                    retry_after = parse_retry_after(headers.get("retry-after"))
                    if retry_after is not None and status in (429, 503):
                        self.mark_blocked()
                        logger.warning(
                            "51job 返回 %s，对方要求等待 %.0f 秒；停止本次采集，"
                            "等待期间不再发起请求（累计风控 %s 次）",
                            status, retry_after, BaseCrawler._blocked_signals,
                        )
                        break

                    body = await page.evaluate(
                        "() => (document.body ? document.body.innerText : '')"
                    )
                    # 风控检测优先于解析：验证码/安全验证页绝不能被当作「无岗位」静默跳过
                    if self.detect_blocked(body, status):
                        self.mark_blocked()
                        logger.warning("51job 第 %s 页触发风控信号，停止翻页", page_num)
                        break

                    payload = self._load_payload(body)
                    items = extract_items(payload) if payload is not None else None
                    if items is None:
                        logger.warning(
                            "51job 第 %s 页响应结构不符预期（status=%s），停止翻页。"
                            "响应片段：%s",
                            page_num, status, (body or "")[:200],
                        )
                        break

                    page_results = [self._map_item(item, city) for item in items]
                    page_results = [job for job in page_results if job]
                    if not page_results:
                        break

                    results.extend(page_results)
                    await self.random_delay()
                    page_num += 1
            finally:
                await browser.close()

        return deduplicate_jobs(results)

    async def parse_page(self, page_content, keyword: str) -> List[Dict]:
        """解析一页内容（JSON 字符串或已解析的 dict）。

        保留这个方法是为了与 ``BaseCrawler`` 的抽象契约一致，
        也方便离线单测直接喂响应样本。
        """
        payload = self._load_payload(page_content)
        items = extract_items(payload) if payload is not None else None
        if items is None:
            return []
        jobs = [self._map_item(item, "") for item in items]
        return [job for job in jobs if job]

    @staticmethod
    def _load_payload(body):
        """把页面内容解析成 JSON；不是 JSON 就返回 ``None``。"""
        if isinstance(body, (dict, list)):
            return body
        if not isinstance(body, str):
            return None
        text = body.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            # 浏览器渲染 JSON 时可能包一层 <pre>，兜一下
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except ValueError:
                    return None
            return None

    def _map_item(self, item: Dict, city: str) -> Optional[Dict]:
        """接口条目 → 与 ``Job`` 模型口径一致的岗位字典。"""
        if not isinstance(item, dict):
            return None

        title = _pick(item, "title")
        if not title:
            return None

        area = _pick(item, "area")
        # 51job 的 area 形如「上海-浦东新区」，取第一段作为城市；调用方指定了
        # 城市时以调用方为准（爬取任务的城市维度不能与用户选择不一致）
        resolved_city = city or (area.split("-")[0].strip() if area else "")

        company_name = _pick(item, "company_name")
        salary_text = _pick(item, "salary")
        welfare = _pick(item, "welfare")
        description = _pick(item, "description")
        skills = self._extract_skills_from(title, welfare, description)

        job_data = {
            "title": title,
            "company_name": company_name,
            "city": resolved_city,
            "experience": _pick(item, "experience"),
            "education": _pick(item, "education"),
            "salary": salary_text,
            "skills": skills,
            "source_site": self.source_site,
            "url": self._normalize_url(_pick(item, "url")),
            "description": " ".join(x for x in (title, welfare, description) if x),
        }
        job_data["job_key"] = generate_job_key(
            self.source_site, title, company_name, resolved_city
        )

        cleaned = clean_job_data(job_data)
        # cleaner 的 parse_salary 只认「8K-12K」，51job 的「1-1.5万」它解析不了，
        # 所以这里用 51job 专用解析覆盖（能解析出非零值时才覆盖，避免把
        # cleaner 已经算对的结果改坏）。
        min_salary, max_salary = parse_salary_to_monthly(salary_text)
        if min_salary or max_salary:
            cleaned["min_salary"] = min_salary
            cleaned["max_salary"] = max_salary
        return cleaned

    @staticmethod
    def _normalize_url(url: str) -> str:
        """把详情链接补成绝对地址（``job_checker`` 靠它核查岗位存活）。"""
        if not url:
            return ""
        url = url.strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return "https://jobs.51job.com" + url
        return url if url.startswith("http") else ""

    @staticmethod
    def _extract_skills_from(*texts) -> str:
        """从标题/福利/描述里抽技能关键词（复用 cleaner 的词表口径）。"""
        from app.crawlers.cleaner import extract_skills

        title = texts[0] if texts else ""
        rest = " ".join(x for x in texts[1:] if x)
        return extract_skills(title, rest)
