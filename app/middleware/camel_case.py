"""响应字段 camelCase 兼容中间件。

**背景**：前端（Vue3 + Element Plus，原本配套 Java 版后端）按 camelCase 消费字段
—— `companyName` / `qualityScore` / `pageNum` / `createdAt` …；
而 Python 版遵循 PEP8 返回 snake_case —— `company_name` / `quality_score` / `page_num`。

路径修好之后，字段名不一致会让列表页大面积显示 `undefined`，
比 404 更隐蔽（接口通、页面空）。

**方案**：对 `application/json` 响应，递归地为每个 snake_case key **追加** camelCase 别名，
保留原 key 不动，使两套命名都能读到。这样 Python 版自身的接口契约不被破坏。

取舍说明：
- 仅在「原 key 含下划线」且「别名尚未存在」时追加，绝不覆盖业务字段；
- 不处理 `text/event-stream`（SSE 流式对话）与二进制（xlsx 导出等），避免破坏流式与文件下载；
- 响应体需缓冲后改写，因此设了体积上限，超大 JSON 原样放行；
- 改写后移除 `content-length`，交由 ASGI 服务器重新计算（uvicorn 会用 chunked 或重算长度）。
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# 单次改写的体积上限：超过则原样透传，避免大响应带来额外内存与 CPU 开销
MAX_REWRITE_BYTES = 2 * 1024 * 1024

# 递归深度上限，防御异常深层的嵌套结构
MAX_DEPTH = 8

_SNAKE_CHAR = re.compile(r"_([a-z0-9])")


def to_camel_case(key: str) -> str:
    """snake_case → camelCase。已是 camelCase 或含大写则原样返回。"""
    if "_" not in key:
        return key
    return _SNAKE_CHAR.sub(lambda m: m.group(1).upper(), key)


def add_camel_aliases(value, depth: int = 0):
    """递归为 dict 的 snake_case key 追加 camelCase 别名（保留原 key）。"""
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, dict):
        result = {k: add_camel_aliases(v, depth + 1) for k, v in value.items()}
        for key in list(result.keys()):
            if not isinstance(key, str):
                continue
            alias = to_camel_case(key)
            if alias != key and alias not in result:
                result[alias] = result[key]
        return result
    if isinstance(value, list):
        return [add_camel_aliases(item, depth + 1) for item in value]
    return value


class CamelCaseCompatMiddleware:
    """纯 ASGI 中间件，只改写 JSON 响应体，不触碰流式与二进制响应。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state = {"rewritable": False, "chunks": [], "size": 0, "overflow": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                content_type = ""
                for name, value in headers:
                    if name.lower() == b"content-type":
                        content_type = value.decode("latin-1").lower()
                        break

                state["rewritable"] = "application/json" in content_type
                if state["rewritable"]:
                    # 改写后长度会变，必须去掉旧的 content-length，否则客户端读取错位
                    message["headers"] = [
                        (n, v) for n, v in headers if n.lower() != b"content-length"
                    ]
                await send(message)
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            if not state["rewritable"]:
                await send(message)
                return

            body = message.get("body", b"") or b""
            state["size"] += len(body)
            if state["size"] > MAX_REWRITE_BYTES:
                # 超限：把已缓冲的内容原样吐出，后续也继续透传。
                #
                # 注意这里的副作用：响应不再带 camelCase 别名，按 camelCase 取值的
                # 前端字段会变成 undefined。接口不会报错，页面只是"某些列空了" ——
                # 属于极难定位的问题，因此必须留下日志，让「为什么字段没了」有据可查。
                if not state["overflow"]:
                    logger.warning(
                        "响应体超过 %d 字节，已跳过 camelCase 字段别名改写；"
                        "依赖 camelCase 的前端字段可能读到 undefined。"
                        "如确实需要改写，请调大 MAX_REWRITE_BYTES 或对该接口做分页。",
                        MAX_REWRITE_BYTES,
                    )
                state["overflow"] = True
                for chunk in state["chunks"]:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                state["chunks"] = []
                await send({"type": "http.response.body", "body": body,
                            "more_body": bool(message.get("more_body"))})
                return

            state["chunks"].append(body)

            if message.get("more_body"):
                return

            raw = b"".join(state["chunks"])
            state["chunks"] = []
            await send({
                "type": "http.response.body",
                "body": _rewrite(raw),
                "more_body": False,
            })

        await self.app(scope, receive, send_wrapper)


def _rewrite(raw: bytes) -> bytes:
    """尝试给 JSON 响应增加 camelCase 别名；失败则原样返回。"""
    if not raw:
        return raw
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw

    try:
        rewritten = add_camel_aliases(data)
        return json.dumps(rewritten, ensure_ascii=False).encode("utf-8")
    except Exception as e:  # noqa: BLE001 - 兼容层绝不能影响正常响应
        logger.warning("camelCase 兼容改写失败，返回原始响应：%s", e)
        return raw
