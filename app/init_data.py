"""初始化数据：首次启动创建默认管理员。

## 为什么这个文件值得单独写注释

它原本只有 15 行，但做的是**安全链的起点**：写死一个默认口令 + 在日志里打印明文。
两件事叠加，再配上「默认监听 0.0.0.0」和「免密登录接口」，默认部署就等于管理员公开。

现在的规则：

| 环境 | 口令来源 | 是否落日志 |
|---|---|---|
| 显式设置 `ADMIN_INITIAL_PASSWORD` | 该环境变量 | 否 |
| `production` 且未设置 | **随机生成**，写入 `data/admin-initial-password.txt` | 否（只给路径） |
| `development` 且未设置 | 本地默认口令 | 否 |

生产环境不再存在**固定**默认口令 —— 源码是公开的（本项目有 GitHub 公开版），
写死的口令等于全员已知凭据。
"""
import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.utils.security import hash_password

logger = logging.getLogger(__name__)

# 仅用于本地开发/演示。生产环境永远走不到这里（见 _resolve_initial_password）。
# 值保持不变：README / docs 里多处按这个口令写操作步骤，改了会让「照文档跑却登不进去」。
# 真正要解决的是「生产环境不该有固定口令」，而不是「把这个值换个新的」。
_DEV_DEFAULT_PASSWORD = "admin123"

# 随机口令落盘位置。相对当前工作目录：打包版的 cwd 是 exe 所在目录，
# 源码版是仓库根目录，两者都能落到使用者看得见的地方。
_PASSWORD_FILE = Path("data") / "admin-initial-password.txt"


def _is_production() -> bool:
    return settings.app_env.lower() in {"production", "prod"}


def _resolve_initial_password() -> tuple:
    """决定初始管理员口令，返回 ``(口令, 来源说明)``。

    **调用方不得把口令写进常规日志。** 唯一的例外是「连文件都写不进去」——
    那种情况下不打印就等于永久锁死系统，两害相权只能打印一次并明确告警。
    """
    configured = (settings.admin_initial_password or "").strip()
    if configured:
        return configured, "ADMIN_INITIAL_PASSWORD 环境变量"

    if _is_production():
        password = secrets.token_urlsafe(18)
        try:
            _PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PASSWORD_FILE.write_text(
                "初始管理员账号：admin\n"
                f"初始口令：{password}\n"
                "\n首次登录后请立即修改口令，并删除本文件。\n",
                encoding="utf-8",
            )
            return password, str(_PASSWORD_FILE)
        except OSError as e:
            logger.error("初始口令文件写入失败（%s），改为在本次启动日志中输出一次", e)
            return password, "本次启动日志"

    return _DEV_DEFAULT_PASSWORD, "开发环境默认口令"


async def init_default_admin(db: AsyncSession):
    """首次启动时创建 admin 账号；已存在则什么都不做。"""
    stmt = select(User).where(User.username == "admin")
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        return

    password, source = _resolve_initial_password()

    admin = User(
        username="admin",
        password=hash_password(password),
        role="ADMIN",
        email="admin@recruitment.com",
    )
    db.add(admin)
    await db.commit()

    if source == "本次启动日志":
        # 兜底路径：文件写不进去，不打印就永远进不去系统
        logger.warning(
            "已创建管理员 admin，初始口令：%s —— 请立即登录并修改，本条仅输出一次", password
        )
    else:
        logger.warning(
            "已创建管理员 admin（初始口令来源：%s，明文不在日志中输出）", source
        )
