import functools
import json
import logging
from app.models.sys_log import SysLog

logger = logging.getLogger(__name__)


def log_action(action: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            db = kwargs.get("db")
            user = kwargs.get("user") or kwargs.get("current_user")
            request = kwargs.get("request")

            success = True
            error_msg = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                if db:
                    try:
                        log_entry = SysLog(
                            username=user.username if user else "anonymous",
                            action=action,
                            method=f"{func.__module__}.{func.__name__}",
                            uri=str(getattr(request, "url", "")) if request else "",
                            ip=getattr(request, "client", "").host if hasattr(getattr(request, "client", ""), "host") else "",
                            params=json.dumps({k: str(v) for k, v in kwargs.items()}, ensure_ascii=False)[:2000],
                            success=1 if success else 0,
                            error_msg=error_msg,
                        )
                        db.add(log_entry)
                        await db.commit()
                    except Exception as e:
                        logger.error(f"Failed to write log: {e}")
        return wrapper
    return decorator