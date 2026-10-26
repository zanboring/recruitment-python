"""熔断器（Circuit Breaker）：防止外部依赖故障引发的级联雪崩。

**为什么需要它**：三级降级链只解决「这个后端挂了换下一个」，但若云端在
反复超时、每个请求都要等满超时时间（默认 60s）才降级，重试风暴会把慢服务
打得更慢，甚至拖垮整个服务（超时也在占用连接池）。熔断器解决的是
「已经知道它坏了，就别再试了，直接快速失败」。

三态模型（经典实现）：
  CLOSED   —— 正常；连续失败达到阈值 → OPEN
  OPEN     —— 熔断中；直接快速失败，不发起真实调用；冷却期后 → HALF_OPEN
  HALF_OPEN—— 放行一个试探请求：成功则恢复 CLOSED，失败则回到 OPEN

用法（包装 LLM 云端调用）::

    breaker = circuit_breaker("llm-cloud", failure_threshold=3, cooldown=30)

    async def guarded():
        async with breaker.guard():
            return await stream_chat(...)

    # 熔断期间 guard() 直接抛 CircuitOpenError，调用方降级到下一后端
"""
import asyncio
import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """熔断期间快速失败抛出，调用方捕获后走降级链路。"""


class CircuitBreaker:
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        enabled: bool = True,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = cooldown_seconds
        self.enabled = enabled

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._open_since = 0.0
        self._lock = threading.Lock()
        self._stats = {"success": 0, "failure": 0, "rejected": 0}

    # ---- 状态查询 ----
    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._open_since >= self.cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN
        return self._state

    @property
    def closed(self) -> bool:
        return not self.enabled or self.state == CircuitState.CLOSED

    def allow_request(self) -> bool:
        """当前是否放行请求：CLOSED 或（HALF_OPEN 且无并发试探中）。"""
        if not self.enabled:
            return True
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN:
            # 半开期间只放行一个试探请求：用失败计数非零表示「试探中」
            with self._lock:
                if self._failure_count < 0:  # 已有试探在途
                    return False
                self._failure_count = -1  # 标记试探进行中
                return True
        return False

    # ---- 调用结果上报 ----
    def record_success(self):
        with self._lock:
            self._stats["success"] += 1
            if self._state != CircuitState.CLOSED:
                logger.info("熔断器 %s 恢复（CLOSED）", self.name)
                self._state = CircuitState.CLOSED
            self._failure_count = 0

    def record_failure(self):
        with self._lock:
            self._stats["failure"] += 1
            # 半开试探失败 → 立刻回到 OPEN 并重启冷却
            if self.state == CircuitState.HALF_OPEN or self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._open_since = time.monotonic()
                logger.warning("熔断器 %s 半开试探失败，回到 OPEN", self.name)
                return
            self._failure_count += 1
            if self._state == CircuitState.CLOSED and self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._open_since = time.monotonic()
                logger.warning(
                    "熔断器 %s 触发：连续失败 %s 次 → OPEN（冷却 %ss）",
                    self.name, self._failure_count, self.cooldown_seconds,
                )
                self._failure_count = 0

    def reject(self):
        with self._lock:
            self._stats["rejected"] += 1

    # ---- 同步上下文管理器（供 async 调用内部使用） ----
    def should_attempt(self) -> bool:
        allowed = self.allow_request()
        if not allowed and self.enabled:
            self.reject()
        return allowed

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_threshold": self.failure_threshold,
                "cooldown_seconds": self.cooldown_seconds,
                "enabled": self.enabled,
                **dict(self._stats),
            }


# ---------------------------------------------------------------- Redis 状态共享（多副本全局熔断）
#
# 为什么需要：进程内熔断器在多副本下**各自为战** —— 副本 A 检测到云端持续失败
# 进入 OPEN，副本 B 毫不知情、继续向同一个已挂的上游发请求（每个都要等满超时），
# 故障因此被放大而不是被抑制。状态放 Redis 后「一次熔断、全局生效」才成立。
#
# 三条设计约束（延续 app/cache.py 的原则）：
#   1. 默认关闭（CIRCUIT_BREAKER_BACKEND=memory），单机行为与改造前完全一致；
#   2. Redis 不可用时**降级为父类的进程内实现** —— 熔断器仍工作，只是退回单副本
#      语义，绝不因为「状态存不了」让熔断能力失效；
#   3. **父类的字段与方法一行不改** —— 用子类覆写，避免与在飞改动
#      （_probe_in_flight 修复）冲突，也让单机路径的代码完全不受本次改造影响。

_BREAKER_KEY_PREFIX = "recsys:breaker:"

# 失败计数窗口（秒）：避免长期不再使用的键永久占据内存
_FAILURE_COUNT_TTL = 300.0

# 「是否放行」的原子判定（Lua）：返回 1 放行 / 0 拒绝。
# 关键在最后一步 —— 半开期用 SET NX PX 抢「试探权」，跨副本也只有一个请求能通过。
# 这与父类用本地布尔量（_probe_in_flight）表达同一语义，区别是它必须在多进程间原子。
_LUA_BREAKER_ALLOW = """
local state = redis.call('GET', KEYS[1])
if not state then
    return 1
end
local open_since = tonumber(redis.call('GET', KEYS[2]) or 0)
local now = tonumber(ARGV[1])
if now - open_since < tonumber(ARGV[2]) then
    return 0
end
local acquired = redis.call('SET', KEYS[3], '1', 'NX', 'PX', math.floor(tonumber(ARGV[2]) * 1000))
if acquired then
    return 1
end
return 0
"""

# 记录成功：清空全部状态 = 回到 CLOSED（所有副本同时恢复）
_LUA_BREAKER_SUCCESS = """
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
redis.call('DEL', KEYS[4])
return 1
"""

# 记录失败：已 OPEN（含半开试探失败）或达阈值 → OPEN；否则仅累加计数。返回 1 = 本次触发熔断
_LUA_BREAKER_FAILURE = """
if redis.call('GET', KEYS[1]) then
    redis.call('SET', KEYS[1], 'open')
    redis.call('SET', KEYS[2], ARGV[1])
    redis.call('DEL', KEYS[3])
    redis.call('DEL', KEYS[4])
    return 1
end
local failures = redis.call('INCR', KEYS[4])
redis.call('PEXPIRE', KEYS[4], math.floor(tonumber(ARGV[3]) * 1000))
if failures >= tonumber(ARGV[2]) then
    redis.call('SET', KEYS[1], 'open')
    redis.call('SET', KEYS[2], ARGV[1])
    redis.call('DEL', KEYS[4])
    return 1
end
return 0
"""


class RedisCircuitBreaker(CircuitBreaker):
    """熔断状态存 Redis 的熔断器（多副本全局熔断）。

    实现方式：**子类覆写**，不改父类 —— 父类的字段与状态机原样保留，
    默认（memory 后端）路径逐字节不受影响。

    接口与父类**完全一致且保持同步**（用同步 redis 客户端），因此
    ``circuit_breaker()`` 工厂可直接返回本类实例，调用方（llm_client）零改动。

    时间基准用**墙钟 time.time()** 而非 monotonic：不同进程的 monotonic 基准
    互不相同，跨副本比较无意义；容器共享宿主机时钟，墙钟可比。

    ``client`` 参数供测试注入替身（用例不依赖真实 Redis 进程）。
    """

    def __init__(self, name="default", failure_threshold=3, cooldown_seconds=30.0,
                 enabled=True, client=None, redis_url=""):
        super().__init__(name, failure_threshold, cooldown_seconds, enabled)
        self._client = client
        self._redis_url = redis_url
        self._connect_cooldown_until = 0.0

    # ---- Redis 连接（惰性；失败进冷却，冷却期内直接用父类实现）----
    def _redis(self):
        if self._client is not None:
            return self._client
        now = time.time()
        if now < self._connect_cooldown_until:
            return None
        from app.config import settings

        url = self._redis_url or getattr(settings, "redis_url", "")
        if not url:
            return None
        try:
            import redis as sync_redis

            client = sync_redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=1.0, socket_timeout=1.0
            )
            client.ping()
        except Exception as e:  # noqa: BLE001 - 连接失败一律降级
            self._connect_cooldown_until = now + 30.0
            logger.warning("熔断器 %s Redis 不可用（30s 内降级进程内）：%s", self.name, e)
            return None
        self._client = client
        logger.info("熔断器 %s 已启用 Redis 共享状态", self.name)
        return client

    def _keys(self):
        prefix = f"{_BREAKER_KEY_PREFIX}{self.name}:"
        return [prefix + "state", prefix + "open_since", prefix + "probe", prefix + "failures"]

    # ---- 覆写状态机（每一步都保留父类作为降级路径）----
    @property
    def state(self) -> CircuitState:
        client = self._redis()
        if client is None:
            return super().state
        try:
            if not client.get(self._keys()[0]):
                return CircuitState.CLOSED
            open_since = float(client.get(self._keys()[1]) or 0)
            if time.time() - open_since >= self.cooldown_seconds:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN
        except Exception as e:  # noqa: BLE001
            logger.warning("熔断器 %s 读状态失败，降级进程内：%s", self.name, e)
            return super().state

    def allow_request(self) -> bool:
        if not self.enabled:
            return True
        client = self._redis()
        if client is None:
            return super().allow_request()
        try:
            allowed = client.eval(
                _LUA_BREAKER_ALLOW, 3, *self._keys()[:3],
                time.time(), self.cooldown_seconds,
            )
            return int(allowed) == 1
        except Exception as e:  # noqa: BLE001
            logger.warning("熔断器 %s 放行判定失败，降级进程内：%s", self.name, e)
            return super().allow_request()

    def record_success(self):
        client = self._redis()
        if client is None:
            super().record_success()
            return
        try:
            client.eval(_LUA_BREAKER_SUCCESS, 4, *self._keys())
        except Exception as e:  # noqa: BLE001
            logger.warning("熔断器 %s 记录成功失败，降级进程内：%s", self.name, e)
            super().record_success()
            return
        with self._lock:
            self._stats["success"] += 1
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._probe_in_flight = False

    def record_failure(self):
        client = self._redis()
        if client is None:
            super().record_failure()
            return
        try:
            triggered = client.eval(
                _LUA_BREAKER_FAILURE, 4, *self._keys(),
                time.time(), self.failure_threshold, _FAILURE_COUNT_TTL,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("熔断器 %s 记录失败失败，降级进程内：%s", self.name, e)
            super().record_failure()
            return
        with self._lock:
            self._stats["failure"] += 1
            if int(triggered) == 1:
                logger.warning(
                    "熔断器 %s 触发熔断 → OPEN（冷却 %ss；状态已写入 Redis，对所有副本生效）",
                    self.name, self.cooldown_seconds,
                )

    def stats(self) -> dict:
        base = super().stats()
        base["state"] = self.state.value
        base["backend"] = "redis"
        return base


# 全局单例注册表：按名字取，供 LLM 调用层使用
_BREAKERS: dict = {}


def circuit_breaker(name: str = "llm-cloud", **kwargs) -> CircuitBreaker:
    """获取（必要时创建）同名熔断器实例。

    后端由 ``CIRCUIT_BREAKER_BACKEND`` 决定（默认 ``memory``，单机行为不变）：
    设为 ``redis`` 且配置了 ``REDIS_URL`` 时返回 :class:`RedisCircuitBreaker`，
    使熔断状态在所有副本之间共享。
    """
    if name not in _BREAKERS:
        _BREAKERS[name] = _make_breaker(name, **kwargs)
    return _BREAKERS[name]


def _make_breaker(name: str, **kwargs) -> CircuitBreaker:
    """按配置选择后端。redis_url 为空时直接走进程内（连惰性建连都不做）。"""
    from app.config import settings

    backend = str(getattr(settings, "circuit_breaker_backend", "memory") or "memory")
    if backend.strip().lower() == "redis" and getattr(settings, "redis_url", ""):
        return RedisCircuitBreaker(name=name, **kwargs)
    return CircuitBreaker(name=name, **kwargs)


def reset_all():
    """测试辅助：清空全部熔断器状态。"""
    _BREAKERS.clear()
