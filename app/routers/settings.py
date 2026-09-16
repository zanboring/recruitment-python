# -*- coding: utf-8 -*-
"""设置中心：前端「设置」页读写 AI 服务配置（API Key / 模型 / 本地模型）。

为什么做这一层（用户明确需求「软件要在设置栏里填 API，换电脑即用」）：
通用版是绿色软件，用户不该要求他会去改 config.env。设置栏直接填 API Key，
保存后写入 config.env（与加载链同一文件，天然持久 + 下次启动自动生效），
并在当前进程内热更新 settings 单例 —— 无需重启服务即生效。

安全性：
- 读取时 Key 一律脱敏（前 4 后 4，中间 ****），绝不回传完整密钥；
- 写入只允许 RUNTIME_EDITABLE_KEYS 白名单键，越权键静默忽略；
- 仅管理员可读/写。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator

from app.config import RUNTIME_EDITABLE_KEYS, masked, settings, save_runtime_config
from app.dependencies import get_current_user, require_admin
from app.models.user import User
from app.schemas.common import Result

router = APIRouter(prefix="/api/settings", tags=["设置中心"])


class ProviderStatus(BaseModel):
    ai_provider: str
    deepseek_api_key: Optional[str] = None   # 脱敏
    deepseek_api_url: str
    deepseek_model: str
    zhipuai_api_key: Optional[str] = None    # 脱敏
    zhipuai_api_url: str
    zhipuai_model: str
    ollama_enabled: bool
    ollama_base_url: str
    ollama_model: str
    ollama_code_model: str
    ollama_tool_model: str
    editable_keys: list


@router.get("/provider")
async def get_provider_config(user: User = Depends(get_current_user)):
    """读取当前 AI 服务配置（密钥脱敏）；仅展示白名单键，其余不外泄。"""
    return Result.success(ProviderStatus(
        ai_provider=settings.ai_provider,
        deepseek_api_key=masked(settings.deepseek_api_key),
        deepseek_api_url=settings.deepseek_api_url,
        deepseek_model=settings.deepseek_model,
        zhipuai_api_key=masked(settings.zhipuai_api_key),
        zhipuai_api_url=settings.zhipuai_api_url,
        zhipuai_model=settings.zhipuai_model,
        ollama_enabled=settings.ollama_enabled,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        ollama_code_model=settings.ollama_code_model,
        ollama_tool_model=settings.ollama_tool_model,
        editable_keys=list(RUNTIME_EDITABLE_KEYS),
    ))


class ProviderUpdateRequest(BaseModel):
    """保存请求：只接收白名单内的键，空字符串表示清除该配置。"""
    ai_provider: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    deepseek_api_url: Optional[str] = None
    deepseek_model: Optional[str] = None
    zhipuai_api_key: Optional[str] = None
    zhipuai_api_url: Optional[str] = None
    zhipuai_model: Optional[str] = None
    ollama_enabled: Optional[bool] = None
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_code_model: Optional[str] = None
    ollama_tool_model: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_case(cls, data):
        """兼容前端 camelCase 契约：deepseekApiKey -> deepseek_api_key 等。"""
        if not isinstance(data, dict):
            return data
        camel_map = {
            "aiProvider": "ai_provider",
            "deepseekApiKey": "deepseek_api_key",
            "deepseekApiUrl": "deepseek_api_url",
            "deepseekModel": "deepseek_model",
            "zhipuaiApiKey": "zhipuai_api_key",
            "zhipuaiApiUrl": "zhipuai_api_url",
            "zhipuaiModel": "zhipuai_model",
            "ollamaEnabled": "ollama_enabled",
            "ollamaBaseUrl": "ollama_base_url",
            "ollamaModel": "ollama_model",
            "ollamaCodeModel": "ollama_code_model",
            "ollamaToolModel": "ollama_tool_model",
        }
        norm = {}
        for k, v in data.items():
            norm[camel_map.get(k, k)] = v
        return norm


@router.post("/provider")
async def update_provider_config(
    request: ProviderUpdateRequest,
    user: User = Depends(require_admin),
):
    """保存 AI 服务配置到 config.env 并热生效；返回写入的键与脱敏后的最新状态。"""
    pairs = {k: v for k, v in request.model_dump(exclude_unset=True).items()}
    if not pairs:
        return Result.failed("没有需要保存的配置")

    result = save_runtime_config(pairs)

    # 返回脱敏后的最新状态，前端据此刷新展示
    status = ProviderStatus(
        ai_provider=settings.ai_provider,
        deepseek_api_key=masked(settings.deepseek_api_key),
        deepseek_api_url=settings.deepseek_api_url,
        deepseek_model=settings.deepseek_model,
        zhipuai_api_key=masked(settings.zhipuai_api_key),
        zhipuai_api_url=settings.zhipuai_api_url,
        zhipuai_model=settings.zhipuai_model,
        ollama_enabled=settings.ollama_enabled,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        ollama_code_model=settings.ollama_code_model,
        ollama_tool_model=settings.ollama_tool_model,
        editable_keys=list(RUNTIME_EDITABLE_KEYS),
    )
    return Result.success({"written": result["written"], "status": status})