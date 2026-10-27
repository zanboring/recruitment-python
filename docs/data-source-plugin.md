# 声明式数据源：加一个 YAML 就能接入新站点

> 目标：**接入一个新的数据源不需要改 Python 代码**。
> 在 `app/crawlers/specs/` 放一个 yaml，重启服务即可。

---

## 一、它解决什么问题

原先接入一个站点要写一个 `BaseCrawler` 子类，而且基类签名 `crawl(keyword, city)`
本身带招聘语义（只能表达"关键词 + 城市"）。

现在把"站点长什么样"抽象成**数据**（YAML），交给通用执行器 `SpecCrawler` 去跑：

```
app/crawlers/specs/*.yaml   ← 数据源声明（你改这里）
        │
        ▼
app/crawlers/spec_schema.py ← 规格解析与校验（不用改）
        │
        ▼
app/crawlers/spec_crawler.py ← 通用执行器（不用改）
        │  复用 BaseCrawler：随机 UA / 请求头协变 / 自适应降速 / 风控检测 / 指数退避重试
        ▼
app/crawlers/registry.py    ← 自动注册（不用改）
        │
        ▼
平台列表 / 抓取任务 / 数据入库 —— 与原有 Python 爬虫完全一致
```

---

## 二、五步接入新站点

1. 复制 `app/crawlers/specs/quotes_demo.yaml` 为新文件，例如 `my_site.yaml`
2. 改 `site`（小写标识）、`label`（中文名）、`url`
3. 用浏览器 DevTools 找到**列表项**的 CSS 选择器，填进 `list.container`
4. 把要抽的字段写成 `字段名: "CSS选择器::取值方式"`
5. 重启服务。`/api/crawler/platforms` 里就会出现这个平台，`implemented: true`

不需要注册、不需要 import、不需要动任何 `.py`。

---

## 三、最小示例

```yaml
site: my_site
label: 我的数据源

request:
  url: "https://example.com/list?page={page}"
  render: false          # true 则用 Playwright 渲染（JS 站点才需要）

pagination:
  type: page_param       # page_param | next_link | none
  start: 1
  max_pages: 3

list:
  container: "div.item"
  fields:
    title: "h3::text"
    link: "a::attr(href)"
    tags: "span.tag::text[]"

limits:
  max_items: 50
  min_interval: 2.0

mapping:
  target: generic        # generic = 原样输出；job = 按 field_map 映射到岗位模型
```

---

## 四、字段抽取语法

| 写法 | 含义 |
|---|---|
| `selector::text` | 取文本（自动去首尾空白） |
| `selector::html` | 取内部 HTML |
| `selector::attr(href)` | 取属性 |
| `selector::text[]` | 取**全部**匹配，返回列表 |
| `selector::text\|regex:(\d+)` | 取到后再用正则提第一个捕获组 |

示例：从 `"￥15-25K"` 里取最小值 → `"span.salary::text|regex:(\d+)-\d+"` → `15`

---

## 五、能力边界（重要，别抱不切实际的期待）

**纯配置就能做：**
- 静态页 或 JS 渲染页的列表 + 详情
- 分页：页码参数 / 下一页链接
- CSS 选择器字段抽取 + 正则后处理
- 限速、单次上限、robots 合规检查

**必须写 Python 类（放 `app/crawlers/`，在 `registry.CRAWLER_REGISTRY` 注册）：**
- 需要登录 / 人工过验证的站点（如 BOSS 直聘 → 见 `boss.py`）
- 接口加密、签名校验、token 链
- 强交互：点击展开、iframe、多步表单
- 非列表结构：图关系、递归树

这不是缺陷，是分工：**配置化覆盖"大量结构相似的简单站点"，复杂站点仍走子类**，
两种方式在注册表里共存，对外表现一致。

---

## 六、已知坑

### 1. 请求头不要声明自己解不了的编码

`BaseCrawler.get_random_headers()` 固定写 `Accept-Encoding: gzip, deflate, br`。
若环境里**没装 brotli**，服务器会真返回 br 压缩体，客户端解不开 → 拿到乱码。

症状极具误导性：**「列表选择器无匹配，0 条」**，看起来像站点改版。

`SpecCrawler` 已按实际解码能力动态声明（`_supported_encodings()`），并在
`requirements.txt` 里显式声明了 `brotli`。自定义 Python 爬虫走 httpx 时也要注意。

### 2. 选择器失效是最常见的运行期故障

站点改版后表现为 0 条 + 日志告警。排查顺序：
1. 用 `curl` 直接拉页面，确认 HTML 结构没变
2. 确认拿到的是 HTML 而不是错误页（`SpecCrawler` 已检查 content-type）
3. 再对比选择器

### 3. 一个 yaml 写错不会拖垮其他数据源

加载时逐文件捕获异常，坏文件记 error 后跳过。
但如果 `site` 标识重复，**后加载的会被忽略**（日志会写明两个文件名）。

---

## 七、与"通用数据分析"的关系

本机制解决的是**数据源通用**：同一套分析链路，数据可以从任意站点/文件进来。

它**不**解决"领域通用"（比如换成电商数据后，技能画像、薪资分析这些招聘特有的
业务逻辑仍然绑在 `Job` 模型上）。要做领域通用，需要另立数据契约层 —— 那是另一个
量级的改造，不应该和本机制混为一谈。
