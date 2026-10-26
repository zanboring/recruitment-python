"""操作日志装饰器：把关键写操作落库到 sys_log 表。

设计要点（面试可讲）：
- **只记「谁、做了什么、成功与否」**：参数做白名单化序列化，
  标量保留、对象只留类型名。避免把 AsyncSession / Request 这类对象
  str() 后写进库（既无意义又可能抛异常）。
- **敏感字段脱敏**：参数名含 password / pwd / token / secret 时统一写 ***。
- **日志失败绝不影响业务**：整体 try/except 吞掉并记 warning。
- **复用业务 session**：本项目的写操作 endpoint 都自行 commit，
  装饰器在 endpoint 返回前追加一条 INSERT + commit，不破坏业务事务；
  若业务抛异常导致 session 脏状态，写入失败也只记 warning。

用法（注意顺序：log_action 放在 @router 之后、async def 之前）：

    @router.post("/")
    @log_action("新增岗位")
    async def create_job(request: JobCreateRequest, db=Depends(get_db), user=Depends(require_admin)):
        ...
"""
import functools
import json
import logging

from app.models.sys_log import SysLog

logger = logging.getLogger(__name__)

# 可安全写入 params 字段的标量类型
_SCALAR_TYPES = (str, int, float, bool)

# 单条日志 params 的最大长度，防止超长入参撑爆字段
_MAX_PARAMS_LEN = 2000

# 参数名命中这些片段时脱敏，避免密码 / Token 落到日志表
_SENSITIVE_HINTS = ("password", "passwd", "pwd", "token", "secret", "api_key")

_MASK = "***"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


def _safe_params(kwargs: dict) -> str:
    """把关键字参数序列化成可读且安全的 JSON 字符串。"""
    data = {}
    for key, value in kwargs.items():
        if _is_sensitive(key):
            # 只标记「传了」而不记录内容
            data[key] = _MASK if value is not None else None
        elif value is None or isinstance(value, _SCALAR_TYPES):
            data[key] = value
        elif isinstance(value, (list, tuple, set)):
            data[key] = f"<{type(value).__name__} len={len(value)}>"
        else:
            # AsyncSession / Starlette Request / Pydantic 请求模型等都归到这里
            data[key] = f"<{type(value).__name__}>"
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text[:_MAX_PARAMS_LEN]


def _extract_username(kwargs: dict) -> str:
    """尽力解析操作者用户名。

    优先级：current_user / user（已认证依赖注入的 User 实例）
            > request 里的 username 字段（登录、注册等未认证接口）。
    """
    for key in ("current_user", "user"):
        username = getattr(kwargs.get(key), "username", None)
        if username:
            return str(username)

    username = getattr(kwargs.get("request"), "username", None)
    if username:
        return str(username)

    return "anonymous"


def _extract_request_info(kwargs: dict) -> tuple:
    """解析请求来源，返回 (uri, ip)。

    优先读 RequestContextMiddleware 写入的 contextvars —— 因为本项目的 endpoint
    普遍把请求体参数命名为 `request`，装饰器拿不到 Starlette 的 Request 对象。
    仅在没有中间件上下文时（直接单测调用 endpoint 函数）才回退到 kwargs。
    """
    from app.middleware.request_context import get_request_context

    ctx = get_request_context()
    if ctx:
        uri = ctx.get("path", "")
        query = ctx.get("query", "")
        if query:
            uri = f"{uri}?{query}"
        return uri, ctx.get("client_ip", "")

    request = kwargs.get("request")
    if request is None:
        return "", ""

    client = getattr(request, "client", None)
    ip = getattr(client, "host", "") if client is not None else ""
    url = getattr(request, "url", "")
    return (str(url) if url else "", str(ip or ""))


def log_action(action: str):
    """记录一次操作日志。用法：@log_action("新增岗位")"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            success = True
            error_msg = None
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                success = False
                # 业务异常的文案本身就足够定位问题，截断后入库
                error_msg = str(e)[:1000]
                raise
            finally:
                db = kwargs.get("db")
                if db is not None:
                    try:
                        uri, ip = _extract_request_info(kwargs)
                        db.add(
                            SysLog(
                                username=_extract_username(kwargs),
                                action=action,
                                method=f"{func.__module__}.{func.__name__}",
                                uri=uri[:255],
                                ip=ip[:50],
                                params=_safe_params(kwargs),
                                success=1 if success else 0,
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                    except Exception as e:  # noqa: BLE001 - 日志失败不能影响业务
                        logger.warning("写入操作日志失败（不影响业务）：%s", e)

        return wrapper

    return decorator
