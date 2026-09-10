import asyncio
import logging
import time
import uuid
from typing import Optional

from app.config import settings
from app.services.llm_client import get_cloud_providers

logger = logging.getLogger(__name__)

# 本地规则引擎版本号（与 Java 版保持一致语义）
MODEL_VERSION = "2.0.0"

# 进程内「当前模型偏好」，取值：
#   auto        —— 自动降级链（云端 -> 本地 -> 规则引擎），默认
#   primary     —— 强制云端模型
#   local       —— 强制本地「语言类」模型（对话表达更好）
#   local_code  —— 强制本地「代码类」模型（结构化任务更快，但语言表达较弱）
#   <模型名>     —— 直接指定某个已安装的本地模型
_current_preference = "auto"

# 除语义化的固定偏好外，还允许直接指定模型名，因此单独维护合法固定值
_FIXED_PREFERENCES = {"auto", "primary", "local", "local_code"}

# 模型健康状态缓存，避免每次请求都探测 Ollama
_health_cache: dict = {"data": None, "timestamp": 0}
HEALTH_CACHE_TTL = 30  # 秒


def _check_cloud_llm() -> dict:
    """检测云端对话模型是否可用（不实际发起请求，避免消耗额度）。

    采用「多供应商」视角：只要**任意一个**服务商配了密钥就视为云端可用，
    并展示当前优先级最高的那个。这样主服务商密钥过期、备选仍可用时，
    状态页不会误导性地显示「云端不可用」。
    """
    providers = get_cloud_providers(settings)
    if not providers:
        return {
            "name": "云端模型",
            "provider": "none",
            "type": "cloud",
            "available": False,
            "description": "未配置云端密钥（DEEPSEEK_API_KEY / ZHIPUAI_API_KEY）",
        }

    primary = providers[0]
    backup_count = len(providers) - 1
    return {
        "name": primary.name,
        "provider": primary.provider,
        "type": "cloud",
        "available": True,
        "description": f"{primary.model} API Key 已配置"
                       + (f"，另有 {backup_count} 个备选" if backup_count else ""),
    }


async def _check_ollama() -> dict:
    """探测本地模型服务：既查服务可达，也查配置的模型是否真的装过。

    「服务可达」与「模型可用」是两件事：Ollama 起来了但配置里的模型没 pull 过时，
    健康检查若只看 /api/tags 的 200 就会谎报可用，直到真正调用时才 404 ——
    而降级链会把 404 当成「本地服务故障」静默落到规则引擎，表现为 AI 能力
    莫名退化。这里一次把两件事都查清，把问题暴露在状态页和日志里。
    """
    from app.services import ollama_client

    chat_model = ollama_client.resolve_model(ollama_client.ROLE_CHAT, settings)
    tool_model = ollama_client.resolve_model(ollama_client.ROLE_TOOL, settings)

    base = {
        "name": chat_model,
        "provider": "ollama",
        "type": "local",
        "chat_model": chat_model,
        "tool_model": tool_model,
    }

    if not settings.ollama_enabled:
        return {
            **base,
            "available": False,
            "installed": [],
            "missing": [],
            "description": "Ollama 未启用（OLLAMA_ENABLED=false）",
        }

    installed, error = await ollama_client.probe(cfg=settings)
    if error:
        return {
            **base,
            "available": False,
            "installed": [],
            "missing": [],
            "description": f"Ollama 不可达（{error}）",
        }

    missing = sorted(
        m for m in {chat_model, tool_model}
        if ollama_client._match_installed(m, installed) is None
    )
    chat_ready = ollama_client._match_installed(chat_model, installed) is not None

    # 逐个角色给出「配的是什么、装了没有」，便于在状态页直接定位缺哪个模型
    role_detail = [
        {
            "role": item["role"],
            "label": item["label"],
            "model": item["model"],
            "installed": ollama_client._match_installed(item["model"], installed) is not None,
        }
        for item in (
            {"role": ollama_client.ROLE_CHAT, "model": chat_model, "label": "对话生成（语言类）"},
            {"role": ollama_client.ROLE_TOOL, "model": tool_model, "label": "工具识别（结构化）"},
        )
    ]

    if not missing:
        description = f"Ollama 已就绪（{len(installed)} 个模型可用）"
    elif chat_ready:
        description = (
            f"Ollama 已就绪；未安装 {', '.join(missing)}"
            f"（该角色将回退到可用模型，可执行 ollama pull 补齐）"
        )
    else:
        description = (
            f"Ollama 可达，但对话模型 {chat_model} 未安装"
            f"（现有：{', '.join(installed) or '无'}）"
        )

    return {
        **base,
        "available": chat_ready,
        "installed": installed,
        "missing": missing,
        "models": role_detail,
        "description": description,
    }


async def _check_ollama_cached() -> dict:
    """带缓存的 Ollama 健康探测。"""
    now = time.time()
    if _health_cache["data"] is not None and now - _health_cache["timestamp"] < HEALTH_CACHE_TTL:
        return _health_cache["data"]
    result = await _check_ollama()
    _health_cache["data"] = result
    _health_cache["timestamp"] = now
    return result


async def _collect(gen) -> str:
    """把异步生成器聚合为完整字符串。"""
    parts = []
    async for chunk in gen:
        parts.append(chunk)
    return "".join(parts)


def local_candidates() -> list:
    """按优先级返回本次请求可尝试的本地模型名（去重）。

    顺序体现「先选对的、再选能用的」：
      1. 偏好指向的模型（local -> 语言类；local_code / 指定名 -> 对应模型）
      2. 语言类模型（对话质量更好，是本地对话的默认选择）
      3. 结构化模型（只装了代码模型时仍有本地推理可用，好过直接落到规则引擎）

    对 ``ai_service`` 公开：模型偏好是全局设置，两条对话入口
    （``/api/ai/chat-stream`` 与 ``/api/model/chat``）必须看到同一份顺序，
    否则「切换模型」在其中一个入口上会静默失效。
    """
    from app.services import ollama_client

    chat_model = ollama_client.resolve_model(ollama_client.ROLE_CHAT, settings)
    tool_model = ollama_client.resolve_model(ollama_client.ROLE_TOOL, settings)

    if _current_preference == "local_code":
        preferred = tool_model
    elif _current_preference in _FIXED_PREFERENCES:
        preferred = chat_model
    else:
        preferred = _current_preference  # 显式指定的模型名

    ordered = [preferred] + ollama_client.chat_candidates(settings)
    result: list = []
    for m in ordered:
        if m and m not in result:
            result.append(m)
    return result


class ModelService:
    @staticmethod
    async def get_status() -> dict:
        """返回主 API 与本地模型健康状态。

        `local` 是「本地这一级」的汇总（决定降级链能否走到它），
        `local_models` 是级内每个角色模型的配置与安装明细 —— 层级关系与
        实际降级链一致：云端 → 本地 → 规则引擎，本地内部再按角色分模型。
        """
        glm4 = _check_cloud_llm()
        ollama = await _check_ollama_cached()
        return {
            "primary": glm4,
            "local": ollama,
            "local_models": ollama.get("models", []),
            "local_installed": ollama.get("installed", []),
            "fallback": {
                "name": "规则引擎",
                "provider": "local",
                "type": "fallback",
                "available": True,
                "description": "内置规则引擎，始终可用",
            },
            "preference": _current_preference,
            "version": MODEL_VERSION,
        }

    @staticmethod
    async def get_list() -> list:
        """返回所有可用的模型层级。

        保持三级结构（cloud / local / fallback），本地这一级内部通过
        `models` 字段展开为按角色分工的多个模型，避免把「层级」和「模型」
        两种粒度混在同一个列表里。
        """
        glm4 = _check_cloud_llm()
        ollama = await _check_ollama_cached()
        return [
            glm4,
            ollama,
            {
                "name": "规则引擎",
                "provider": "local",
                "type": "fallback",
                "available": True,
                "description": "内置规则引擎，始终可用",
            },
        ]

    @staticmethod
    async def switch(model_name: str) -> dict:
        """切换当前使用的模型偏好。

        支持两类取值，兼顾「简单开关」与「精确指定」：
          - 语义化偏好：``auto`` / ``primary`` / ``local`` / ``local_code``
          - 具体模型名：必须已安装（会校验），例如 ``qwen2.5-coder:7b``

        指定模型名时做安装校验，是因为切换到一个没 pull 过的模型会表现为
        「切完就不好用了」—— 请求永远 404 并静默落到规则引擎，很难排查。
        """
        global _current_preference
        from app.services import ollama_client

        name = (model_name or "").strip()
        lowered = name.lower()

        if lowered in _FIXED_PREFERENCES:
            _current_preference = lowered
            return {
                "success": True,
                "message": f"模型偏好已切换为 {lowered}",
                "preference": lowered,
            }

        installed, _error = await ollama_client.probe(cfg=settings)
        matched = ollama_client._match_installed(name, installed) if name else None
        if matched:
            _current_preference = matched
            return {
                "success": True,
                "message": f"模型偏好已切换为指定模型 {matched}",
                "preference": matched,
            }

        available_hint = (
            f"，或任意已安装的本地模型（现有：{', '.join(installed) or '无'}）"
            if name else ""
        )
        return {
            "success": False,
            "message": (
                f"不支持的模型标识：{model_name}"
                f"，可选值：{', '.join(sorted(_FIXED_PREFERENCES))}{available_hint}"
            ),
        }

    @staticmethod
    async def reload() -> dict:
        """重载模型状态：清空健康缓存并重新探测。"""
        global _current_preference
        _health_cache["data"] = None
        _health_cache["timestamp"] = 0
        _current_preference = "auto"
        glm4 = _check_cloud_llm()
        ollama = await _check_ollama()
        _health_cache["data"] = ollama
        _health_cache["timestamp"] = time.time()
        return {
            "success": True,
            "message": "模型状态已重新加载",
            "primary": glm4,
            "local": ollama,
            "preference": _current_preference,
        }

    @staticmethod
    async def health() -> dict:
        """模型健康检查详情。"""
        glm4 = _check_cloud_llm()
        ollama = await _check_ollama_cached()
        return {
            "primary": glm4,
            "local": ollama,
            "timestamp": int(time.time()),
        }

    @staticmethod
    async def chat(message: str, use_local_model: bool = False, db=None, session_id: str = "", user_id=None) -> dict:
        """智能路由对话：根据用户选择 + 模型可用性自动分发请求。

        返回 {"response": str, "used_model": str, "success": bool}

        session_id 为空时使用一次性临时会话，避免所有调用共享同一份
        历史上下文导致跨用户串话；传入 session_id 则按会话保持上下文。
        """
        if not message or not message.strip():
            return {"response": "", "used_model": "none", "success": False, "error": "消息内容不能为空"}

        # 延迟导入，避免模块加载时的循环依赖
        # chat() 只负责「临时会话」的生命周期管理，实际后端调度在 _dispatch_chat
        from app.services.ai_service import clear_session

        temp_session = None
        if not session_id:
            temp_session = f"model-{uuid.uuid4().hex}"
            session_id = temp_session
        try:
            return await ModelService._dispatch_chat(message, use_local_model, db, session_id, user_id)
        finally:
            if temp_session:
                clear_session(temp_session, user_id)

    @staticmethod
    async def _dispatch_chat(message: str, use_local_model: bool, db, session_id: str, user_id) -> dict:
        # 延迟导入，避免模块加载时的循环依赖
        from app.services.ai_service import call_cloud_stream, call_ollama_stream
        from app.services.local_model_service import LocalModelService

        ollama = await _check_ollama_cached()
        providers = get_cloud_providers(settings)
        candidates = local_candidates()

        # 延迟导入，避免模块加载时的循环依赖；复用 ai_service 的统一记录入口，
        # 保证两条对话入口（/api/ai/chat-stream 与 /api/model/chat）的用量口径一致。
        from app.services.ai_service import _record_usage, _usage_sink

        async def try_local(model: str, tag: str):
            """尝试用指定本地模型作答；失败返回 None，由调用方继续降级。"""
            box, sink = _usage_sink()
            started = time.time()
            try:
                response = await _collect(
                    call_ollama_stream(
                        message, session_id, db, user_id, model=model, on_usage=sink
                    )
                )
                await _record_usage(
                    "chat", provider="ollama", model=model, tier="local", usage=box,
                    latency_ms=int((time.time() - started) * 1000), user_id=user_id,
                )
                return {"response": response, "used_model": tag, "success": True}
            except Exception as e:
                await _record_usage(
                    "chat", provider="ollama", model=model, tier="local", usage=box,
                    latency_ms=int((time.time() - started) * 1000),
                    success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
                )
                logger.warning("本地模型 %s 调用失败，继续降级：%s", model, e)
                return None

        async def try_local_chain(tag: str = "local"):
            """按候选顺序尝试本地模型。"""
            for model in candidates:
                hit = await try_local(model, tag)
                if hit:
                    return hit
            return None

        # 偏好明确指向本地（含直接指定模型名）时，本地优先于云端
        prefer_local = (
            use_local_model
            or _current_preference in ("local", "local_code")
            or _current_preference not in _FIXED_PREFERENCES
        )

        if prefer_local and ollama["available"]:
            hit = await try_local_chain()
            if hit:
                return hit

        # 默认链（auto）或偏好「云端」：按优先级依次尝试已配置的云端服务商
        if not prefer_local and _current_preference in ("auto", "primary"):
            for provider in providers:
                box, sink = _usage_sink()
                started = time.time()
                try:
                    response = await _collect(
                        call_cloud_stream(
                            provider, message, session_id, db, user_id, on_usage=sink
                        )
                    )
                    await _record_usage(
                        "chat", provider=getattr(provider, "provider", ""),
                        model=getattr(provider, "model", ""), tier="cloud", usage=box,
                        latency_ms=int((time.time() - started) * 1000), user_id=user_id,
                    )
                    return {"response": response, "used_model": "primary", "success": True}
                except Exception as e:
                    await _record_usage(
                        "chat", provider=getattr(provider, "provider", ""),
                        model=getattr(provider, "model", ""), tier="cloud", usage=box,
                        latency_ms=int((time.time() - started) * 1000),
                        success=False, error_msg=f"{type(e).__name__}: {e}",
                        user_id=user_id,
                    )
                    logger.warning("云端模型 %s 调用失败，尝试下一个后端：%s", provider.name, e)

        # 兜底链：本地模型依次尝试 → 规则引擎
        if ollama["available"]:
            hit = await try_local_chain()
            if hit:
                return hit

        # 规则引擎兜底，始终可用（零 token，但这条记录本身就是降级事件）
        started = time.time()
        response = await LocalModelService.chat(message, db)
        await _record_usage(
            "chat", provider="local_fallback", model="规则引擎", tier="fallback",
            latency_ms=int((time.time() - started) * 1000), user_id=user_id,
        )
        return {"response": response, "used_model": "local_fallback", "success": True}
