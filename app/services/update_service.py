"""检查 GitHub Release 上是否有新版本。

三条原则：

1. **绝不抛出** —— 检查更新是附加功能。网络不可达、被限流、仓库不存在、
   返回体结构变了，都只返回一个「状态 + 原因」，不影响任何业务接口，
   也不会让前端因为「检查更新失败」而弹一个红色错误。
2. **不做启动时自动联网** —— 只在接口被显式调用时才发请求。绿色版可能跑在
   内网或离网机器上，启动即联网会带来无谓的等待与失败日志；
   由用户点「检查更新」更可控、也更好解释。
3. **不用字符串比版本** —— 见 ``app/version.py`` 的 ``is_newer``：
   字典序下 ``"1.10.0" < "1.9.0"``，会漏掉新版。
"""
import logging
from typing import Optional

import httpx

from app.config import settings
from app.version import APP_NAME, APP_VERSION, is_newer

logger = logging.getLogger(__name__)

GITHUB_LATEST_RELEASE = "https://api.github.com/repos/{repo}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 10

# GitHub API 要求带 User-Agent（不带会直接 403）。
# 这里显式声明，并带上版本号，便于在 GitHub 侧辨认请求来源。
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# 版本说明可能很长，截断后再返回，避免把整个响应体塞进前端
MAX_NOTES_CHARS = 4000


def is_enabled() -> bool:
    return bool(settings.update_check_enabled)


def normalize_repo(raw: Optional[str]) -> str:
    """把各种写法的仓库标识归一化成 ``owner/name``。

    容忍用户直接粘贴完整 URL 或带 ``.git`` 后缀 —— 配置项写错只会得到
    一个含糊的「仓库不存在」，不如在这里先纠正掉。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.endswith(".git"):
        value = value[: -len(".git")]
    value = value.strip("/")
    return value if value.count("/") == 1 else ""


def _result(status: str, **extra) -> dict:
    base = {"status": status, "current_version": APP_VERSION}
    base.update(extra)
    return base


async def check_for_update() -> dict:
    """查询最新 Release 并与当前版本比较。**不抛异常**。

    ``status`` 取值与含义：

    - ``disabled``      开关关闭（``UPDATE_CHECK_ENABLED=false``）
    - ``unconfigured``  未配置或配置非法（``UPDATE_REPO`` 不是 ``owner/name``）
    - ``no_release``    仓库可达但还没有发布任何 Release（不算错误）
    - ``error``         网络失败 / 非 200 / 响应不是 JSON
    - ``ok``            正常拿到最新版本，``has_update`` 表示是否有新版
    """
    if not is_enabled():
        return _result("disabled", detail="未开启更新检查（UPDATE_CHECK_ENABLED=false）")

    repo = normalize_repo(settings.update_repo)
    if not repo:
        return _result(
            "unconfigured",
            detail=f"UPDATE_REPO 需为 owner/name 形式，当前为：{settings.update_repo!r}",
        )

    url = GITHUB_LATEST_RELEASE.format(repo=repo)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    except Exception as exc:  # noqa: BLE001 - 见模块文档第 1 条
        logger.warning("检查更新失败（请求异常）：%s", exc)
        return _result("error", detail=f"请求失败：{exc}", repo=repo)

    if response.status_code == 404:
        # 仓库存在但没有 Release 时 GitHub 也返回 404，这属于正常状态而非故障
        return _result("no_release", detail="该仓库还没有发布任何 Release", repo=repo)
    if response.status_code == 403:
        return _result(
            "error",
            detail="GitHub 拒绝了请求（通常是未认证的调用频率超限，稍后再试）",
            repo=repo,
        )
    if response.status_code != 200:
        return _result("error", detail=f"HTTP {response.status_code}", repo=repo)

    try:
        payload = response.json()
    except ValueError:
        return _result("error", detail="响应不是合法 JSON", repo=repo)

    if not isinstance(payload, dict):
        return _result("error", detail="响应结构不符预期", repo=repo)

    tag = payload.get("tag_name")
    assets = payload.get("assets") or []
    asset_name = ""
    if isinstance(assets, list) and assets:
        # 优先挑发布包（zip），没有就退回第一个附件
        zip_assets = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".zip")]
        picked = (zip_assets or [a for a in assets if isinstance(a, dict)])[0]
        asset_name = str(picked.get("name", ""))

    notes = payload.get("body") or ""
    has_update = is_newer(tag, APP_VERSION)

    return _result(
        "ok",
        repo=repo,
        latest_version=str(tag or "").lstrip("v") or None,
        latest_tag=tag,
        has_update=has_update,
        release_url=payload.get("html_url"),
        published_at=payload.get("published_at"),
        asset_name=asset_name,
        notes=str(notes)[:MAX_NOTES_CHARS],
        detail="发现新版本，建议更新" if has_update else "当前已是最新版本",
    )
