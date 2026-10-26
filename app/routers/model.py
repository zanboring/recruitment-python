from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.user import User
from app.schemas.common import Result
from app.services.model_service import ModelService, compat_aliases
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/model", tags=["模型管理"])


class ModelChatRequest(BaseModel):
    message: str
    use_local_model: bool = False


class ModelSwitchRequest(BaseModel):
    model_name: str

    # model_name 与 pydantic 的 protected namespace "model_" 前缀冲突，
    # 这里显式关闭该检查以消除告警（字段本身就是业务语义，不重命名是为了兼容前端）。
    model_config = {"protected_namespaces": ()}

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_case(cls, data):
        """兼容前端（Java 版契约）发送的 modelName。

        用 validator 而非 Field(alias=...)：后者在 FastAPI 构建 body schema 时
        会触发 Pydantic 的 UnsupportedFieldAttributeWarning（实测功能正常，纯噪音）。
        """
        if isinstance(data, dict) and "modelName" in data and "model_name" not in data:
            return {**data, "model_name": data["modelName"]}
        return data


@router.get("/status")
async def get_model_status(user: User = Depends(get_current_user)):
    """模型状态。

    在原生结构（primary / local / local_models / fallback / preference / version）
    之外，额外提供 Java 版前端 `ModelManager.vue` 所需的扁平字段：
    currentModel / ollamaAvailable / zhipuAvailable / modelList。
    只做字段补充，不删除原有字段，保证两端契约都不被破坏。

    本地这一级内部按角色分工为多个模型（语言类 + 代码类），因此 modelList
    把它们逐个列出，前端可直接作为可切换选项展示。
    """
    status = await ModelService.get_status()

    primary = status.get("primary", {})
    local = status.get("local", {})
    preference = status.get("preference", "auto")
    local_models = status.get("local_models", [])
    local_names = [m.get("model") for m in local_models if m.get("model")]

    role_to_index = {m.get("role"): i for i, m in enumerate(local_models)}

    if preference in ("local", "local_code"):
        # local 指向语言类模型；local_code 指向结构化（代码类）模型
        role = "tool" if preference == "local_code" else "chat"
        idx = role_to_index.get(role, 0)
        current_model = local_names[idx] if idx < len(local_names) else local.get("name")
    elif preference == "primary":
        current_model = primary.get("name")
    elif preference == "auto":
        current_model = primary.get("name") if primary.get("available") else local.get("name")
    else:
        # 偏好被设置为某个具体模型名
        current_model = preference

    # 与 /api/ai/status 共用同一份别名实现（同一规则不在两处各写一遍）
    status.update(compat_aliases(status))

    status.update({
        "currentModel": current_model,
        "ollamaAvailable": bool(local.get("available")),
        "zhipuAvailable": bool(primary.get("available")),
        "localInstalled": status.get("local_installed", []),
        "modelList": (
            ([primary.get("name")] if primary.get("available") else [])
            + local_names
            + ["规则引擎"]
        ),
    })
    return Result.success(status)


@router.get("/list")
async def get_model_list(user: User = Depends(get_current_user)):
    models = await ModelService.get_list()
    return Result.success(models)


@router.post("/switch")
@log_action("切换模型偏好")
async def switch_model(request: ModelSwitchRequest, user: User = Depends(require_admin)):
    result = await ModelService.switch(request.model_name)
    if not result.get("success"):
        return Result.failed(result.get("message", "切换失败"))
    return Result.success(result)


@router.post("/reload")
@log_action("重载模型")
async def reload_model(user: User = Depends(require_admin)):
    result = await ModelService.reload()
    return Result.success(result)


@router.get("/health")
async def health_check(user: User = Depends(get_current_user)):
    health = await ModelService.health()
    return Result.success(health)


@router.get("/usage")
async def get_usage(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """AI 用量与成本统计。

    回答三个问题（这也是「可观测」与「只是能跑」的分界线）：
      1. 花了多少 —— ``totals``
      2. 贵在哪 —— ``by_scene`` / ``by_model`` / ``by_provider``
      3. 降级生效了吗 —— ``by_tier`` 里 local / fallback 的占比与失败记录

    限于管理员访问：费用与调用量属于运营数据，且暴露模型配置细节。
    """
    from app.services import usage_service

    return Result.success(await usage_service.summary(db, days=days))


@router.post("/chat")
async def model_chat(
    request: ModelChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await ModelService.chat(request.message, request.use_local_model, db)
    if not result.get("success"):
        return Result.failed(result.get("error", "AI 服务调用失败"))
    return Result.success({
        "response": result["response"],
        "usedModel": result["used_model"],
        "success": True,
    })
