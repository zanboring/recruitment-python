"""请求上下文中间件：让任意层代码都能拿到当前 HTTP 请求的元信息。

**为什么需要它**：
本项目的 endpoint 普遍把请求体参数命名为 `request`（如 `request: LoginRequest`），
因此 `log_action` 装饰器无法从 kwargs 里拿到 Starlette 的 Request 对象，
操作日志的 `uri` / `ip` 字段会永远是空 —— 日志等于丢了一半信息。

逐个改 endpoint 签名去注入 Request 侵入性太大，这里改用 contextvars：
中间件在请求进入时把 method / path / client_ip 写进上下文变量，
需要的地方（如 log_decorator）直接读取，业务代码零改动。

注意：只记录 TCP 层的真实对端地址，不采信 X-Forwarded-For ——
代理头可被客户端伪造，用于写日志会产生误导（限流模块另有独立的可信代理开关）。
"""
import contextvars

# 当前请求的元信息；非 HTTP 上下文（后台任务、单元测试直调）时为 None
_request_context: contextvars.ContextVar = contextvars.ContextVar(
    "request_context", default=None
)


def set_request_context(method: str, path: str, client_ip: str, query: str = ""):
    """写入当前请求上下文，返回用于重置的 token。"""
    return _request_context.set(
        {"method": method, "path": path, "client_ip": client_ip, "query": query}
    )


def get_request_context() -> dict | None:
    """读取当前请求上下文；不在请求中时返回 None。"""
    return _request_context.get()


def reset_request_context(token) -> None:
    _request_context.reset(token)


class RequestContextMiddleware:
    """纯 ASGI 中间件：把请求元信息放进 contextvars。

    不包一层 BaseHTTPMiddleware，是因为后者会缓冲/包装响应流，
    对 SSE 流式接口不友好。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else ""
        query_bytes = scope.get("query_string", b"") or b""
        token = set_request_context(
            method=scope.get("method", ""),
            path=scope.get("path", ""),
            client_ip=client_ip,
            query=query_bytes.decode("utf-8", errors="ignore")[:200],
        )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_context(token)
