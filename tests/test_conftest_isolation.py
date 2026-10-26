# -*- coding: utf-8 -*-
"""锁住 conftest 的后端隔离（W1 / A11 的回归闸门）。

**为什么需要专门的用例**：这几个开关是**运行时配置**（`config.env` / 环境变量），
一旦 conftest 忘记固定它们，症状离根因极远 —— 表现为「569 个 F/E +
大量 ERROR at setup」的限流雪崩：

    限流计数（login:ip:127.0.0.1）跨用例全局累积
      → 很快超过 limit=10
      → 后续所有需要登录态的 fixture 在 setup 阶段拿到 429
      → 雪崩

把它写成可执行断言，将来有人删掉 conftest 里那三行时，这里会立刻红。

**关于触发条件（重要）**：在**默认环境**下这几个值本来就等于默认值，所以本文件的
断言恒真；它真正的价值在于「运行时配置确实启用了 Redis」时 —— 那正是
A11 的验收场景（故意启用 Redis 后再跑全量，必须仍全绿）。
因此**验收 A11 时不能省掉「启用 Redis」这一步**，否则这个闸门不会被触发。
"""
import pytest


class TestBackendIsolation:
    def test_限流后端在测试环境固定为内存(self):
        from app.config import settings

        assert settings.rate_limit_backend == "memory", (
            "conftest 未隔离 rate_limit_backend：运行时启用 Redis 后，"
            "限流计数会跨用例累积，登录 fixture 会集体拿到 429"
        )

    def test_熔断后端在测试环境固定为内存(self):
        from app.config import settings

        assert settings.circuit_breaker_backend == "memory", (
            "conftest 未隔离 circuit_breaker_backend：熔断状态会跨用例共享"
        )

    def test_采集配额后端在测试环境固定为内存(self):
        from app.config import settings

        assert settings.quota_backend == "memory", (
            "conftest 未隔离 quota_backend：运行时启用 Redis 后，"
            "采集日配额会跨用例累积，后续采集类用例会被配额莫名拦下"
        )

    def test_会话历史后端在测试环境固定为内存(self):
        from app.config import settings

        assert settings.session_backend == "memory", (
            "conftest 未隔离 session_backend：运行时启用 Redis 后，"
            "会话历史会写进真实 Redis（用例依赖真实进程，违反任务书 §3 约束 2）"
        )

    def test_redis_url在测试环境为空(self):
        """地址为空 ⇒ 限流 / 熔断 / 缓存的 Redis 路径都不会被启用。"""
        from app.config import settings

        assert settings.redis_url == "", (
            "conftest 未清空 redis_url：测试会尝试连真实 Redis 进程，"
            "违反「新用例不得依赖真实 Redis」的约束（任务书 §3 约束 2）"
        )


class TestRedisPathTestsUseFakes:
    """Redis 路径的用例必须自带替身，不得依赖真实 Redis 进程。"""

    def test_限流用例自带替身(self):
        from tests.test_rate_limit_redis import FakeRedis

        assert hasattr(FakeRedis, "eval")

    def test_熔断用例自带替身(self):
        from tests.test_circuit_breaker_redis import FakeRedisSync

        assert hasattr(FakeRedisSync, "eval")

    @pytest.mark.parametrize("module_name", ["test_rate_limit_redis", "test_circuit_breaker_redis"])
    def test_替身不导入真实redis客户端(self, module_name):
        """替身文件里不应出现「为跑测试而真连 Redis」的痕迹。

        允许 `import redis`（实现层用得到），但**不允许**出现连向
        127.0.0.1:6379 / localhost:6379 的默认地址 —— 那意味着用例在依赖真实进程。
        """
        from pathlib import Path

        source = Path(__file__).parent.joinpath(f"{module_name}.py").read_text(encoding="utf-8")
        assert "127.0.0.1:6379" not in source
        assert "localhost:6379" not in source
