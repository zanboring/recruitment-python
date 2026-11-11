# -*- coding: utf-8 -*-
"""岗位详情回填：从一个岗位的详情地址取回 ``job_desc`` / ``publish_time`` / ``detail_html``。

**为什么需要它**

列表页只给到标题、薪资、城市这类「卡片字段」，岗位正文（岗位职责 / 任职要求）
和发布时间只在详情里。库里的 ``job_desc`` 平均只有 10 个字符（等于标题 + 福利词），
``detail_html`` 与 ``publish_time`` 全空 —— 详情页从来没有被真正抓过。

**三条取数路径，按可靠性排序**

1. ``safe_get`` 直接取 HTML —— 适用于静态详情页（以及将来接入的其他站点）。
2. Playwright 渲染详情页 —— 51job 的详情页是 SPA，``document`` 里没有正文；
   渲染后从 DOM 取。
3. 搜索接口兜底（``/api/job/search-pc``，浏览器内触发）—— 实测 51job 的详情页
   ``we.51job.com/pc/jobdetail?jobId=...`` 会 302 到登录页，``jobs.51job.com/...``
   会撞滑块验证，**未登录状态下拿不到详情 DOM**；但搜索结果接口的每条 item
   都带 ``jobDescribe``（正文）与 ``issueDateString``（发布时间）。按 ``jobId``
   精确命中后再取字段，取的仍是**这一个岗位自己的**数据，不是拿别的岗位凑数。

**安全与节流**

- 所有出站请求走 ``app.utils.url_safety.safe_get``（逐跳校验 + 强制
  ``follow_redirects=False``）；Playwright 的每次 document 导航都先过
  ``assert_safe_url_async``，主 URL 与 302 之后的每一跳都判，不交给浏览器去试。
- 每次网络动作前 ``await_domain_slot`` 取域名槽位，与被复用的爬虫同一套闸门。
- 任何单条失败都只产出 ``DetailResult.ok is False`` + ``reason``，不抛异常 ——
  回填脚本要能跑完 20 条，不能因为第 3 条失败就整批中止。
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from app.config import settings
from app.crawlers.base import BaseCrawler
from app.crawlers.throttle import await_domain_slot
from app.utils.url_safety import UnsafeURLError, assert_safe_url_async, safe_get

logger = logging.getLogger("detail_fetcher")

SOURCE_SITE = "51job"
SEARCH_PAGE = "https://we.51job.com/pc/search"

# 正文短于这个长度就不算「抓到了」——列表页标题拼福利词大约 10 个字符，
# 把那种结果当正文写回去等于用假数据把问题盖住。
MIN_DESC_LEN = 30

# detail_html 落库上限。详情页整页 HTML 通常 60~150KB，个别站点会把
# 内联脚本堆到几 MB；截断是为了不让一次回填把库撑大，保留字符串前缀
# 对「看正文长什么样」这件事不影响。
MAX_HTML_LEN = 200_000

# 详情页正文容器的 class 候选。写成「class 里含某个词」而不是精确类名：
# 这类站点改版时改的是具体类名，很少改语义词。
_DESC_CLASS_HINTS = (
    "job-detail", "jobdetail", "job_detail", "job-msg", "job_msg",
    "bmsg", "jobdescribe", "job-desc", "job-desc", "description", "job-content",
)

# 「发布时间 / 更新于 2026-04-27」这类写法
_DATE_KEYS = ("发布时间", "发布日期", "更新于", "更新时间", "发布于", "发表于")
_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")

# 最小请求头。**刻意不做「更像浏览器」的伪装**：job51 的 SPA 在
# Accept / Sec-Fetch-* 这组头上反而会被打回空壳（详见 job51._crawl_rendered_dom
# 的隔离实测表），最小头才拿得到内容。
_MIN_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class DetailResult:
    """一条岗位详情的抓取结果。``ok`` 由正文是否够长决定，不由「有没有抛异常」决定。"""

    job_id: str = ""
    job_desc: str = ""
    publish_time: Optional[datetime] = None
    detail_html: str = ""
    source: str = ""
    reason: str = ""
    attempts: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.job_desc) >= MIN_DESC_LEN

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "job_desc": self.job_desc,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "detail_html_len": len(self.detail_html),
            "source": self.source,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- 解析


class _ContainerText(HTMLParser):
    """取「第一个 class 命中候选词的元素」里的文本，按嵌套深度配对结束标签。

    不用正则直接截 ``<div class="...">(.*?)</div>``：正文里几乎一定有嵌套 div，
    非贪婪会停在第一个内层 ``</div>``，抓出来的正文是半截的 —— 而半截正文
    看起来「有内容」，比空值更难被发现。
    """

    def __init__(self, hints):
        super().__init__(convert_charrefs=True)
        self.hints = tuple(hints)
        self.depth = 0
        self.hit = False
        self.parts: List[str] = []
        self.done = False

    @staticmethod
    def _match(attrs, hints) -> bool:
        for name, value in attrs:
            if name in ("class", "id", "itemprop") and value:
                low = value.lower()
                if any(h in low for h in hints):
                    return True
        return False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth == 0 and not self.hit and self._match(attrs, self.hints):
            self.hit = True
            self.depth = 1
            return
        if self.hit:
            self.depth += 1

    def handle_endtag(self, tag):
        if not self.hit or self.done:
            return
        self.depth -= 1
        if self.depth <= 0:
            self.done = True

    def handle_data(self, data):
        if self.hit and not self.done:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return _clean_text("".join(self.parts))


def _clean_text(raw: str) -> str:
    """把 HTML 片段压成可读正文：去掉标签残留、合并空行。"""
    if not raw:
        return ""
    text = unescape(raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [ln.strip(" \t\u3000") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _parse_date(text: str) -> Optional[datetime]:
    """把 ``2026-04-27`` / ``2026-04-27 16:35:53`` / ``2026/4/27`` 解析成 datetime。"""
    if not text:
        return None
    m = _DATE_RE.search(str(text))
    if not m:
        return None
    year, month, day = (int(x) for x in m.groups())
    if not (1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    clock = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(text)[m.end():m.end() + 12])
    hour = minute = second = 0
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        second = int(clock.group(3) or 0)
        if hour > 23 or minute > 59 or second > 59:
            hour = minute = second = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _json_ld_nodes(data) -> List[dict]:
    """展平 JSON-LD：顶层可能是对象、数组，或 ``@graph`` 容器。"""
    nodes: List[dict] = []
    if isinstance(data, list):
        for item in data:
            nodes.extend(_json_ld_nodes(item))
    elif isinstance(data, dict):
        if "@graph" in data:
            nodes.extend(_json_ld_nodes(data["@graph"]))
        nodes.append(data)
    return nodes


def parse_detail_html(html: str, url: str = "") -> Optional[Tuple[str, Optional[datetime]]]:
    """从详情页 HTML 里取 ``(正文, 发布时间)``；结构不认识就返回 ``None``。

    返回 ``None`` 的含义是**结构不匹配**，不是「这页没正文」——调用方据此决定
    要不要换一条取数路径，而不是把空字符串当结果写回库。
    """
    if not html or len(html) < 200:
        return None

    # 1) schema.org JobPosting：结构化数据比 CSS 类名稳定得多
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            payload = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        for node in _json_ld_nodes(payload):
            if not isinstance(node, dict):
                continue
            kind = str(node.get("@type") or "").lower()
            if kind not in ("jobposting", "jobpostings"):
                continue
            desc = _clean_text(str(node.get("description") or ""))
            if len(desc) >= MIN_DESC_LEN:
                return desc, _parse_date(str(node.get("datePosted") or ""))

    # 2) 常见正文容器
    parser = _ContainerText(_DESC_CLASS_HINTS)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - 解析失败等同于结构不匹配
        logger.debug("详情页 HTML 解析失败：%s", exc)
        return None
    if len(parser.text) >= MIN_DESC_LEN:
        return parser.text, _extract_publish_time(html)

    return None


def _extract_publish_time(html: str) -> Optional[datetime]:
    """从整页文本里找「发布时间 / 更新于 2026-04-27」。"""
    text = _clean_text(html)
    for key in _DATE_KEYS:
        idx = text.find(key)
        while idx != -1:
            found = _parse_date(text[idx: idx + 40])
            if found:
                return found
            idx = text.find(key, idx + 1)
    return None


def parse_search_item(item: dict) -> Optional[Tuple[str, Optional[datetime]]]:
    """从搜索接口的一条 item 里取 ``(正文, 发布时间)``。

    ``jobDescribe`` 偶尔带 ``<br>``，先清成纯文本；不足 ``MIN_DESC_LEN`` 视为没有。
    """
    if not isinstance(item, dict):
        return None
    desc = _clean_text(str(item.get("jobDescribe") or ""))
    if len(desc) < MIN_DESC_LEN:
        return None
    publish = _parse_date(str(item.get("issueDateString") or item.get("confirmDateString") or ""))
    return desc, publish


def extract_job_id(url: str) -> str:
    """从详情地址里取平台岗位号。

    三种实测形态都要认，库里的 URL 不止一种来源：

    - ``https://we.51job.com/pc/jobdetail?jobId=171818429``（DOM 卡片路径生成）
    - ``https://jobs.51job.com/changsha-kfq/174174002.html``（搜索接口的
      ``jobHref`` 原样存进来的形态，**不带 jobId 字样**）
    - ``https://m.51job.com/jobdetail/174174002.html``
    """
    if not url:
        return ""
    for pattern in (
        r"[?&]jobId=(\d+)",
        r"/jobdetail/(\d+)",
        r"/(\d{6,})\.html",
    ):
        m = re.search(pattern, url, re.I)
        if m:
            return m.group(1)
    return ""


# --------------------------------------------------------------------------- 取数


class DetailFetcher:
    """详情抓取器。浏览器**在多个岗位之间复用**：每次新建上下文要 3~5 秒，
    20 条就是一分多钟的纯启动开销。用 ``aclose()`` 显式释放。"""

    def __init__(self, *, timeout: Optional[float] = None, min_interval: Optional[float] = None):
        self.timeout = float(timeout or settings.crawl_timeout)
        self.min_interval = (
            float(min_interval) if min_interval is not None else float(settings.crawl_domain_min_interval)
        )
        self._pw_ctx = None
        self._browser = None

    # ---------------------------------------------------------------- 浏览器

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        from playwright.async_api import async_playwright

        self._pw_ctx = async_playwright()
        pw = await self._pw_ctx.__aenter__()
        self._browser = await pw.chromium.launch(headless=True)
        return self._browser

    async def _new_page(self):
        browser = await self._ensure_browser()
        # 干净的上下文 + 最小头：见模块文档，51job 的 SPA 在「更像浏览器」的头下
        # 会被打回空壳（keyword/jobArea 都不生效）。
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        page = await context.new_page()

        # document 级导航逐跳校验：主 URL 合法、302 到内网地址是最经典的绕过手法
        async def _guard(route):
            if route.request.resource_type == "document":
                try:
                    await assert_safe_url_async(route.request.url)
                except UnsafeURLError:
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", _guard)
        return context, page

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._pw_ctx is not None:
            try:
                await self._pw_ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._pw_ctx = None

    # ---------------------------------------------------------------- 对外

    async def fetch(
        self, url: str, *, keyword: str = "", city: str = "", allow_search: bool = True
    ) -> DetailResult:
        """抓一条详情。**不抛异常**：失败信息放在 ``reason`` / ``attempts`` 里。"""
        result = DetailResult(job_id=extract_job_id(url))
        if not url:
            result.reason = "缺少岗位 URL"
            return result

        # 1) 静态 HTML
        try:
            await await_domain_slot(url, self.min_interval)
            resp = await safe_get(url, headers=_MIN_HEADERS, timeout=self.timeout)
            html = resp.text or ""
            final_url = str(getattr(resp, "url", "") or url)
            if BaseCrawler.detect_blocked(html, resp.status_code, final_url):
                result.attempts.append("html: 命中风控信号")
            else:
                parsed = parse_detail_html(html, final_url)
                if parsed:
                    desc, publish = parsed
                    result.job_desc = desc
                    result.publish_time = publish
                    result.detail_html = html[:MAX_HTML_LEN]
                    result.source = "html"
                    return result
                result.attempts.append(f"html: 结构不匹配（{len(html)} 字节）")
        except UnsafeURLError as exc:
            result.attempts.append(f"html: 目标地址不安全（{exc}）")
        except Exception as exc:  # noqa: BLE001 - 网络问题不该中断整批
            result.attempts.append(f"html: {type(exc).__name__}: {exc}")

        # 2) 渲染详情页
        try:
            rendered = await self._render_detail(url)
            if rendered:
                desc, publish = rendered
                result.job_desc = desc
                result.publish_time = publish
                result.detail_html = ""  # 见 _render_detail：只在能拿到正文时返回正文
                result.source = "render"
                return result
            result.attempts.append("render: 未拿到正文")
        except Exception as exc:  # noqa: BLE001
            result.attempts.append(f"render: {type(exc).__name__}: {exc}")

        # 3) 搜索接口兜底（需要标题做关键词）
        if allow_search and keyword:
            try:
                found = await self._search_by_keyword(keyword, city, result.job_id)
                if found:
                    desc, publish, html = found
                    result.job_desc = desc
                    result.publish_time = publish
                    result.detail_html = html
                    result.source = "search_api"
                    return result
                result.attempts.append("search: 结果里没有该 jobId")
            except Exception as exc:  # noqa: BLE001
                result.attempts.append(f"search: {type(exc).__name__}: {exc}")
        elif not keyword:
            result.attempts.append("search: 未提供标题，跳过")

        result.reason = "；".join(result.attempts[-3:]) or "未知原因"
        return result

    # ---------------------------------------------------------------- 内部

    async def _render_detail(self, url: str) -> Optional[Tuple[str, Optional[datetime]]]:
        """渲染详情页并从 DOM 取正文；登录墙 / 滑块验证都返回 ``None``。"""
        context, page = await self._new_page()
        try:
            await await_domain_slot(url, self.min_interval)
            await page.goto(url, timeout=int(self.timeout * 1000), wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            final = str(getattr(page, "url", "") or "")
            title = ""
            try:
                title = await page.title()
            except Exception:  # noqa: BLE001
                pass
            # 登录墙与滑块验证：URL / 标题里带得到证据（detect_blocked 的同一判据）
            if any(k in final.lower() for k in ("login.", "/login", "passport")) or \
                    any(k in title for k in ("登录", "验证")):
                logger.info("详情页需要登录或验证，跳过渲染取数：%s", url)
                return None
            html = await page.content()
            if BaseCrawler.detect_blocked(html, 200, final, title):
                BaseCrawler.mark_blocked()
                return None
            return parse_detail_html(html, final)
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

    async def _search_by_keyword(self, keyword: str, city: str, job_id: str):
        """在搜索结果接口的响应里按 ``jobId`` 精确命中，取正文与发布时间。

        为什么不是「按标题模糊取第一条」：标题相近的岗位很多，取错一条等于
        把别人的岗位职责写进这条记录 —— 数据看着完整、内容是错的，比空着更糟。
        """
        if not job_id:
            return None
        from urllib.parse import quote

        from app.crawlers.job51 import extract_items, get_area_code

        area = get_area_code(city or "") or ""
        url = f"{SEARCH_PAGE}?jobArea={area}&keyword={quote(keyword)}&searchType=2&sortType=0"

        context, page = await self._new_page()
        payloads: List[str] = []

        async def _collect(resp):
            if "/api/job/search-pc" not in resp.url:
                return
            try:
                text = await resp.text()
            except Exception:  # noqa: BLE001
                return
            if text.lstrip().startswith("{") and len(text) > 5000:
                payloads.append(text)

        page.on("response", lambda r: asyncio.ensure_future(_collect(r)))
        try:
            await await_domain_slot(url, self.min_interval)
            await page.goto(url, timeout=int(self.timeout * 1000), wait_until="domcontentloaded")
            try:
                await page.wait_for_selector(".joblist-item", timeout=20000)
            except Exception:  # noqa: BLE001 - 没有卡片也可能是接口已返回，继续看 payload
                pass
            await page.wait_for_timeout(3000)
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

        for text in reversed(payloads):
            try:
                items = extract_items(text)
            except (ValueError, TypeError):
                continue
            if not items:
                continue
            for item in items:
                if str(item.get("jobId") or "") != str(job_id):
                    continue
                parsed = parse_search_item(item)
                if not parsed:
                    return None
                desc, publish = parsed
                return desc, publish, ""
        return None


async def fetch_detail(
    url: str, *, keyword: str = "", city: str = ""
) -> DetailResult:
    """一次性抓取（自建并关闭浏览器）。批量场景请用 :class:`DetailFetcher` 复用浏览器。"""
    fetcher = DetailFetcher()
    try:
        return await fetcher.fetch(url, keyword=keyword, city=city)
    finally:
        await fetcher.aclose()
