"""滑动窗口限流：默认进程内内存实现，多副本可切换 Redis 后端。

守护三类高危接口：

   1. /api/auth/login      —— 防密码暴力破解（配合原有的失败 5 次锁定 30 分钟）
   2. /api/auth/register   —— 防批量注册灌水
   3. /api/ai/*、/api/model/chat —— 防 LLM Token 被刷爆，成本直通账单

后端选择（``RATE_LIMIT_BACKEND``，默认 ``memory``，见 app/config.py）：
    - ``memory``：进程内滑动窗口，单进程部署完全正确、零依赖；
    - ``redis`` ：计数放 Redis（Lua + ZSET），**多副本共享**。
      为什么多副本必须换：内存实现下每个 worker 各持一份计数，登录限流
      10 次/分在 2 副本下实际放行 20 次 —— 防爆破直接形同虚设。
      Redis 不可用时自动降级回内存并记 warning，请求不中断。

实现取舍说明（面试高频追问点）：
    - **为什么必须是 Lua 而不是 INCR + EXPIRE**：INCR 是固定窗口，窗口边界会出现
      双倍突发（前一窗口末尾与后一窗口开头各放行 limit 个）。ZSET + 时间戳天然
      滑动，但「清理过期 → 计数 → 写入」三步必须原子完成，否则并发下两个请求会
      同时读到 count = limit-1 而都放行；Lua 在 Redis 单线程内执行，天然原子。
    - **为什么 ZSET 的 member 必须唯一**：member 相同会覆盖而非累加，若拿毫秒
      时间戳当 member，同一毫秒内的并发请求会互相覆盖，计数偏小 → 限流失效。
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
import uuid
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


# ---------------------------------------------------------------- Redis 后端
#
# 多副本部署时限流计数必须跨进程共享，否则 N 个副本各算一份，
# 实际配额变成 N 倍（登录防爆破形同虚设）。压测口径见 tests/test_rate_limit_redis.py。
#
# 降级契约：RedisSlidingWindowLimiter.check() 在 Redis 不可用时返回 None，
# 由调用方（中间件）回退内存 limiter —— 限流是保护手段，
# 不该因 Redis 故障变成请求失败（5xx）的来源。

# 键前缀：与 app/cache.py 的缓存键隔离，避免互相误伤
_REDIS_KEY_PREFIX = "recsys:ratelimit:"

# 连接/执行失败后的冷却时长（秒）：冷却期内直接走内存降级，
# 不每请求都重试一次（否则故障时每个请求都要白等一次连接超时）。
_REDIS_RECONNECT_COOLDOWN = 30.0

# 滑动窗口 Lua 脚本（ZSET 实现）。
#   KEYS[1] = zset key；ARGV = {now_ms, window_ms, limit, member}
#   返回 {1, 0, remaining}       —— 放行
#        {0, oldest_score_ms, 0} —— 拒绝（oldest 供调用方换算 Retry-After）
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

-- 1) 移除窗口外的旧记录（滑动语义：老请求过期后立即腾出配额）
redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local count = redis.call('ZCARD', key)

if count >= limit then
    -- 2) 超限：返回最早记录的毫秒分数，供调用方换算 Retry-After
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    redis.call('PEXPIRE', key, window_ms)
    if oldest[2] then
        return {0, math.floor(tonumber(oldest[2])), 0}
    end
    return {0, now_ms, 0}
end

-- 3) 放行：记录本次请求。
--    member 必须唯一 —— 同毫秒并发时若拿时间戳当 member 会互相覆盖，计数偏小。
redis.call('ZADD', key, now_ms, member)
-- 过期时间 = 窗口 + 1s 余量：长期不活跃的 key 自动回收，不会无限堆积
redis.call('PEXPIRE', key, window_ms + 1000)
return {1, 0, limit - count - 1}
"""


class RedisSlidingWindowLimiter:
    """Redis 滑动窗口限流（多副本共享计数）。

    与 ``SlidingWindowLimiter`` 的接口差异只有一个：``check`` 是 **async**
    （Redis 往返必须异步）；返回值语义完全一致：
    ``(allowed, retry_after, remaining)``。

    降级契约：Redis 不可用（未配置 / 连接失败 / 脚本报错）时返回 ``None``，
    调用方据此回退内存实现 —— **不抛异常**：限流故障不能变成请求失败。

    ``client`` 参数用于测试注入替身（用例不依赖真实 Redis 进程即可跑）。
    """

    def __init__(self, redis_url: str = "", client=None, connect_timeout: float = 1.0):
        self._redis_url = redis_url
        self._client = client
        self._connect_timeout = connect_timeout
        self._cooldown_until = 0.0

    async def _get_client(self):
        """惰性建连；失败进入冷却期，冷却期内直接返回 None（走内存降级）。"""
        if self._client is not None:
            return self._client

        now = time.monotonic()
        if now < self._cooldown_until:
            return None

        from app.config import settings

        url = self._redis_url or settings.redis_url
        if not url:
            # 未配置地址：不算故障，不记 warning、不进冷却（静默走内存）
            return None

        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                url, decode_responses=True, socket_connect_timeout=self._connect_timeout
            )
            await client.ping()
        except Exception as e:  # noqa: BLE001 - 连接失败一律降级
            self._cooldown_until = now + _REDIS_RECONNECT_COOLDOWN
            logger.warning(
                "限流 Redis 后端不可用（%.0fs 内降级内存）：%s",
                _REDIS_RECONNECT_COOLDOWN, e,
            )
            return None

        self._client = client
        logger.info("限流已启用 Redis 后端：%s", url)
        return client

    async def check(self, key: str, limit: int, window: int = 60):
        """返回 ``(allowed, retry_after, remaining)``；Redis 不可用返回 ``None``。"""
        if limit <= 0:
            return True, 0, 0

        client = await self._get_client()
        if client is None:
            return None

        now_ms = int(time.time() * 1000)
        window_ms = int(window) * 1000
        member = f"{now_ms}-{uuid.uuid4().hex[:12]}"

        try:
            raw = await client.eval(
                _SLIDING_WINDOW_LUA,
                1,
                f"{_REDIS_KEY_PREFIX}{key}",
                now_ms,
                window_ms,
                limit,
                member,
            )
        except Exception as e:  # noqa: BLE001 - 脚本/网络异常一律降级
            self._client = None  # 连接可能已坏，下次重建
            self._cooldown_until = time.monotonic() + _REDIS_RECONNECT_COOLDOWN
            logger.warning("限流 Redis 脚本执行失败，本次降级内存：%s", e)
            return None

        allowed = int(raw[0]) == 1
        if allowed:
            return True, 0, int(raw[2])

        oldest_ms = int(raw[1])
        wait_ms = max(0, oldest_ms + window_ms - now_ms)
        retry_after = max(1, wait_ms // 1000 + 1)
        return False, retry_after, 0


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

    def __init__(self, app, limiter: SlidingWindowLimiter = None, redis_limiter=None):
        self.app = app
        self.limiter = limiter or default_limiter
        self._redis_limiter = redis_limiter

    def _redis_enabled(self) -> bool:
        """是否启用 Redis 后端：需显式配置 ``RATE_LIMIT_BACKEND=redis`` **且**有 ``REDIS_URL``。

        redis_url 为空时直接返回 False（连惰性建连的开销都省掉）——
        保证「不配 Redis 时项目与改造前完全一致」。

        每次请求都重新读配置（而不是构造时固化），这样设置页热更新后无需重启。
        """
        from app.config import settings

        backend = str(getattr(settings, "rate_limit_backend", "memory") or "memory")
        return backend.strip().lower() == "redis" and bool(settings.redis_url)

    def _get_redis_limiter(self) -> RedisSlidingWindowLimiter:
        if self._redis_limiter is None:
            self._redis_limiter = RedisSlidingWindowLimiter()
        return self._redis_limiter

    async def _check(self, key: str, limit: int, window: int) -> tuple:
        """统一出口：Redis 优先（多副本共享计数），不可用则降级内存。

        内存分支直接复用 ``self.limiter.check``（同步实现），
        行为与改造前完全一致 —— 这是「单机不回归」的保证。
        """
        if self._redis_enabled():
            result = await self._get_redis_limiter().check(key, limit, window)
            if result is not None:
                return result
            # Redis 不可用 → 本次降级内存（limiter 内已记 warning）
        return self.limiter.check(key, limit, window)

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

        allowed, retry_after, remaining = await self._check(key, limit, window)
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
