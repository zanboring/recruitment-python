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
from app.crawlers.cleaner import generate_job_key
from app.crawlers.spec_schema import (
    JOB_SALARY_FIELDS,
    JOB_STANDARD_FIELDS,
    CrawlSpec,
    FieldSpec,
)

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


def _as_int(value: Any) -> Optional[int]:
    """把抽取到的薪资数值转成整数元；转不了返回 None。

    不做单位推断（"15" 到底是 15 元还是 15K 无法从文本判断）：
    YAML 里 ``min_salary`` / ``max_salary`` 约定直接写月薪元数，
    拿不准就用 ``salary`` 文本交给既有薪资解析器。
    """
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        logger.warning("薪资字段不是数值，已忽略：%r（请改用 salary 文本或写月薪元数）", value)
        return None


class SpecCrawler(BaseCrawler):
    """按 YAML 规格执行抓取的通用爬虫。"""

    def __init__(self, spec: CrawlSpec):
        super().__init__(spec.site)
        self.spec = spec
        self._browser = None
        # 详情页抓取的说明条目（失败条数 / 风控即停 / 预算截断）。
        # 由 ``crawler_service._crawl_platform`` 并入任务 message，不另造返回结构。
        self.detail_notes: List[str] = []
        self._detail_requests = 0
        self._detail_ok = 0
        self._detail_failed = 0
        self._detail_stopped = False
        self._detail_block_reason = ""

    # ------------------------------------------------------------------ 入口

    async def crawl(self, keyword: str = "", city: str = "") -> List[Dict]:
        """兼容 ``BaseCrawler`` 的旧签名，内部翻译为参数表。"""
        params = self.spec.build_params(keyword=keyword, city=city)
        return await self.crawl_params(params)

    async def crawl_params(self, params: Dict[str, Any]) -> List[Dict]:
        """按任意参数抓取（将来通用调度走这里，不限于关键词+城市）。"""
        spec = self.spec
        items: List[Dict] = []
        self._reset_detail_state()

        page = int(params.get("page", spec.pagination_start) or spec.pagination_start)
        pages_done = 0
        # next_link 模式必须跨迭代维护「当前页地址」：
        # 原来每轮都回到循环顶部按 page 重渲染 URL，于是 max_pages 轮里反复抓
        # 「第 1 页 → 第 2 页」，第 3 页永远到不了，条目成倍重复。
        current_url: Optional[str] = None
        visited: set = set()

        try:
            while pages_done < spec.max_pages:
                if spec.pagination_type == "next_link" and current_url is not None:
                    url = current_url
                else:
                    params["page"] = page
                    url = spec.render_url(params)

                # 真实站点「下一页」翻到末页时常仍指向自己；不设这道门会死循环。
                if url in visited:
                    logger.warning(
                        "[%s] 翻页目标重复（已抓过），停止：%s", spec.site, url
                    )
                    break
                visited.add(url)

                logger.info("[%s] 抓取第 %s 页：%s", spec.site, pages_done + 1, url)

                html = await self._fetch(url)

                # 风控先判，避免把验证码页当空结果
                if self.detect_blocked(html):
                    self.mark_blocked()
                    raise CrawlBlockedError(
                        f"[{spec.site}] 命中风控信号（验证码/限流/人机校验），"
                        f"请在浏览器中手动完成后重试"
                    )

                batch = self.parse_page(html, keyword=str(params.get("keyword", "")))
                # 先补详情、再映射：详情页字段可能覆盖列表同名字段，
                # 而 field_map / title 也可能只存在于详情页。
                await self._enrich_batch(batch, url)
                items.extend(self._shape_records(batch, params, url))
                pages_done += 1

                # 详情页命中风控：已抓到的列表数据照常返回，
                # 但不再发出任何新请求（继续打只会让封禁升级）。
                if self._detail_stopped:
                    break

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
                    if not nxt or nxt == url:
                        break
                    current_url = nxt
                    await asyncio.sleep(self._interval())
                    continue

                break
        finally:
            await self._close_browser()

        self._finalize_detail_notes()
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

    # ------------------------------------------------------------ 详情页抓取
    #
    # 详情页是「列表里只给链接、正文在另一页」的站点的必需品。这里只有三条
    # 约束，缺任何一条都会把一个小功能变成对目标站点的压测（详见
    # docs/data-source-plugin.md「详情页」一节）：
    #   1. 两层闸门：``detail.max_items`` 与 ``limits.max_items`` 取较小者；
    #   2. 每条详情请求前等 ``_interval()``，不因"就几条"跳过；
    #   3. 命中风控立即停止，不是跳过这一条接着打。

    def _detail_active(self) -> bool:
        """详情页是否真的会被抓取（三要素齐全且预算 > 0）。"""
        spec = self.spec
        return bool(
            spec.detail_enabled
            and spec.detail_link_field
            and spec.detail_fields
            and spec.detail_max_items > 0
        )

    def _detail_budget(self) -> int:
        cap = min(self.spec.detail_max_items, self.spec.max_items)
        return max(0, cap)

    def _reset_detail_state(self) -> None:
        self.detail_notes = []
        self._detail_requests = 0
        self._detail_ok = 0
        self._detail_failed = 0
        self._detail_stopped = False
        self._detail_block_reason = ""

    def parse_detail_page(self, page_content) -> Dict[str, Any]:
        """按 ``detail.fields`` 解析详情页，返回 {字段名: 值}（同步，不涉及 IO）。

        与列表页共用同一个 ``_extract`` —— 两种页面只差"容器节点是哪一个"，
        抽取语义（text/attr/html/multi/regex/default）没有理由分叉。
        """
        if not page_content:
            return {}
        soup = BeautifulSoup(page_content, "lxml")
        return {f.name: self._extract(soup, f) for f in self.spec.detail_fields}

    def _detail_link(self, row: Dict[str, Any]) -> str:
        value = row.get(self.spec.detail_link_field)
        if isinstance(value, list):
            value = value[0] if value else None
        return str(value or "").strip()

    async def _enrich_batch(self, batch: List[Dict], page_url: str) -> None:
        """用详情页补齐这一页的记录（原地修改，不新增记录）。

        单条失败（超时/404/解析不到）只影响这一条：保留列表页字段、计数，
        然后继续下一条 —— 否则一条 404 会让整页数据全部作废。
        """
        spec = self.spec
        if not self._detail_active() or not batch:
            return

        budget = self._detail_budget() - self._detail_requests
        if budget <= 0:
            return

        for row in batch:
            if budget <= 0 or self._detail_stopped:
                break

            href = self._detail_link(row)
            if not href:
                continue
            url = urljoin(page_url, href)

            # 每条详情请求之间必须等：复用既有节流（spec 下限与自适应降速取较大者），
            # 不另造 sleep，也不因为"就十条"跳过。
            await asyncio.sleep(self._interval())
            self._detail_requests += 1
            budget -= 1

            try:
                html = await self._fetch(url)
            except CrawlBlockedError as exc:
                # 429 这类「明确的限流」由 _fetch_plain 抛上来并已 mark_blocked
                self._detail_block_reason = str(exc)
                self._detail_stopped = True
                break
            except Exception as exc:  # noqa: BLE001
                # 超时 / 404 / 非 HTML 响应：只丢这一条的详情，不影响整页
                self._detail_failed += 1
                logger.warning(
                    "[%s] 详情页抓取失败（保留列表页字段）：%s（%s）",
                    spec.site, url, exc,
                )
                continue

            if self.detect_blocked(html):
                self.mark_blocked()
                self._detail_block_reason = "详情页命中风控信号（验证码/限流/人机校验）"
                self._detail_stopped = True
                break

            detail = self.parse_detail_page(html)
            merged = 0
            for name, value in detail.items():
                # 详情页字段覆盖列表同名字段（详情页更细）；
                # 取不到的一律保留列表原值 —— 不要用空值把已有数据洗掉。
                if value in (None, "", []):
                    continue
                row[name] = value
                merged += 1

            if merged:
                self._detail_ok += 1
            else:
                self._detail_failed += 1
                logger.warning(
                    "[%s] 详情页解析不到任何字段（保留列表页字段）：%s", spec.site, url
                )

    def _finalize_detail_notes(self) -> None:
        """把这一次抓取的详情页情况写进说明条目（并入任务 message）。"""
        if not self._detail_active():
            return
        if self._detail_requests:
            self.detail_notes.append(
                f"平台 {self.spec.site} 详情页：成功 {self._detail_ok} 条，"
                f"失败 {self._detail_failed} 条（失败条目保留列表页字段）"
            )
        if self._detail_stopped:
            self.detail_notes.append(
                f"平台 {self.spec.site} 详情页抓取已停止：{self._detail_block_reason}"
                f"（已抓到的列表数据照常保留，未再发出新请求）"
            )
        elif self._detail_requests >= self._detail_budget() > 0:
            self.detail_notes.append(
                f"平台 {self.spec.site} 详情页请求已达上限 "
                f"{self._detail_budget()} 条，其余条目仅保留列表页字段"
            )

    # ---------------------------------------------------------- 输出映射
    #
    # 为什么映射放在执行器里而不是服务层：``mapping`` 写在 YAML 里，是
    # **这个数据源自己的输出契约**；服务层只认标准化岗位字典，不该知道
    # 某个站点把标题叫 ``job_name`` 还是 ``position``。

    def _shape_records(self, batch: List[Dict], params: Dict[str, Any], page_url: str) -> List[Dict]:
        """按 spec.target 决定这一页记录的输出形态。"""
        if self.spec.target == "job":
            mapped = [self._map_to_job(row, params, page_url) for row in batch]
            return [m for m in mapped if m]

        # generic：保持原样（分析用、不进岗位表），但显式打标 ——
        # 下游 ``deduplicate_jobs`` 只保留有 job_key 的行，没有标记就会被
        # 静默吞掉，表现成「YAML 配好了、抓到了、库里一条没有」。
        for row in batch:
            row["_target"] = "generic"
        return batch

    def _map_to_job(self, row: Dict[str, Any], params: Dict[str, Any], page_url: str) -> Optional[Dict[str, Any]]:
        """把一条抽取结果按 ``mapping.field_map`` 映射成标准岗位字典。

        缺少 ``title`` 时返回 None（无法生成 job_key，入库没有意义）；
        其余字段缺失一律留空串，由服务层 ``normalize_job`` 决定怎么补。
        """
        spec = self.spec

        def pick(name: str) -> str:
            source = spec.field_map.get(name) or name
            value = row.get(source)
            if isinstance(value, list):
                value = "、".join(str(v) for v in value)
            return "" if value is None else str(value).strip()

        mapped: Dict[str, Any] = {
            name: pick(name)
            for name in JOB_STANDARD_FIELDS
            if name not in JOB_SALARY_FIELDS
        }

        salary = pick("salary")
        if salary:
            mapped["salary"] = salary
        # 数值薪资：按「元 / 月」直接取，不猜单位；只给下限时上限同上限。
        lo = _as_int(pick("min_salary"))
        hi = _as_int(pick("max_salary"))
        if lo is not None and hi is None:
            hi = lo
        if lo is not None:
            mapped["min_salary"] = lo
        if hi is not None:
            mapped["max_salary"] = hi

        if not mapped["city"]:
            mapped["city"] = str(params.get("city") or "").strip()
        if not mapped["url"]:
            mapped["url"] = page_url
        elif not mapped["url"].startswith(("http://", "https://")):
            mapped["url"] = urljoin(page_url, mapped["url"])

        mapped["source_site"] = spec.site
        mapped["_target"] = "job"

        if not mapped["title"]:
            logger.warning(
                "[%s] 一条记录缺少 title（field_map 期望字段 %r），无法入库已跳过",
                spec.site, spec.field_map.get("title", "title"),
            )
            return None

        # job_key 走统一实现（平台 + 标题 + 公司 + 城市），
        # 不能在这里另算一套 —— 否则同一岗位经不同入口入库会出现两行。
        mapped["job_key"] = generate_job_key(
            spec.site, mapped["title"], mapped["company_name"], mapped["city"]
        )
        return mapped

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
