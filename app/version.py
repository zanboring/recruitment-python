"""应用版本与构建元信息的**唯一来源**。

为什么要单独一个文件：
版本号原先只以字面量形式出现在 ``app/main.py`` 的 ``FastAPI(version="1.0.0")``，
而打包脚本、CHANGELOG、更新检查、前端「关于」页都需要它。多份副本必然漂移 ——
典型症状是 OpenAPI 文档里写着 1.0.0、exe 里显示的却还是上一版。
现在收敛到这里一处，其余位置一律引用。

**版本号纪律**（语义化版本 MAJOR.MINOR.PATCH）：

- ``MAJOR``：不兼容变更（接口契约变化，或需要手工 ``ALTER TABLE`` 的库结构变更）
- ``MINOR``：向后兼容的新功能
- ``PATCH``：向后兼容的缺陷修复

发版时 **GitHub Release 的 tag 必须与 ``APP_VERSION`` 一致**（``v1.0.0``），
更新检查就是靠比对两者判断有没有新版 —— 对不上会导致「明明发了新版却提示已是最新」。
"""
from __future__ import annotations

import platform
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

APP_NAME = "RecSys"
APP_VERSION = "1.0.0"

# 版本号形如 1.2.3，可选带 -beta.1 这类预发布后缀
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$")


def parse_version(text: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """把 ``v1.2.3`` / ``1.2.3`` / ``1.2.3-beta.1`` 解析成 ``(1, 2, 3)``。

    解析不了返回 ``None``（而不是抛异常）：调用方是「检查更新」这类
    附加功能，遇到脏 tag 应该退化到「无法比较」，不该把接口打成 500。
    """
    if not text:
        return None
    match = _VERSION_RE.match(str(text).strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(candidate: Optional[str], current: str = APP_VERSION) -> bool:
    """``candidate`` 是否比 ``current`` 新。任一方解析不了都返回 ``False``。

    刻意不用字符串比较：``"1.10.0" < "1.9.0"`` 在字典序下成立，会漏掉新版。
    """
    left = parse_version(candidate)
    right = parse_version(current)
    if left is None or right is None:
        return False
    return left > right


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 里。

    用于区分「本地调试版」与「通用版」—— 两者的更新方式不同
    （前者 ``git pull``，后者下载新的 Release 包）。
    """
    return bool(getattr(sys, "frozen", False))


def build_info() -> dict:
    """返回可直接 JSON 序列化的构建信息。

    ``distribution`` 让前端能明确告诉用户「你用的是绿色版还是源码版」，
    这在排障时很关键：同样是「功能没生效」，两者的排查路径完全不同。
    """
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "distribution": "frozen-exe" if is_frozen() else "source",
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }
