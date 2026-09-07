import asyncio
import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 本地规则引擎版本号（与 Java 版保持一致语义）
MODEL_VERSION = "2.0.0"

# 进程内「当前模型偏好」，取值：
#   primary  —— 优先使用 GLM-4 云端模型
#   local    —— 优先使用 Ollama 本地模型
#   auto     —— 自动降级链（GLM-4 -> Ollama -> 规则引擎）
_current_preference = "auto"

# 模型健康状态缓存，避免每次请求都探测 Ollama
_health_cache: dict = {"data": None, "timestamp": 0}
HEALTH_CACHE_TTL = 30  # 秒


def _check_glm4() -> dict:
    """检测智谱 GLM-4 云端模型是否已配置 API Key（不实际发起请求，避免消耗额度）。"""
    configured = bool(settings.zhipuai_api_key)
    return {
        "name": "GLM-4",
        "provider": "zhipu",
        "type": "cloud",
        "available": configured,
        "description": "API Key 已配置" if configured else "未配置 API Key",
    }


async def _check_ollama() -> dict:
    """探测 Ollama 本地模型服务是否可达。"""
    if not settings.ollama_enabled:
        return {
            "name": settings.ollama_model,
            "provider": "ollama",
            "type": "local",
            "available": False,
            "description": "Ollama 未启用（OLLAMA_ENABLED=false）",
        }
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                return {
                    "name": settings.ollama_model,
                    "provider": "ollama",
                    "type": "local",
                    "available": True,
                    "description": f"Ollama 已就绪（模型：{settings.ollama_model}）",
                }
            return {
                "name": settings.ollama_model,
                "provider": "ollama",
                "type": "local",
                "available": False,
                "description": f"Ollama 返回异常状态码 {resp.status_code}",
            }
    except Exception as e:
        return {
            "name": settings.ollama_model,
            "provider": "ollama",
            "type": "local",
            "available": False,
            "description": f"Ollama 不可达（{type(e).__name__}）",
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


class ModelService:
    @staticmethod
    async def get_status() -> dict:
        """返回主 API 与本地模型健康状态。"""
        glm4 = _check_glm4()
        ollama = await _check_ollama_cached()
        return {
            "primary": glm4,
            "local": ollama,
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
        """返回所有可用模型列表。"""
        glm4 = _check_glm4()
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
        """切换当前使用的模型偏好。"""
        global _current_preference
        name = (model_name or "").strip().lower()
        valid = {"primary", "local", "auto"}
        if name not in valid:
            return {
                "success": False,
                "message": f"不支持的模型标识：{model_name}，可选值：{', '.join(sorted(valid))}",
            }
        _current_preference = name
        return {
            "success": True,
            "message": f"模型偏好已切换为 {name}",
            "preference": name,
        }

    @staticmethod
    async def reload() -> dict:
        """重载模型状态：清空健康缓存并重新探测。"""
        global _current_preference
        _health_cache["data"] = None
        _health_cache["timestamp"] = 0
        _current_preference = "auto"
        glm4 = _check_glm4()
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
        glm4 = _check_glm4()
        ollama = await _check_ollama_cached()
        return {
            "primary": glm4,
            "local": ollama,
            "timestamp": int(time.time()),
        }

    @staticmethod
    async def chat(message: str, use_local_model: bool = False, db=None) -> dict:
        """智能路由对话：根据用户选择 + 模型可用性自动分发请求。

        返回 {"response": str, "used_model": str, "success": bool}
        """
        if not message or not message.strip():
            return {"response": "", "used_model": "none", "success": False, "error": "消息内容不能为空"}

        # 延迟导入，避免模块加载时的循环依赖
        from app.services.ai_service import call_glm4_stream, call_ollama_stream
        from app.services.local_model_service import LocalModelService

        # 优先使用 Ollama 本地模型
        if use_local_model:
            ollama = await _check_ollama_cached()
            if ollama["available"]:
                try:
                    response = await _collect(call_ollama_stream(message, "", db))
                    return {"response": response, "used_model": "local", "success": True}
                except Exception as e:
                    logger.warning(f"Ollama 调用失败，降级：{e}")

        # 尊重用户切换的偏好
        ollama = await _check_ollama_cached()
        glm4 = _check_glm4()

        if _current_preference == "local" and ollama["available"]:
            try:
                response = await _collect(call_ollama_stream(message, "", db))
                return {"response": response, "used_model": "local", "success": True}
            except Exception as e:
                logger.warning(f"Ollama 调用失败，降级：{e}")

        if _current_preference == "primary" and glm4["available"]:
            try:
                response = await _collect(call_glm4_stream(message, "", db))
                return {"response": response, "used_model": "primary", "success": True}
            except Exception as e:
                logger.warning(f"GLM-4 调用失败，降级：{e}")

        # 默认降级链：GLM-4 -> Ollama -> 规则引擎
        if glm4["available"]:
            try:
                response = await _collect(call_glm4_stream(message, "", db))
                return {"response": response, "used_model": "primary", "success": True}
            except Exception as e:
                logger.warning(f"GLM-4 调用失败，降级到 Ollama：{e}")

        if ollama["available"]:
            try:
                response = await _collect(call_ollama_stream(message, "", db))
                return {"response": response, "used_model": "local", "success": True}
            except Exception as e:
                logger.warning(f"Ollama 调用失败，降级到规则引擎：{e}")

        # 规则引擎兜底，始终可用
        response = await LocalModelService.chat(message, db)
        return {"response": response, "used_model": "local_fallback", "success": True}
