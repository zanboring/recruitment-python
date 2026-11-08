"""SSE 编码：含换行的 chunk 必须拆成多个 `data:` 行。

## 回归什么

2026-11-08 qoder 复审发现 `app/routers/ai.py` 一直是：

```python
yield f"data: {chunk}\n\n"
```

SSE 规范里每个 `data:` 行是**独立字段**，接收方用 `\n` 拼回。`chunk` 一旦含裸换行，
第二行起就不以 `data:` 开头 —— 事件流解析器把它当非法字段**整段丢弃**。

症状：**AI 回答只要出现换行，换行之后的正文全部消失**。
更隐蔽的是，前端 `escText()` 的 `\n → <br>` 永远拿不到换行 ——
那条「保留换行」的修复实际上是空转，而测试全绿（因为没人测过含换行的 chunk）。

## 这里锁什么

编码 → 解析 的**往返一致性**。解析器是这个文件里手写的最小实现，
等价于浏览器行为、也等价于 `app/static/index.html` 里那段手写逻辑。
"""
from app.routers.ai import _encode_sse_data, _sse_event


def _parse(stream: str) -> list:
    """最小 SSE 解析器：按空行切事件，事件内多行 data 用 `\\n` 拼回。"""
    events = []
    for evt in stream.split("\n\n"):
        lines = [ln for ln in evt.split("\n") if ln.startswith("data: ")]
        if lines:
            events.append("\n".join(ln[6:] for ln in lines))
    return events


# ---------------------------------------------------------------------------
# 编码形态
# ---------------------------------------------------------------------------

def test_单行文本编码成单个data行():
    assert _encode_sse_data("你好") == "data: 你好\n\n"


def test_含换行的文本拆成多行data():
    assert _encode_sse_data("第一行\n第二行") == "data: 第一行\ndata: 第二行\n\n"


def test_空文本仍是合法事件():
    """空 chunk 不能产生没有 data 字段的事件——那会被解析器忽略。"""
    assert _encode_sse_data("") == "data: \n\n"
    assert _parse(_encode_sse_data("")) == [""]


# ---------------------------------------------------------------------------
# 往返一致性（真正会出问题的地方）
# ---------------------------------------------------------------------------

def test_解析后能还原原始文本():
    text = "标题\n\n正文第一段\n正文第二段"
    assert _parse(_encode_sse_data(text)) == [text]


def test_连续空行不会被压缩():
    text = "第一段\n\n\n第二段"
    assert _parse(_encode_sse_data(text)) == [text]


def test_两帧之间的换行不会被吃掉():
    """`"第一行\\n"` + `"第二行"` 必须还原成两行。

    这是 `split("\\n")` 与 `splitlines()` 的区别所在：
    `splitlines()` 会吞掉末尾空行，两帧就粘成 `"第一行第二行"`。
    """
    stream = _encode_sse_data("第一行\n") + _encode_sse_data("第二行")
    parts = _parse(stream)
    assert parts == ["第一行\n", "第二行"]
    assert "".join(parts) == "第一行\n第二行"


def test_多帧拼接等价于一次性编码():
    """流式切分点不同，最终文本必须一致。"""
    chunks = ["招聘", "数据\n", "分析\n\n", "系统"]
    assert "".join(_parse("".join(_encode_sse_data(c) for c in chunks))) == "".join(chunks)


# ---------------------------------------------------------------------------
# 具名事件不受影响
# ---------------------------------------------------------------------------

def test_具名事件格式不变():
    evt = _sse_event("sources", {"a": 1})
    assert evt.startswith("event: sources\n")
    assert evt.endswith("\n\n")


# ---------------------------------------------------------------------------
# 对照：证明判据真的能抓到旧 bug
# ---------------------------------------------------------------------------

def test_旧写法确实会丢掉换行之后的内容():
    """把旧实现（`f"data: {chunk}\\n\\n"`）的输出走一遍解析器。

    这里**不去改生产文件**做反向验证：那等于临时引入一个已知 bug，
    而且安全策略会拦（拦得对）。用人造样本证明判据有效，效果等价。

    结论：`"第一行\\n第二行"` 经旧写法编码后，第二行不是合法 SSE 字段，
    解析器只拿到 `"第一行"` —— 正是「换行之后全没了」这个症状。
    """
    old_style = "data: 第一行\n第二行\n\n"
    assert _parse(old_style) == ["第一行"]

    # 新写法同样的输入不丢
    assert _parse(_encode_sse_data("第一行\n第二行")) == ["第一行\n第二行"]
