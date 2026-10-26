"""系统信息与版本自证。

绿色版软件最常见的排障困境是「用户说功能没生效，其实是他在跑三个月前的 exe」。
把版本号、发行形态（``frozen-exe`` / ``source``）、运行环境通过接口透出来，
再看问题就能一眼定位；配合 ``/update-check``，用户也不必去 GitHub 手动比对版本号。

两个接口都要求登录：它们会暴露出运行环境细节，且 ``/update-check`` 会发起外网请求，
不适合匿名调用（本项目此前就出现过「匿名即可调用外部依赖接口」的问题）。
"""
from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import Result
from app.services import update_service
from app.version import build_info, is_frozen

router = APIRouter(prefix="/api/system", tags=["系统信息"])


@router.get("/version")
async def get_version(user: User = Depends(get_current_user)):
    """当前版本与构建信息。

    ``distribution`` 告诉前端「这是绿色版还是源码版」—— 两者的升级方式不同
    （前者下载新 Release 包，后者 ``git pull``），提示文案必须区分。
    """
    info = build_info()
    info["upgrade_hint"] = (
        "绿色版：到 Release 页下载新版压缩包，解压覆盖（配置与数据在 data/ 目录，不会被覆盖）"
        if is_frozen()
        else "源码版：git pull origin main 后重新执行 python scripts/init_db.py"
    )
    return Result.success(info)


@router.get("/update-check")
async def update_check(user: User = Depends(get_current_user)):
    """检查 GitHub 上是否有新版本。

    永远不会失败：网络不可达、被限流、还没发过 Release 都会以
    ``status`` 字段如实返回（详见 ``app/services/update_service.py``）。
    """
    return Result.success(await update_service.check_for_update())
