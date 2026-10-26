"""OpenAI 兼容协议的统一 LLM 调用层。

**为什么要有这一层**

DeepSeek 与智谱 GLM 都实现 OpenAI Chat Completions 协议 —— 相同的
`/chat/completions` 端点、相同的 `messages` 入参、相同的流式分片格式
（`choices[].delta.content`）与相同的结束标记 `data: [DONE]`，
差异只在 base_url、模型名与少量厂商特有参数。

把 HTTP 细节收敛到这一层之后：
- 切换服务商只改配置（`AI_PROVIDER=deepseek|zhipu`），调用方代码零改动；
- 新增任意 OpenAI 兼容厂商（Moonshot、通义、SiliconFlow…）只需加一行配置；
- 流式解析、超时、错误处理只有一份实现，不会出现「两个厂商行为不一致」的坑。

**关于思考模式（DeepSeek V4）**
`reasoning_effort` 控制：`none` 关闭、`low/high/max` 开启。开启后流式分片里会多出
`delta.reasoning_content`（思维链），本层只取 `delta.content`（最终答案），
因为招聘问答场景不需要把思维链透出给用户；需要展示推理过程时在调用方按需扩展即可。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from app.config import settings

# 熔断器：云端连续失败后快速拒绝再试，防止超时风暴拖垮降级链
# （详见 app/utils/circuit_breaker.py；AI_CIRCUIT_BREAKER_ENABLED=false 可关闭）
from app.utils.circuit_breaker import circuit_breaker, CircuitOpenError

logger = logging.getLogger(__name__)

_cloud_breaker = circuit_breaker(
    "llm-cloud",
    failure_threshold=3,
    cooldown_seconds=30,
    enabled=True,
)

def breaker_enabled() -> bool:
    return bool(getattr(settings, "ai_circuit_breaker_enabled", True))


# 云端调用超时（秒）。思考模式（reasoning_effort 开启）耗时更长，调用方需相应放宽。
DEFAULT_TIMEOUT = 60

_VALID_EFFORTS = {"low", "high", "max"}


@dataclass(frozen=True)
class CloudProvider:
    """一个 OpenAI 兼容的云端服务商配置。"""

    name: str          # 展示名，如 "DeepSeek"
    provider: str      # 内部标识，如 "deepseek"
    api_url: str
    api_key: str
    model: str
    temperature: float = 0.7
    extra_payload: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.api_key)


def _deepseek_extra_payload(cfg=None) -> dict:
    """DeepSeek 特有的思考模式参数。

    默认 `none`（关闭）：招聘问答不需要长思维链，开启会显著增加延迟与 token 消耗。
    需要更强推理能力时把它调成 `low` / `high` / `max` 即可。
    """
    cfg = cfg or settings
    effort = (cfg.deepseek_reasoning_effort or "none").strip().lower()
    if effort in _VALID_EFFORTS:
        return {"reasoning_effort": effort}
    return {}


def get_cloud_providers(cfg=None) -> List[CloudProvider]:
    """返回所有「已配置密钥」的云端服务商，按优先级排序。

    优先级由 `AI_PROVIDER` 决定：默认 deepseek 优先、智谱作为备选；
    显式设为 zhipu 则智谱优先。这样即使主服务商的密钥过期或额度用尽，
    只要备选还配着密钥，对话链路依然可用 —— 属于「多供应商容灾」的最小实现。

    参数 `cfg` 允许调用方注入配置（默认用全局 settings）。
    之所以做成可注入：本模块与调用方（如 model_service）各自
    `from app.config import settings`，单元测试 patch 其中一处不会影响另一处，
    提供注入点后调用方传入自己的 settings 引用即可被正确隔离。
    """
    cfg = cfg or settings
    providers = [
        CloudProvider(
            name="DeepSeek",
            provider="deepseek",
            api_url=cfg.deepseek_api_url,
            api_key=cfg.deepseek_api_key,
            model=cfg.deepseek_model,
            extra_payload=_deepseek_extra_payload(cfg),
        ),
        CloudProvider(
            name="智谱 GLM",
            provider="zhipu",
            api_url=cfg.zhipuai_api_url,
            api_key=cfg.zhipuai_api_key,
            model=cfg.zhipuai_model,
        ),
    ]
    if (cfg.ai_provider or "").strip().lower() == "zhipu":
        providers.reverse()
    return [p for p in providers if p.available]


def _normalize_usage(usage: dict) -> dict:
    """把服务商返回的 usage 归一化为统一字段名。

    OpenAI 兼容协议下字段名本就一致（prompt_tokens / completion_tokens /
    total_tokens），但个别网关会省略 total_tokens。这里补齐，使云端与本地
    （``ollama_client``）上报的形状完全相同，上层记录逻辑无需分支。
    """
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or (prompt_tokens + completion_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
    }


async def stream_chat(
    provider: CloudProvider,
    messages: list,
    temperature: Optional[float] = None,
    on_usage=None,
    timeout: int = DEFAULT_TIMEOUT,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """流式调用，逐段产出回复文本。

    参数：
        on_usage  可选回调，收到 usage 数据（token 用量）时触发，用于成本统计。
                  通过 `stream_options.include_usage` 让服务端在最后一片里带上用量。
    """
    # 熔断检查：OPEN 期间快速失败（抛 CircuitOpenError 由降级链捕获）
    if breaker_enabled() and not _cloud_breaker.closed:
        raise CircuitOpenError(f"熔断器开启：{_cloud_breaker.state.value}")

    payload: Dict = {
        "model": provider.model,
        "messages": messages,
        "stream": True,
        "temperature": provider.temperature if temperature is None else temperature,
        # 让流式响应在末尾附带 usage，否则无法统计流式请求的 token 消耗
        "stream_options": {"include_usage": True},
        **provider.extra_payload,
    }
    # 输出长度上限：不传时由服务端默认值决定，可能远超预期
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key}",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", provider.api_url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                usage = chunk.get("usage")
                if usage and on_usage:
                    on_usage(_normalize_usage(usage))

                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                content = delta.get("content")   # 思考模式下另有 reasoning_content，此处不透出
                if content:
                    yield content


async def complete_chat(
    provider: CloudProvider,
    messages: list,
    temperature: float = 0.1,
    timeout: int = DEFAULT_TIMEOUT,
    on_usage=None,
    max_tokens: Optional[int] = None,
) -> str:
    """非流式调用，返回完整回复文本。

    低温度默认值（0.1）用于「工具识别」这类要求输出稳定 JSON 的场景。
    ``on_usage`` 在响应带 ``usage`` 时触发，字段形状与流式路径统一为
    ``{"prompt_tokens", "completion_tokens", "total_tokens"}``。
    """
    # 熔断检查：OPEN 期间快速失败
    if breaker_enabled() and not _cloud_breaker.closed:
        raise CircuitOpenError(f"熔断器开启：{_cloud_breaker.state.value}")

    payload: Dict = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        **provider.extra_payload,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(provider.api_url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        if breaker_enabled():
            _cloud_breaker.record_failure()
        raise
    if breaker_enabled():
        _cloud_breaker.record_success()
    if on_usage and data.get("usage"):
        on_usage(_normalize_usage(data["usage"]))
    return data["choices"][0]["message"]["content"]
