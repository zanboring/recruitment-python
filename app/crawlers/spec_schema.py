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
  · 静态或 JS 渲染的**列表页 + 详情页**
  · 分页：页码参数 / 下一页链接 / 滚动加载
  · 字段抽取：CSS 选择器、取文本或属性、正则后处理
  · 限速、单次上限、合规探针

不能（必须写 Python 类）：
  · 需要登录或人工过验证的站点（如 BOSS 直聘）—— 见 ``boss.py``
  · 接口加密、签名校验、token 链
  · 强交互（点击展开、iframe、多步表单）
  · 非列表结构（图关系、递归树）

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


class SpecError(ValueError):
    """规格文件不合法。"""


@dataclass
class FieldSpec:
    """单个字段的抽取规则。

    ``expr`` 语法：
      ``css::text``          取文本（去空白）
      ``css::attr(href)``    取属性
      ``css::html``          取内部 HTML
      末尾加 ``[]`` 表示取全部匹配（返回列表），如 ``div.tag a::text[]``
      ``regex:模式``         对已取到的值做二次提取（第一捕获组）
    """

    name: str
    expr: str
    default: Any = None

    def parse_expr(self) -> Dict[str, Any]:
        """拆解表达式，返回 selector / mode / attr / multi / regex。"""
        raw = self.expr.strip()
        regex = None
        m = re.search(r"\|regex:(.+)$", raw)
        if m:
            regex = m.group(1)
            raw = raw[: m.start()].strip()

        multi = raw.endswith("[]")
        if multi:
            raw = raw[: -2].strip()

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
                    f"字段 {self.name} 的表达式后缀无法识别：{tail!r}（支持 ::text / ::html / ::attr(x)）"
                )
            raw = selector.strip()

        if not raw:
            raise SpecError(f"字段 {self.name} 缺少 CSS 选择器：{self.expr!r}")

        return {"selector": raw, "mode": mode, "attr": attr, "multi": multi, "regex": regex}


@dataclass
class CrawlSpec:
    """一个数据源的完整声明。"""

    site: str
    label: str
    url: str
    list_container: str
    fields: List[FieldSpec]
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
        if not container:
            raise SpecError(f"{where}缺少 list.container（列表项的 CSS 选择器）")

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

        pg = data.get("pagination") or {}
        limits = data.get("limits") or {}
        mapping = data.get("mapping") or {}

        target = str(mapping.get("target") or "generic").lower()
        if target not in ("generic", "job"):
            raise SpecError(f"{where}mapping.target 只能是 generic 或 job，收到 {target!r}")

        return cls(
            site=site,
            label=label,
            url=url,
            list_container=container,
            fields=fields,
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
            detail_enabled=bool(detail.get("enabled", False)),
            detail_link_field=str(detail.get("link_field") or ""),
            detail_fields=detail_fields,
            max_items=max(1, int(limits.get("max_items", 100))),
            min_interval=float(limits.get("min_interval", 0.0) or 0.0),
            target=target,
            field_map={str(k): str(v) for k, v in (mapping.get("field_map") or {}).items()},
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
