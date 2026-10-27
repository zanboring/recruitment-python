"""声明式爬虫的通用执行器。

一个 ``SpecCrawler`` 实例 = 一份 YAML 规格 + ``BaseCrawler`` 的全部反爬能力
（随机 UA、请求头协变、自适应降速、风控检测、指数退避重试）。

设计要点
--------
1. **兼容旧签名**：``BaseCrawler.crawl_with_retry`` 调的是 ``crawl(keyword, city)``，
   所以这里保留该签名，内部再翻译成 spec 的参数表。这样现有调度链路
   （``CrawlerService.start_crawl``）不需要任何改动就能跑声明式数据源。

2. **两种抓取模式**：
   · ``render: false`` → httpx 直取 HTML（快、省资源，适合静态页）
   · ``render: true``  → Playwright 渲染后取 content（适合 JS 渲染页）
   Playwright 是延迟导入的：测试环境没装也不影响 httpx 路径。

3. **风控不静默**：命中 ``detect_blocked`` 时抛 ``CrawlBlockedError``，
   让上层把任务标成「被拦」而不是「成功但 0 条」——后者会把验证码页
   当成"这个关键词没有岗位"，是最难查的一类假数据。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.crawlers.base import BaseCrawler
from app.crawlers.spec_schema import CrawlSpec, FieldSpec

logger = logging.getLogger(__name__)


class CrawlBlockedError(RuntimeError):
    """被目标站风控拦截（验证码 / 429 / 滑块等）。

    单独成类是为了让上层能区分「被拦」与「代码错」：
    前者可以提示用户人工介入后重试，后者需要修代码。
    """


def _supported_encodings() -> str:
    """返回当前环境**真正能解码**的 Accept-Encoding。

    不要照抄浏览器的 "gzip, deflate, br"：环境里没有 brotli 解码器时，
    服务器返回 br 压缩体，客户端只能得到乱码，而错误会延迟到解析阶段
    表现为「选择器无匹配、0 条」，极难定位（2026-10-26 实测踩过）。
    """
    encodings = ["gzip", "deflate"]
    try:
        import brotli  # noqa: F401
        encodings.append("br")
    except ImportError:
        pass
    return ", ".join(encodings)


class SpecCrawler(BaseCrawler):
    """按 YAML 规格执行抓取的通用爬虫。"""

    def __init__(self, spec: CrawlSpec):
        super().__init__(spec.site)
        self.spec = spec
        self._browser = None

    # ------------------------------------------------------------------ 入口

    async def crawl(self, keyword: str = "", city: str = "") -> List[Dict]:
        """兼容 ``BaseCrawler`` 的旧签名，内部翻译为参数表。"""
        params = self.spec.build_params(keyword=keyword, city=city)
        return await self.crawl_params(params)

    async def crawl_params(self, params: Dict[str, Any]) -> List[Dict]:
        """按任意参数抓取（将来通用调度走这里，不限于关键词+城市）。"""
        spec = self.spec
        items: List[Dict] = []

        page = int(params.get("page", spec.pagination_start) or spec.pagination_start)
        pages_done = 0

        try:
            while pages_done < spec.max_pages:
                params["page"] = page
                url = spec.render_url(params)
                logger.info("[%s] 抓取第 %s 页：%s", spec.site, page, url)

                html = await self._fetch(url)

                # 风控先判，避免把验证码页当空结果
                if self.detect_blocked(html):
                    self.mark_blocked()
                    raise CrawlBlockedError(
                        f"[{spec.site}] 命中风控信号（验证码/限流/人机校验），"
                        f"请在浏览器中手动完成后重试"
                    )

                batch = self.parse_page(html, keyword=str(params.get("keyword", "")))
                items.extend(batch)
                pages_done += 1

                if len(items) >= spec.max_items:
                    items = items[: spec.max_items]
                    break

                if spec.pagination_type == "none":
                    break

                if spec.pagination_type == "page_param":
                    page += 1
                    # 第 1 页之后按 min_interval 节流；首页前的间隔由调度层统一管
                    # 翻页前必须等：spec 下限与自适应降速取较大者（见 _interval）。
                    # 不按 min_interval 是否 >0 决定跳过节流 —— 默认 0 时也要走
                    # 基类的基础延迟，否则默认配置就是无间隔连打对方站点。
                    await asyncio.sleep(self._interval())
                    continue

                if spec.pagination_type == "next_link":
                    nxt = self._find_next_link(html, url)
                    if not nxt:
                        break
                    params["page"] = nxt          # next_link 模式直接带完整 URL
                    spec_url_backup = spec.url
                    try:
                        spec.url = nxt
                        html = await self._fetch(nxt)
                    finally:
                        spec.url = spec_url_backup
                    if self.detect_blocked(html):
                        self.mark_blocked()
                        raise CrawlBlockedError(f"[{spec.site}] 翻页时命中风控信号")
                    items.extend(self.parse_page(html, keyword=str(params.get("keyword", ""))))
                    pages_done += 1
                    # 翻页前必须等：spec 下限与自适应降速取较大者（见 _interval）。
                    # 不按 min_interval 是否 >0 决定跳过节流 —— 默认 0 时也要走
                    # 基类的基础延迟，否则默认配置就是无间隔连打对方站点。
                    await asyncio.sleep(self._interval())
                    continue

                break
        finally:
            await self._close_browser()

        logger.info("[%s] 抓取完成，共 %s 条", spec.site, len(items))
        return items

    def parse_page(self, page_content, keyword: str = "") -> List[Dict]:
        """解析一页 HTML，返回记录列表（同步，不涉及 IO）。"""
        if not page_content:
            return []
        soup = BeautifulSoup(page_content, "lxml")
        nodes = soup.select(self.spec.list_container)
        if not nodes:
            logger.warning(
                "[%s] 列表选择器无匹配：%r（站点改版？或该页无结果）",
                self.spec.site, self.spec.list_container,
            )
            return []

        records: List[Dict] = []
        for node in nodes:
            row: Dict[str, Any] = {}
            for f in self.spec.fields:
                row[f.name] = self._extract(node, f)
            # 全字段都为空的行通常是占位/广告位，丢掉
            if any(v not in (None, "", []) for v in row.values()):
                records.append(row)
        return records

    # ------------------------------------------------------------- 字段抽取

    def _extract(self, node, f: FieldSpec) -> Any:
        p = f.parse_expr()
        if p["multi"]:
            found = node.select(p["selector"])
            values = [self._value(n, p) for n in found]
            return [v for v in values if v not in (None, "")]
        one = node.select_one(p["selector"])
        if one is None:
            return f.default
        return self._value(one, p)

    @staticmethod
    def _value(node, p: Dict[str, Any]) -> Any:
        mode = p["mode"]
        if mode == "attr":
            val = node.get(p["attr"]) if p["attr"] else None
        elif mode == "html":
            val = node.decode_contents()
        else:
            val = node.get_text(strip=True)

        if val is None:
            return None
        val = str(val).strip()

        if p["regex"]:
            m = re.search(p["regex"], val)
            val = m.group(1) if (m and m.groups()) else (m.group(0) if m else None)
        return val

    # ------------------------------------------------------------------ IO

    async def _fetch(self, url: str) -> str:
        """取一页 HTML。render=true 走 Playwright，否则 httpx。"""
        if self.spec.render:
            return await self._fetch_rendered(url)
        return await self._fetch_plain(url)

    async def _fetch_plain(self, url: str) -> str:
        import httpx

        headers = self.get_random_headers()
        # 🔴 关键修正（2026-10-26 实测踩坑）：
        # BaseCrawler.get_random_headers() 固定声明 "Accept-Encoding: gzip, deflate, br"。
        # 若运行环境**没装 brotli**，服务器会真的返回 br 压缩体，而 httpx 解不开，
        # 于是拿到一堆乱码 —— 症状是「列表选择器无匹配，0 条」，看起来像站点改版，
        # 极难定位。这里按**实际解码能力**声明，而不是照抄浏览器请求头。
        headers["Accept-Encoding"] = _supported_encodings()
        headers.update(self.spec.headers)

        async with httpx.AsyncClient(
            timeout=self.spec.timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                self.mark_blocked()
                raise CrawlBlockedError(f"[{self.spec.site}] HTTP 429 限流")
            resp.raise_for_status()

            # 拿到的如果不是 HTML（比如被代理替换成 JSON 错误页），直接报错，
            # 不要留到解析阶段变成「0 条」—— 那是最难查的一类假数据。
            ctype = (resp.headers.get("content-type") or "").lower()
            if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
                raise RuntimeError(
                    f"[{self.spec.site}] 响应不是 HTML（content-type={ctype}），"
                    f"可能被代理/CDN 拦截或需要登录"
                )
            return resp.text

    async def _fetch_rendered(self, url: str) -> str:
        """用 Playwright 渲染后取 HTML（延迟导入，测试环境无 playwright 也不影响 httpx 路径）。"""
        from playwright.async_api import async_playwright

        if self._browser is None:
            self._pw_ctx = async_playwright()
            pw = await self._pw_ctx.__aenter__()
            self._browser = await pw.chromium.launch(headless=True)
            headers = self.get_random_headers()
            self._context = await self._browser.new_context(
                user_agent=headers.pop("User-Agent"),
                extra_http_headers=headers,
                viewport={"width": 1920, "height": 1080},
            )

        page = await self._context.new_page()
        try:
            await page.goto(url, timeout=self.spec.timeout * 1000, wait_until="domcontentloaded")
            if self.spec.min_interval > 0:
                await page.wait_for_timeout(int(self._interval() * 1000))
            return await page.content()
        finally:
            await page.close()

    async def _close_browser(self):
        if getattr(self, "_browser", None) is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        ctx = getattr(self, "_pw_ctx", None)
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._pw_ctx = None

    # -------------------------------------------------------------- 辅助

    def _interval(self) -> float:
        """翻页前的等待时长。

        语义（三条都不能少，否则要么打太快、要么把配置项变成死代码）：

        1. spec 写了 ``min_interval`` → **按作者的节奏来**。原先实现是
           ``adaptive if min_interval > 0 else 0``，即 min_interval>0 时反而去用
           基类的 12~18 秒基础延迟，写 2.0 完全不生效 —— 配置项形同虚设。
        2. spec 没写 → 用基类基础随机延迟。原先返回 0，默认配置下多页抓取
           **毫无间隔地连打**，这是最容易被判定为爬虫、从临时限流升级为封 IP 的行为。
        3. 一旦出现风控信号，``_adaptive_delay()`` 会被放大 —— 此时**降速优先于
           作者写的下限**，不能因为"我配了 2 秒"就无视对方已经在拦你了。
        """
        if self.spec.min_interval > 0:
            if type(self)._blocked_signals > 0:
                return max(self.spec.min_interval, self._adaptive_delay())
            return self.spec.min_interval
        return self._adaptive_delay()

    def _find_next_link(self, html: str, base_url: str) -> Optional[str]:
        if not self.spec.next_selector:
            return None
        soup = BeautifulSoup(html, "lxml")
        a = soup.select_one(self.spec.next_selector)
        if a is None:
            return None
        href = a.get("href")
        return urljoin(base_url, href) if href else None
