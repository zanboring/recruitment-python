"""声明式爬虫规格（CrawlSpec）—— 加一个 YAML 就能接入新数据源。

为什么要有这一层
----------------
``BaseCrawler`` 的抽象签名是 ``crawl(keyword, city)`` —— 本身带招聘语义：
只能表达「关键词 + 城市」这一种查询。想爬一个电商列表或新闻站，
就得再写一个 Python 类，等于**每接一个源都要动代码**。

本模块把「站点长什么样」抽象成数据（YAML），交给通用执行器
``spec_crawler.SpecCrawler`` 去跑。于是新增数据源 = 在
``app/crawlers/specs/`` 放一个 yaml，不改任何 Python。

能覆盖什么、不能覆盖什么（诚实边界）
------------------------------------
能（纯配置即可）：
  · 静态或 JS 渲染的列表页；可选的**详情页补齐**（``detail.*``，受
    ``detail.max_items`` 与 ``limits.max_items`` 双重约束 —— 见
    docs/data-source-plugin.md §六）
  · **JSON 接口型数据源**（``type: api``，2026-11-10 新增）：直接从接口
    取 JSON，用 ``json:路径`` 表达式抽字段 —— 见本文档 ``FieldSpec``
  · 分页：页码参数 / 下一页链接（滚动加载尚未实现）
  · 字段抽取：CSS 选择器、取文本或属性、正则后处理、JSON 路径
  · 限速、单次上限、合规探针

不能（必须写 Python 类）：
  · 需要登录或人工过验证的站点（如 BOSS 直聘）—— 见 ``boss.py``
  · 接口加密、签名校验、token 链
  · 强交互（点击展开、iframe、多步表单）
  · 非列表结构（图关系、递归树）

**一条实测边界（2026-11-10）**：51job 的搜索接口对非浏览器客户端返回
阿里云 WAF 页（HTTP 200 + ``_waf_bd8ce2ce37`` + ``text/html``），
``type: api`` + httpx 拿不到 JSON。它需要浏览器上下文（即 ``job51.py`` 的做法），
**不是** ``type: api`` 能解决的 —— 别把「接口被 WAF 挡」误判成「表达式写错了」。

**这不是缺陷，是分工**：配置化解决「大量结构相似的简单站点」，
复杂站点仍走子类。两种方式在 ``registry`` 里共存。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

SPEC_DIR = Path(__file__).parent / "specs"

# 站点标识只允许字母数字下划线中划线，避免被当作路径或注入
_SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")

# 运行期加载过的声明式数据源标识（`source_site` 白名单的一部分）。
#
# 为什么需要可变集合：`registry.SUPPORTED_PLATFORMS` 是**导入时**由 `specs/`
# 目录算出的静态快照，而 `load_spec_file()` 允许在运行期单独加载一个 yaml
# （测试、自定义目录、将来热加载）。这类源不在快照里，若白名单只认快照，
# 就会出现「抓取成功、入库被拒」—— 症状看起来像数据丢了，其实是来源校验过严。
REGISTERED_SPEC_SITES: set = set()

# ``mapping.field_map`` 的取值域：Job 入库链路能接收的标准字段。
# 键 = 标准字段名（左），值 = YAML ``list.fields`` / ``detail.fields`` 里的字段名（右）。
# 方向选「标准字段 → 抽取字段」而不是反过来：目标 schema 是固定的，
# 这样加载期就能把写错的键名挡下来，而不是等入库时才静默丢字段。
JOB_STANDARD_FIELDS = (
    "title",
    "company_name",
    "city",
    "experience",
    "education",
    "salary",
    "min_salary",
    "max_salary",
    "description",
    "url",
    "skills",
)

# 薪资字段单独处理：``salary`` 是文本（交给既有解析器归一），
# ``min_salary`` / ``max_salary`` 是数值，二者取其一即可。
JOB_SALARY_FIELDS = ("salary", "min_salary", "max_salary")


class SpecError(ValueError):
    """规格文件不合法。"""


@dataclass
class FieldSpec:
    """单个字段的抽取规则。

    ``expr`` 语法（两族，按前缀区分；不写 ``json:`` 的一律按 CSS 处理，向后兼容）：

    CSS 族（``type: html``，默认）：
      ``css::text``          取文本（去空白）
      ``css::attr(href)``    取属性
      ``css::html``          取内部 HTML
      末尾加 ``[]`` 表示取全部匹配（返回列表），如 ``div.tag a::text[]``

    JSON 族（``type: api``，2026-11-10 新增）：
      ``json:jobName``        取顶层字段
      ``json:job.salary.min`` 点号路径
      ``json:items[].jobName`` 数组展开：路径中任一段末尾写 ``[]`` 表示
                                「对数组每一项继续取」，返回列表
    通用后缀：
      ``|regex:模式``         对已取到的值做二次提取（第一捕获组），两族都可用

    **为什么用 ``json:`` 前缀而不是引入 JSONPath 库**：
    1. 依赖成本 —— 项目当前没有任何 JSONPath 依赖，为一条路径语法引入一个库
       不值得；实现一次点号 + ``[]`` 展开只需几十行，且完全可控；
    2. 与 CSS 族保持同一种「前缀式」语法，学习成本只增加一个前缀；
    3. 缺字段必须返回 ``None`` 而不是抛异常 —— 接口字段常变，一条缺字段
       不该让整批失败。JSONPath 库的求值语义（到底返回空列表还是异常）
       反而需要额外包一层才能保证这一点。

    边界：**不支持下标**（``items[0]``）与过滤器（``[?(@.x)]``）。
    需要「第 N 项」时请让接口自己给，或用 ``|regex:`` 后处理 ——
    这两类语义一旦放开，就得回答「越界怎么办、多匹配怎么办」，而它们
    在抓取场景里几乎用不到。
    """

    name: str
    expr: str
    default: Any = None

    def parse_expr(self) -> Dict[str, Any]:
        """拆解表达式，返回 kind / selector | path / mode / attr / multi / regex。

        ``kind`` 是 2026-11-10 新增的判别键（``css`` / ``json``）：
        调用方据此决定「去 DOM 里找」还是「去 JSON 里走路径」。
        """
        raw = self.expr.strip()
        regex = None
        # 容忍管道两侧的空格：YAML 里人几乎一定会写成 `json:x | regex:...`，
        # 只认 `|regex:` 会让这条表达式被当成「路径里带空格」而整体报错，
        # 错误信息还会指向"路径无法解析"，离真正原因很远。
        m = re.search(r"\|\s*regex:(.+)$", raw)
        if m:
            regex = m.group(1).strip()
            raw = raw[: m.start()].strip()

        multi = raw.endswith("[]")
        if multi:
            raw = raw[: -2].strip()

        # ---------------- JSON 族 ----------------
        if raw.lower().startswith("json:"):
            path = raw[len("json:"):].strip()
            if not path:
                raise SpecError(
                    f"字段 {self.name} 的 json: 表达式缺少路径：{self.expr!r}"
                    f"（示例 json:jobName / json:job.salary.min / json:items[].jobName）"
                )
            if "::" in path:
                raise SpecError(
                    f"字段 {self.name} 的 json: 表达式不接受 ::text / ::attr(x) 后缀：{self.expr!r}"
                    f"（json: 直接取字段值；要二次提取请用 |regex:）"
                )
            if not re.fullmatch(r"[A-Za-z0-9_$]+(\[\])?(\.[A-Za-z0-9_$]+(\[\])?)*", path):
                raise SpecError(
                    f"字段 {self.name} 的 json: 路径无法解析：{path!r}"
                    f"（只支持点号路径与段末的 [] 展开；不支持下标 [0] 与过滤器）"
                )
            return {
                "kind": "json",
                "path": path,
                "selector": None,
                "mode": "json",
                "attr": None,
                "multi": multi,
                "regex": regex,
            }

        # ---------------- CSS 族 ----------------
        attr = None
        mode = "text"
        if "::" in raw:
            selector, _, tail = raw.partition("::")
            tail = tail.strip()
            if tail == "text":
                mode = "text"
            elif tail == "html":
                mode = "html"
            elif tail.startswith("attr(") and tail.endswith(")"):
                mode = "attr"
                attr = tail[5:-1].strip()
            else:
                raise SpecError(
                    f"字段 {self.name} 的表达式后缀无法识别：{tail!r}"
                    f"（CSS 支持 ::text / ::html / ::attr(x)；"
                    f"JSON 源请用 json:路径，如 json:jobName）"
                )
            raw = selector.strip()

        if not raw:
            raise SpecError(f"字段 {self.name} 缺少 CSS 选择器：{self.expr!r}")

        return {
            "kind": "css",
            "selector": raw,
            "mode": mode,
            "attr": attr,
            "multi": multi,
            "regex": regex,
            "path": None,
        }


@dataclass
class CrawlSpec:
    """一个数据源的完整声明。"""

    site: str
    label: str
    url: str
    list_container: str
    fields: List[FieldSpec]
    # 数据源类型：html（默认，CSS 选择器）/ api（JSON 接口，json: 路径表达式）
    source_type: str = "html"
    # 仅 type: api：记录数组在响应 JSON 里的路径，如 resultbody.job.items
    list_path: str = ""
    method: str = "GET"
    render: bool = False           # True = 用 Playwright（JS 渲染页）
    timeout: int = 30
    headers: Dict[str, str] = field(default_factory=dict)

    # 输入参数：名字 → {label, required, default, type}
    inputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 分页
    pagination_type: str = "none"   # none | page_param | next_link
    pagination_start: int = 1
    max_pages: int = 1
    next_selector: str = ""

    # 详情页（可选）
    detail_enabled: bool = False
    detail_link_field: str = ""
    detail_fields: List[FieldSpec] = field(default_factory=list)
    # 一次抓取里最多请求多少个详情页；0 = 不抓详情（未配置 detail 时的默认行为）。
    # 这是防「列表 100 条 × 每条详情」把目标站点打爆的第一层闸门。
    detail_max_items: int = 0

    # 限额
    max_items: int = 100
    min_interval: float = 0.0

    # 输出映射：job = 映射到 Job 模型字段；generic = 原样输出
    target: str = "generic"
    field_map: Dict[str, str] = field(default_factory=dict)

    source_path: Optional[Path] = None

    # ---------- 构造 ----------

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source_path: Path = None) -> "CrawlSpec":
        where = f"[{source_path.name}] " if source_path else ""

        site = str(data.get("site") or "").strip().lower()
        if not _SITE_ID_RE.match(site):
            raise SpecError(
                f"{where}site 标识不合法：{site!r}（要求 2-49 位，小写字母/数字/下划线/中划线）"
            )

        label = str(data.get("label") or site).strip()

        # url 既可写在顶层（简短写法），也可写在 request.url 下（分组更清晰）。
        # 两种都支持，否则示例文件与加载器会不一致 —— 那类不一致只会让人猜。
        req = data.get("request") or {}
        url = str(req.get("url") or data.get("url") or "").strip()
        if not url:
            raise SpecError(f"{where}缺少 url（顶层或 request.url，可含 {{参数名}} 占位符）")

        lst = data.get("list") or {}
        container = str(lst.get("container") or "").strip()
        list_path = str(lst.get("path") or "").strip()

        # 数据源类型：顶层 type（也容忍写在 request 下，与 url/method 同一个分组）
        source_type = str(data.get("type") or req.get("type") or "html").strip().lower()
        if source_type not in ("html", "api"):
            raise SpecError(
                f"{where}type 只能是 html（默认，CSS 选择器）或 api（JSON 接口），"
                f"收到 {source_type!r}"
            )

        if source_type == "api":
            # api 源的记录数组位置必须显式写：不像 CSS 那样能"猜"一个 container，
            # 猜错了只会得到 0 条，而 0 条在这里无法与「接口确实没数据」区分。
            if not list_path:
                raise SpecError(
                    f"{where}type: api 必须写 list.path（记录数组的 JSON 路径），"
                    f"例如 list.path: resultbody.job.items"
                )
            if container:
                raise SpecError(
                    f"{where}type: api 不接受 list.container（CSS 选择器）：{container!r}；"
                    f"JSON 源请用 list.path + json: 表达式"
                )
            if list_path and re.search(r"\[\d", list_path):
                raise SpecError(
                    f"{where}list.path 不支持下标：{list_path!r}（数组位置由接口决定，不要写死）"
                )
            if bool(req.get("render", False)):
                raise SpecError(
                    f"{where}type: api 与 render: true 冲突：api 走 httpx 取 JSON；"
                    f"要渲染 JS 页面请用 type: html + render: true"
                )
        else:
            if not container:
                raise SpecError(
                    f"{where}缺少 list.container（列表项的 CSS 选择器）；"
                    f"如果这是 JSON 接口，请写 type: api + list.path"
                )
            if list_path:
                raise SpecError(
                    f"{where}list.path 只能用于 type: api，当前 type={source_type!r}"
                )

        raw_fields = lst.get("fields") or {}
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise SpecError(f"{where}list.fields 必须是非空映射（字段名 → 抽取表达式）")

        fields = [FieldSpec(name=k, expr=str(v)) for k, v in raw_fields.items()]
        # 提前解析一次，把语法错误在加载期暴露，而不是等运行到那一页才炸
        for f in fields:
            f.parse_expr()

        detail = data.get("detail") or {}
        detail_fields = [
            FieldSpec(name=k, expr=str(v))
            for k, v in (detail.get("fields") or {}).items()
        ]
        for f in detail_fields:
            f.parse_expr()

        detail_enabled = bool(detail.get("enabled", False))
        detail_link_field = str(detail.get("link_field") or "").strip()
        # 未写 detail.max_items 时默认 10（仅在 enabled 时生效）；
        # 显式写 0 表示「知道有详情页，但这次不抓」。
        detail_max_raw = detail.get("max_items", 10)
        try:
            detail_max_items = max(0, int(detail_max_raw))
        except (TypeError, ValueError):
            raise SpecError(
                f"{where}detail.max_items 必须是整数，收到 {detail_max_raw!r}"
            )
        if detail_enabled and not detail_link_field:
            raise SpecError(
                f"{where}detail.enabled=true 但缺少 detail.link_field"
                f"（取值应为 list.fields 里存放详情页链接的字段名）"
            )
        if detail_enabled and not detail_fields:
            raise SpecError(
                f"{where}detail.enabled=true 但 detail.fields 为空 —— 抓详情页却没有任何字段要取"
            )
        if source_type == "api" and (detail_enabled or detail_fields):
            # 详情补齐只认 HTML（要 BeautifulSoup 解析）。静默忽略 detail.* 会让作者
            # 以为「配了就会抓」，实际一条都不抓 —— 那是最难查的一类假配置。
            raise SpecError(
                f"{where}type: api 不支持 detail.*（详情页补齐是 HTML 语义）："
                f"若详情就在接口里，请直接用 json: 路径取；否则请改用 type: html"
            )

        pg = data.get("pagination") or {}
        limits = data.get("limits") or {}
        mapping = data.get("mapping") or {}

        target = str(mapping.get("target") or "generic").lower()
        if target not in ("generic", "job"):
            raise SpecError(f"{where}mapping.target 只能是 generic 或 job，收到 {target!r}")

        field_map = {str(k): str(v) for k, v in (mapping.get("field_map") or {}).items()}
        if target == "job" and field_map:
            # 加载期就挡下两类笔误，而不是等入库时才发现字段全是空的：
            #   1) 左边写了不存在的标准字段（例如 company）；
            #   2) 右边引用了 list/detail 里都不存在的抽取字段。
            unknown_keys = sorted(set(field_map) - set(JOB_STANDARD_FIELDS))
            if unknown_keys:
                raise SpecError(
                    f"{where}mapping.field_map 含未知标准字段：{', '.join(unknown_keys)}"
                    f"（可用：{', '.join(JOB_STANDARD_FIELDS)}）"
                )
            # 只认真实会被抓取的来源：list.fields 恒可用；detail.fields 仅在
            # 「enabled + link_field + max_items>0」时才会被执行器消费，
            # 否则把它算进合法来源只会让用户配出一个「永远取到空值」的映射。
            known_sources = {f.name for f in fields}
            if detail_enabled and detail_link_field and detail_max_items > 0:
                known_sources |= {f.name for f in detail_fields}
            missing_sources = sorted(set(field_map.values()) - known_sources)
            if missing_sources:
                raise SpecError(
                    f"{where}mapping.field_map 引用了不可用的抽取字段：{', '.join(missing_sources)}"
                    f"（可用：{', '.join(sorted(known_sources))}；"
                    f"detail.fields 需 detail.enabled + link_field + max_items>0 才可用）"
                )
        if target == "job":
            # 没有 title 就生成不了 job_key，整行只能在入库前被丢掉。
            # 这里只告警不报错：field_map 可能故意留空（走同名字段），
            # 运行期会对确实缺失的行计数并写进任务说明，不会静默消失。
            effective_title = field_map.get("title", "title")
            # 详情页也可能提供 title（列表只给链接、标题在详情页里），故一并算上
            known_names = {f.name for f in fields} | {f.name for f in detail_fields}
            if effective_title not in known_names:
                logger.warning(
                    "%smapping.target=job 但没有任何字段映射到 title（期望抽取字段 %r），"
                    "运行期所有条目都会因缺标题而无法入库", where, effective_title,
                )

        # 登记这个站点标识：`crawler_service` 的来源白名单要认「此刻确实存在的
        # 数据源」，而 `specs/` 目录是**启动时**加载的，运行期用 `load_spec_file()`
        # 加载的源（测试、自定义目录、将来热加载）不在那份静态快照里。不登记的话，
        # 「加一个 yaml 就能接入新数据源」这条承诺会在入库环节被白名单拒掉。
        # 登记点放在唯一构造入口（yaml 与 dict 两种来源都经过这里），
        # 且放在全部校验之后，避免一个非法 spec 也把标识占进白名单。
        REGISTERED_SPEC_SITES.add(site)

        return cls(
            site=site,
            label=label,
            url=url,
            list_container=container,
            fields=fields,
            source_type=source_type,
            list_path=list_path,
            method=str(req.get("method") or "GET").upper(),
            render=bool(req.get("render", False)),
            timeout=int(req.get("timeout", 30)),
            headers={str(k): str(v) for k, v in (req.get("headers") or {}).items()},
            inputs={str(k): (v if isinstance(v, dict) else {"default": v})
                    for k, v in (data.get("inputs") or {}).items()},
            pagination_type=str(pg.get("type") or "none").lower(),
            pagination_start=int(pg.get("start", 1)),
            max_pages=max(1, int(pg.get("max_pages", 1))),
            next_selector=str(pg.get("next_selector") or ""),
            detail_enabled=detail_enabled,
            detail_link_field=detail_link_field,
            detail_fields=detail_fields,
            detail_max_items=detail_max_items,
            max_items=max(1, int(limits.get("max_items", 100))),
            min_interval=float(limits.get("min_interval", 0.0) or 0.0),
            target=target,
            field_map=field_map,
            source_path=source_path,
        )

    # ---------- 运行期辅助 ----------

    def build_params(self, keyword: str = "", city: str = "", **extra) -> Dict[str, Any]:
        """把调用方给的 keyword/city 与输入默认值合并成渲染参数。

        这样既兼容旧签名 ``crawl(keyword, city)``，也允许将来传任意参数。
        """
        params: Dict[str, Any] = {}
        for name, meta in self.inputs.items():
            params[name] = meta.get("default", "")
        # 关键字与城市是最常见的两个输入，允许隐式注入（不必在 yaml 里重复声明）
        if keyword:
            params.setdefault("keyword", keyword)
            params["keyword"] = keyword
        if city:
            params["city"] = city
        params.update(extra)
        params.setdefault("page", self.pagination_start)
        return params

    def render_url(self, params: Dict[str, Any]) -> str:
        """用参数渲染 URL 模板；缺参数时原样保留占位符并告警（不静默拼错 URL）。"""
        def _sub(m):
            key = m.group(1)
            if key not in params:
                logger.warning("[%s] URL 模板占位符 {%s} 未提供，将保留原样", self.site, key)
                return m.group(0)
            return str(params[key])

        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _sub, self.url)


def load_spec_file(path: Path) -> CrawlSpec:
    """加载单个 yaml；出错时抛 SpecError，附文件名便于排查。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SpecError(f"[{path.name}] YAML 解析失败：{e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"[{path.name}] 顶层必须是映射")
    return CrawlSpec.from_dict(raw, source_path=path)


def load_all_specs(spec_dir: Path = None) -> Dict[str, CrawlSpec]:
    """加载目录下所有 *.yaml / *.yml，返回 site → spec。

    单个文件写错**不影响其他文件**（记 error 后跳过）：否则一个笔误会让
    整个平台列表变成空，症状是「所有爬虫突然都没了」，极难定位。
    """
    directory = spec_dir or SPEC_DIR
    result: Dict[str, CrawlSpec] = {}
    if not directory.is_dir():
        return result

    for path in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
        if path.name.startswith("_"):     # 下划线开头视为禁用/草稿
            continue
        try:
            spec = load_spec_file(path)
        except SpecError as e:
            logger.error("跳过无效规格：%s", e)
            continue
        if spec.site in result:
            logger.error(
                "站点标识重复：%s（%s 与 %s），后者被忽略",
                spec.site, result[spec.site].source_path.name, path.name,
            )
            continue
        result[spec.site] = spec

    if result:
        logger.info("已加载 %s 个声明式数据源：%s", len(result), ", ".join(sorted(result)))
    return result
