"""基于滑动窗口日志算法的内存限流器，用于保护三类高危接口：

    1. /api/auth/login      —— 防密码暴力破解（配合原有的失败 5 次锁定 30 分钟）
    2. /api/auth/register   —— 防批量注册灌水
    3. /api/ai/*、/api/model/chat —— 防 LLM Token 被刷爆，成本直通账单

实现取舍说明（面试高频追问点）：
    - 单机内存实现，不引入 Redis，符合本项目「单实例毕设部署」的定位；
      多副本水平扩展时需要替换为分布式计数器（Redis INCR + EXPIRE 或令牌桶 Lua 脚本）。
    - 使用 threading.Lock 而非 asyncio.Lock：临界区只有几次 list 操作，
      同步锁足够且天然可在同步单元测试里直接调用。
    - 滑动窗口相比固定窗口不会出现「窗口边界双倍突发」问题。
    - 默认不采信 X-Forwarded-For：该头客户端可随意伪造，若用作限流维度
      等于「换个头换一份配额」，会直接绕过登录防爆破。确实位于可信代理
      之后时，用 RATE_LIMIT_TRUST_FORWARDED_FOR=true 显式开启。
"""
import logging
import threading
import time
from collections import deque, OrderedDict

logger = logging.getLogger(__name__)

MAX_TRACKED_KEYS = 10000


class SlidingWindowLimiter:
    """滑动窗口计数器。

    check(key, limit, window) -> (allowed, retry_after, remaining)
      allowed     是否放行
      retry_after 被拒时需要等待的秒数（向上取整，供 Retry-After 头使用）
      remaining   本次放行后窗口内剩余可用配额
    """

    def __init__(self):
        self._hits: "OrderedDict[str, deque]" = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60) -> tuple:
        if limit <= 0:
            return True, 0, 0

        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            else:
                self._hits.move_to_end(key)

            cutoff = now - window
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = max(1, int(hits[0] + window - now) + 1)
                return False, retry_after, 0

            hits.append(now)

            # 容量保护：防止 key 无限增长被当作放大内存的攻击面
            while len(self._hits) > MAX_TRACKED_KEYS:
                self._hits.popitem(last=False)

            return True, 0, max(0, limit - len(hits))

    def reset(self):
        """清空全部计数，仅用于测试或运维手动解锁。"""
        with self._lock:
            self._hits.clear()


default_limiter = SlidingWindowLimiter()


# ---------------------------------------------------------------- 规则表


def resolve_limit(method: str, path: str) -> tuple:
    """根据请求方法与路径决定限流规则。

    返回 (规则名, 每分钟上限)；返回 None 表示该路径不限流。
    """
    from app.config import settings

    if not path.startswith("/api/"):
        return None

    if path == "/api/auth/login" and method == "POST":
        return ("login", settings.rate_limit_login_per_minute)
    if path == "/api/auth/register" and method == "POST":
        return ("register", settings.rate_limit_register_per_minute)
    if path.startswith("/api/ai/") or path == "/api/model/chat":
        return ("ai", settings.rate_limit_ai_per_minute)
    return ("default", settings.rate_limit_default_per_minute)


def _client_identity(scope: dict) -> str:
    """优先用 JWT 里的用户 ID 作为限流维度，未登录时退化为来源 IP。

    这样同一内网出口下的多个用户不会互相挤占配额；已登录用户换 IP
    也绕不过限制（因为按 user_id 计数）。

    代理头（X-Forwarded-For）默认**不采信**：该头由客户端完全可控，
    若直接当作限流维度，攻击者每请求伪造一个新 IP 即可获得一份新配额，
    登录接口的防爆破与注册防灌水会同时失效。确认部署在可信代理之后时，
    可通过 RATE_LIMIT_TRUST_FORWARDED_FOR=true 显式打开。
    """
    from app.config import settings

    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from app.utils.security import verify_token

            payload = verify_token(auth.split(" ", 1)[1])
            return f"user:{payload.get('sub')}"
        except Exception:
            # 无效 token 不在这里拦截，交给鉴权依赖给出明确的 401
            pass

    if settings.rate_limit_trust_forwarded_for:
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"

    client = scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


def build_too_many_requests(retry_after: int, limit: int):
    """构造 429 响应。这里不走 AppException —— 中间件在 ExceptionMiddleware
    之外，抛异常只能被兜底成 500，必须自己落成 JSONResponse。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={
            "code": 429,
            "message": f"请求过于频繁，请 {retry_after} 秒后再试",
            "data": None,
        },
        headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(limit)},
    )


class RateLimitMiddleware:
    """纯 ASGI 中间件（非 BaseHTTPMiddleware）。

    关键点：放行分支直接透传 receive/send，不拦截响应消息流，
    因此不会影响 AI 对话接口的 SSE 流式输出。
    """

    def __init__(self, app, limiter: SlidingWindowLimiter = None):
        self.app = app
        self.limiter = limiter or default_limiter

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from app.config import settings

        if not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        rule = resolve_limit(scope.get("method", "GET"), scope.get("path", ""))
        if rule is None:
            await self.app(scope, receive, send)
            return

        name, limit = rule
        window = settings.rate_limit_window_seconds
        key = f"{name}:{_client_identity(scope)}"

        allowed, retry_after, remaining = self.limiter.check(key, limit, window)
        if not allowed:
            logger.warning("触发限流 rule=%s key=%s limit=%s", name, key, limit)
            response = build_too_many_requests(retry_after, limit)
            await response(scope, receive, send)
            return

        extra_headers = [
            (b"x-ratelimit-limit", str(limit).encode()),
            (b"x-ratelimit-remaining", str(remaining).encode()),
        ]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + extra_headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
