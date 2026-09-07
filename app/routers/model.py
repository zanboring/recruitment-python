from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import Result
from app.services.model_service import ModelService

router = APIRouter(prefix="/api/model", tags=["模型管理"])


class ModelChatRequest(BaseModel):
    message: str
    use_local_model: bool = False


class ModelSwitchRequest(BaseModel):
    model_name: str


@router.get("/status")
async def get_model_status():
    status = await ModelService.get_status()
    return Result.success(status)


@router.get("/list")
async def get_model_list():
    models = await ModelService.get_list()
    return Result.success(models)


@router.post("/switch")
async def switch_model(request: ModelSwitchRequest):
    result = await ModelService.switch(request.model_name)
    if not result.get("success"):
        return Result.failed(result.get("message", "切换失败"))
    return Result.success(result)


@router.post("/reload")
async def reload_model():
    result = await ModelService.reload()
    return Result.success(result)


@router.get("/health")
async def health_check():
    health = await ModelService.health()
    return Result.success(health)


@router.post("/chat")
async def model_chat(request: ModelChatRequest, db: AsyncSession = Depends(get_db)):
    result = await ModelService.chat(request.message, request.use_local_model, db)
    if not result.get("success"):
        return Result.failed(result.get("error", "AI 服务调用失败"))
    return Result.success({
        "response": result["response"],
        "usedModel": result["used_model"],
        "success": True,
    })
