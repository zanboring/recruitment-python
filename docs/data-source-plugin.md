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

## 五、输出映射：generic / job

`mapping.target` 决定这个数据源的产物进哪张表。

### `target: generic`（默认）

原样输出抽取到的字段，**不写岗位表**，适合先看效果或接自定义分析。

每条记录带 `_target: generic` 标记。入库链路会把它们计数并写进任务说明
（「平台 X 是通用（generic）数据源：本次抓到 N 条分析用数据，未写入岗位表」），
**不会静默丢弃** —— 这也是历史缺陷的修复点：没有这个标记时，
`deduplicate_jobs()` 只保留有 `job_key` 的行，generic 数据会被无声吞掉，
表现为「YAML 配好了、抓到了、库里一条没有」。

### `target: job`

按 `field_map` 映射成标准岗位字典，然后走**既有入库契约**：
`crawler_service.normalize_job()` 补薪资 → `save_job()` 按 `job_key` 去重。

`field_map` 的方向是 **标准字段 → 抽取字段**（左边是固定 schema，右边引用
`list.fields` 里的键；`detail.fields` 也可以引用，但必须先满足 §六 的启用条件，
否则加载期报错）：

```yaml
mapping:
  target: job
  field_map:
    title: job_name
    company_name: company
    city: city
    salary: salary_text
    experience: exp
    education: edu
    url: link
```

规则：

| 项 | 说明 |
|---|---|
| 标准字段全集 | `title` / `company_name` / `city` / `experience` / `education` / `salary` / `min_salary` / `max_salary` / `description` / `url` / `skills` |
| 不写 `field_map` | 按**同名字段**取（`title` 找 `title`，`company_name` 找 `company_name`） |
| 加载期校验 | 左边写不存在的标准字段、或右边引用不存在的抽取字段 → **直接报错**，不会等到入库才发现字段全空 |
| `title` | 唯一硬性要求：没有它生成不了 `job_key`，该条被跳过并记 warning（不是静默丢弃） |
| 薪资（二选一） | 写 `salary` 文本（如 `15-25K`，交给既有解析器）**或** `min_salary` / `max_salary` 数值（单位：元/月，不做单位推断；只写 min 时 max 同上限） |
| `city` | 未抽到时回退到调用方传入的城市 |
| `url` | 未抽到时回退到当前页地址；相对链接会补成绝对地址 |
| `job_key` | 由统一实现 `app.utils.job_key.generate_job_key(平台, 标题, 公司, 城市)` 生成，**禁止另算一套**（否则同一岗位会因入口不同而重复入库） |

> 说明：早期版本文档已写「`target: job` 能映射进岗位表」，但代码里
> `field_map` 从未被消费 —— 配了 job 也一条都进不了库。本版把这条承诺补成实现，
> 上面的规则即实际行为。

---

## 六、详情页抓取（列表 + 详情）

列表里只给链接、正文在另一个页面的站点，用 `detail` 声明详情页：

```yaml
detail:
  enabled: true                    # 关闭（默认）时行为与「只有列表」完全一致
  link_field: link                 # 必填：list.fields 里存详情页链接的字段名
  max_items: 10                    # 本次最多抓几个详情页；0 = 不抓
  fields:
    description: "div.job-desc::text"
    company: "div.company::text"   # 与列表同名字段 → 详情页的值覆盖列表
```

执行顺序：解析列表页 → 逐条按 `link_field` 取链接 → `urljoin` 成绝对地址 →
抓详情页 → 按 `detail.fields` 用**与列表页相同的抽取语法**抽字段 →
合并 → 再走 `mapping` 映射。

合并规则：**详情页字段覆盖列表同名字段**（详情页更细）；详情页取不到（空串 /
无匹配 / 空列表）时**保留列表页原值**，不会用空值把已有数据洗掉。

### 三个必须知道的约束

1. **请求量有两层闸门**。`detail.max_items`（默认 10）与 `limits.max_items`
   取较小者 —— 前者管"详情页最多抓几条"，后者管"整个任务最多要多少条"。
   `detail.max_items: 0` 或整块不写 = **不抓详情**，与历史行为完全一致。
   这就是防「列表 100 条 × 每条详情」把目标站点打爆的那道闸。
2. **每条详情请求前都等 `_interval()`**。复用的是翻页那套节流
   （spec `min_interval` 与风控后的自适应降速取较大者），不另造 sleep，
   也不因"就十条"跳过。
3. **详情页命中风控立即停手**。详情页同样过 `detect_blocked()` / `mark_blocked()`
   （含 HTTP 429）；一旦命中就停止后续**所有**请求，而不是跳过这条接着打。
   已经抓到的列表数据照常返回。

### 单条失败怎么办

详情页超时 / 404 / 解析不到任何字段 → **只丢这一条的详情**，保留列表页字段，
继续下一条。本次抓取的详情页情况会写进任务说明（`crawler_service` 的说明条目
机制），例如：

- `平台 X 详情页：成功 8 条，失败 2 条（失败条目保留列表页字段）`
- `平台 X 详情页抓取已停止：详情页命中风控信号…（已抓到的列表数据照常保留，未再发出新请求）`
- `平台 X 详情页请求已达上限 10 条，其余条目仅保留列表页字段`

> `mapping.field_map` 引用 `detail.fields` 的前提是 `enabled: true` + `link_field` +
> `max_items > 0` 三者齐全；缺一条会在**加载期**报错，而不是让你拿到一列空值。

---

## 七、能力边界（重要，别抱不切实际的期待）

**纯配置就能做：**
- 静态页 或 JS 渲染页的**列表页**；需要时按 `detail.*` 逐条补齐**详情页**（见 §六）
- 分页：页码参数 / 下一页链接
- CSS 选择器字段抽取 + 正则后处理
- 限速、单次上限、robots 合规检查
- 输出映射：`generic` 原样输出 / `job` 按 `field_map` 映射进岗位表（见 §五）

**必须写 Python 类（放 `app/crawlers/`，在 `registry.CRAWLER_REGISTRY` 注册）：**
- 需要登录 / 人工过验证的站点（如 BOSS 直聘 → 见 `boss.py`）
- 接口加密、签名校验、token 链
- 强交互：点击展开、iframe、多步表单
- 非列表结构：图关系、递归树

这不是缺陷，是分工：**配置化覆盖"大量结构相似的简单站点"，复杂站点仍走子类**，
两种方式在注册表里共存，对外表现一致。

---

## 八、已知坑

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

### 4. 「配了 target: job 却一条都不入库」

按顺序查三处：

1. `field_map` 的右边是否指向真实存在的抽取字段：`list.fields` 恒可用，
   `detail.fields` 需要 `detail.enabled` + `link_field` + `max_items > 0` 齐全
   （写错或缺条件都会在**加载期**报错，服务起不来）；
2. `title` 是否真的抽到了非空值（缺标题的行会被跳过并记 warning）；
3. 任务说明里是否写着「有 N 条记录缺少 job_key」或「通用（generic）数据源」——
   这两类都不会入库，但会明说，不再静默。

### 5. 「配了 detail 却没补到详情字段」

按顺序查四处：

1. `detail.enabled` 是否为 `true`、`detail.max_items` 是否 > 0（0 等于关掉）；
2. `detail.link_field` 是否真的抽到了链接（相对路径会自动 `urljoin`，空值会跳过该条）；
3. 任务说明里是否写着「详情页：成功 X 条，失败 Y 条」—— 失败条目保留列表页字段；
4. 是否写着「详情页请求已达上限 N 条」—— 这是 `detail.max_items` /
   `limits.max_items` 的闸门生效了，不是 bug。

---

## 九、与"通用数据分析"的关系

本机制解决的是**数据源通用**：同一套分析链路，数据可以从任意站点/文件进来。

它**不**解决"领域通用"（比如换成电商数据后，技能画像、薪资分析这些招聘特有的
业务逻辑仍然绑在 `Job` 模型上）。要做领域通用，需要另立数据契约层 —— 那是另一个
量级的改造，不应该和本机制混为一谈。
