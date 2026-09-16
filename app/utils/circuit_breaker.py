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


# 全局单例注册表：按名字取，供 LLM 调用层使用
_BREAKERS: dict = {}


def circuit_breaker(name: str = "llm-cloud", **kwargs) -> CircuitBreaker:
    """获取（必要时创建）同名熔断器实例。"""
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(name=name, **kwargs)
    return _BREAKERS[name]


def reset_all():
    """测试辅助：清空全部熔断器状态。"""
    _BREAKERS.clear()
