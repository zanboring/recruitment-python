"""本地模型（Ollama）调用层：按任务角色路由到不同的本地模型。

本地模型在本项目里不是「一个备胎」，而是按能力分工的两类：

    qwen2.5:14b        语言类 14.8B  —— 对话生成（语言理解与表达更强）
    qwen2.5-coder:7b   代码类  7.6B  —— 结构化任务（工具识别 / JSON 抽取）更快

分工依据来自本机实测，可用 ``scripts/bench_local_models.py`` 复现：

    | 任务                    | qwen2.5:14b | qwen2.5-coder:7b |
    |-------------------------|-------------|------------------|
    | 工具识别准确率           | 6/6 (100%)  | 6/6 (100%)       |
    | 工具识别平均耗时         | 7.4s        | 5.4s             |
    | 知识问答通过             | 3/3         | 2/3              |
    | 生成速度                 | 12.2 tok/s  | 72.2 tok/s       |

结论：结构化任务交给小而快的专用模型，自然语言生成交给大的通用模型 ——
按任务选「对」的模型，而不是一律选「大」的模型。这与云端走 OpenAI 兼容
统一层的设计并列，构成完整的「云端 + 本地」双通道模型路由。

所有对外函数都接受可选的 ``cfg``（默认取全局 ``settings``），与
``llm_client.get_cloud_providers(settings)`` 保持同一套注入方式，
便于测试替换配置而不必打补丁到具体模块。
"""
import json
import logging
import time
from typing import AsyncGenerator, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ---- 任务角色：决定使用哪个本地模型 ----
ROLE_CHAT = "chat"   # 自然语言生成（对话、分析报告）
ROLE_TOOL = "tool"   # 结构化抽取（工具识别、JSON 输出）

_TAGS_TIMEOUT = 3.0
_DEFAULT_TIMEOUT = 60.0

# 模型清单缓存：避免每次调用前都探测一遍 /api/tags。
# 按 base_url 分桶 —— 否则切换 OLLAMA_BASE_URL（或测试注入不同地址）时
# 会读到上一个地址的过期结果，表现为「模型明明装了却报缺失」。
_probe_cache: dict = {}
_PROBE_CACHE_TTL = 30.0


class LocalModelError(RuntimeError):
    """本地模型不可用或调用失败。"""


def _cfg(cfg=None):
    """取配置对象，未显式传入时使用全局 settings。"""
    return cfg if cfg is not None else settings


def resolve_model(role: str = ROLE_CHAT, cfg=None) -> str:
    """按任务角色返回应使用的本地模型名。

    角色到模型的映射是可配置的：``OLLAMA_TOOL_MODEL`` 显式指定工具识别模型，
    留空则回退到 ``OLLAMA_CODE_MODEL``，再回退到 ``OLLAMA_MODEL``。
    这样即使只装了一个模型，配置也不用改。
    """
    conf = _cfg(cfg)
    if role == ROLE_TOOL:
        return (
            (getattr(conf, "ollama_tool_model", "") or "").strip()
            or (getattr(conf, "ollama_code_model", "") or "").strip()
            or (getattr(conf, "ollama_model", "") or "").strip()
        )
    return (getattr(conf, "ollama_model", "") or "").strip() or "qwen2.5:14b"


def configured_models(cfg=None) -> List[dict]:
    """列出所有在配置里声明了角色的本地模型（用于状态展示）。

    同一模型承担多个角色时只保留一条，避免状态页出现重复项。
    """
    seen: dict = {}
    for role, label in ((ROLE_CHAT, "对话生成（语言类）"),
                        (ROLE_TOOL, "工具识别（结构化）")):
        model = resolve_model(role, cfg)
        seen.setdefault(model, {"model": model, "label": label, "roles": []})
        seen[model]["roles"].append(role)
    return list(seen.values())


def chat_candidates(cfg=None) -> List[str]:
    """本地对话可尝试的模型顺序（去重）。

    语言类模型优先 —— 对话表达质量更好；结构化模型作为兜底，
    用于「只 pull 了代码模型」或语言模型加载失败的情况。
    有兜底比直接掉到规则引擎要好：本地推理能力仍能发挥作用。
    """
    ordered = [resolve_model(ROLE_CHAT, cfg), resolve_model(ROLE_TOOL, cfg)]
    result: List[str] = []
    for m in ordered:
        if m and m not in result:
            result.append(m)
    return result


def _match_installed(model: str, installed: List[str]) -> Optional[str]:
    """在已安装清单里宽松匹配模型名。

    Ollama 各版本对 tag 的补全规则不一致（``qwen2.5:14b`` 可能被登记为
    ``qwen2.5:14b``，也可能因省略 tag 而写作 ``qwen2.5``），这里逐级放宽。
    """
    if not model:
        return None
    if model in installed:
        return model
    for name in installed:
        if name == f"{model}:latest":
            return name
        # 允许配置里省略 tag（qwen2.5 匹配 qwen2.5:14b 之外的任意 tag 不做，
        # 只匹配同名无 tag 的情况，避免把 7b 误当成 14b）
        if name.split(":", 1)[0] == model and ":" not in model:
            return name
    return None


async def probe(force: bool = False, cfg=None) -> Tuple[List[str], str]:
    """探测 Ollama 可达性与已安装模型清单。

    返回 ``(已安装模型名列表, 错误描述)``，错误描述为空串表示探测成功。

    刻意返回错误描述而不是抛异常：健康检查的职责是「如实报告状态」，
    把异常类型写进描述里比吞掉更有助于排查（否则只会看到一句「不可用」）。
    失败结果不写入缓存，避免网络抖动被放大成 30 秒的假故障。
    """
    base_url = getattr(_cfg(cfg), "ollama_base_url", "http://localhost:11434")
    entry = _probe_cache.get(base_url)
    now = time.time()
    if (
        not force
        and entry is not None
        and entry["models"] is not None
        and now - entry["timestamp"] < _PROBE_CACHE_TTL
    ):
        return entry["models"], ""

    try:
        async with httpx.AsyncClient(timeout=_TAGS_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            payload = resp.json() or {}
            names = [
                m.get("name", "")
                for m in payload.get("models", [])
                if m.get("name")
            ]
    except Exception as e:
        return [], type(e).__name__

    _probe_cache[base_url] = {"models": names, "timestamp": now}
    return names, ""


def reset_probe_cache() -> None:
    """清空探测缓存（测试与配置热更新时使用）。"""
    _probe_cache.clear()


async def ensure_model(model: str, cfg=None) -> None:
    """确认模型确实已安装，未安装则抛出带清单的明确错误。

    这一步的价值在于「不要盲信配置」：配置里写的模型可能根本没 pull 过。
    若跳过校验直接请求，Ollama 会返回 404，而降级链会把它当成「本地模型
    服务故障」，最终静默落到规则引擎 —— 表现为「AI 能力莫名退化」，
    极难排查。提前校验能让问题在日志里以一句人话暴露。

    探测失败（拿不到清单）时不阻拦请求：网络抖动不应该让本地推理不可用，
    真有问题时 HTTP 层会正常报错。
    """
    installed, error = await probe(cfg=cfg)
    if error or not installed:
        return
    if _match_installed(model, installed) is None:
        raise LocalModelError(
            f"本地模型 {model} 未安装（Ollama 中现有：{', '.join(installed)}）。"
            f"请执行 `ollama pull {model}`，或修正配置 OLLAMA_MODEL / OLLAMA_CODE_MODEL。"
        )


async def chat_stream(
    messages: List[dict],
    model: Optional[str] = None,
    role: str = ROLE_CHAT,
    timeout: float = _DEFAULT_TIMEOUT,
    options: Optional[dict] = None,
    cfg=None,
    on_usage=None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """流式调用本地模型，逐段产出文本。

    ``max_tokens`` 映射为 Ollama 的 ``num_predict``。**必须显式设置**：
    Ollama 默认 ``num_predict=-1`` 表示不限制输出长度，而本地模型在生成
    长回答时速度只有每秒十几个 token，一次不设上限的回答足以让用户等上
    几分钟（更不用说超时后整段作废）。
    

    ``on_usage`` 会在收到带统计的末尾分片时被调用一次，入参与云端
    （``llm_client``）保持同一形状，便于统一记录用量：

        {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}

    Ollama 只在 ``done=true`` 的那个分片里给出 ``prompt_eval_count`` /
    ``eval_count``，因此必须读到流结束才能取到用量。
    """
    conf = _cfg(cfg)
    target = model or resolve_model(role, conf)
    await ensure_model(target, conf)

    payload: dict = {"model": target, "messages": messages, "stream": True}
    merged = dict(options or {})
    if max_tokens:
        merged["num_predict"] = int(max_tokens)
    if merged:
        payload["options"] = merged

    url = f"{conf.ollama_base_url}/api/chat"
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", errors="ignore")
                raise LocalModelError(
                    f"Ollama 返回 {response.status_code}：{body[:200]}"
                )
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (data.get("message") or {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    if on_usage:
                        prompt_tokens = int(data.get("prompt_eval_count") or 0)
                        completion_tokens = int(data.get("eval_count") or 0)
                        on_usage({
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        })
                    break


async def chat_sync(
    messages: List[dict],
    model: Optional[str] = None,
    role: str = ROLE_CHAT,
    timeout: float = _DEFAULT_TIMEOUT,
    options: Optional[dict] = None,
    tools: Optional[List[dict]] = None,
    cfg=None,
    on_usage=None,
) -> dict:
    """非流式调用，返回完整的 message 结构。

    传入 ``tools`` 时会透传原生 function calling 定义。实测本机 qwen2.5:14b
    支持原生 ``tool_calls`` 返回，而 qwen2.5-coder:7b 会把裸 JSON 写进
    ``content`` —— 因此调用方不应假定某一种形态，两种都要处理。
    """
    conf = _cfg(cfg)
    target = model or resolve_model(role, conf)
    await ensure_model(target, conf)

    payload: dict = {"model": target, "messages": messages, "stream": False}
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools

    url = f"{conf.ollama_base_url}/api/chat"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise LocalModelError(
                f"Ollama 返回 {response.status_code}：{response.text[:200]}"
            )
        data = response.json() or {}
    if on_usage:
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        on_usage({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        })
    return data.get("message") or {}


async def detect_tool_call(message: str, cfg=None, on_usage=None) -> Optional[dict]:
    """用本地「结构化任务」模型判断是否需要调用工具。

    复用与云端完全相同的提示词与解析逻辑（``tool_service``），
    因此本地降级路径下的 Function Calling 行为与云端保持一致 ——
    区别只在哪个模型来「读」这段提示词。
    """
    from app.services.tool_service import (
        build_tool_detection_prompt,
        extract_tool_call,
    )

    reply = await chat_sync(
        [
            {"role": "system", "content": build_tool_detection_prompt()},
            {"role": "user", "content": message},
        ],
        role=ROLE_TOOL,
        options={"temperature": 0.1, "num_predict": 128},
        cfg=cfg,
        on_usage=on_usage,
    )
    return extract_tool_call(reply.get("content") or "")
