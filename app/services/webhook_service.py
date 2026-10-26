"""日报推送：企业微信 / 钉钉 / 通用 JSON Webhook。

三条原则：

1. **默认关闭** —— 未开启或未配置 URL 时安静跳过，不产生任何外部请求。
   本地演示环境不该因为「忘了配 webhook」而在日志里刷错误。
2. **绝不抛出** —— 推送是锦上添花。它失败不能让日报生成失败，
   失败通过返回值与日志留痕，而不是异常。
3. **识别业务层错误码** —— 企业微信与钉钉都用 HTTP 200 + ``errcode != 0``
   表达失败（token 失效、内容被风控等）。只看 HTTP 状态码会把这类失败
   记成「推送成功」，是最容易骗过监控的一种假成功。
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PUSH_TIMEOUT_SECONDS = 10

WECOM = "wecom"
DINGTALK = "dingtalk"
GENERIC = "generic"
SUPPORTED_TYPES = (WECOM, DINGTALK, GENERIC)

# 各类机器人的中文名，用于把结果写成用户看得懂的话
_TYPE_LABELS = {
    WECOM: "企业微信机器人",
    DINGTALK: "钉钉机器人",
    GENERIC: "通用 Webhook",
}


@dataclass
class PushResult:
    """一次推送的结果。``attempted=False`` 表示压根没发请求。"""

    attempted: bool = False
    success: bool = False
    target: str = ""
    detail: str = ""

    def describe(self) -> str:
        if not self.attempted:
            return f"未推送（{self.detail}）"
        state = "成功" if self.success else "失败"
        return f"推送{state}（{self.target}）：{self.detail}"


def normalize_type(raw: Optional[str]) -> Optional[str]:
    """归一化 webhook 类型；未知类型返回 ``None``。

    刻意不静默回退成某个默认类型：把 ``report_webhook_type`` 写成 ``wechat``
    这类笔误时，按通用格式发出去的报文会被机器人拒收，而日志只会显示
    「对方返回格式错误」，很难联想到是自己配置写错了。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return WECOM
    if value in SUPPORTED_TYPES:
        return value
    if value in ("wx", "wework", "qywx", "企业微信"):
        return WECOM
    if value in ("dingding", "dd", "钉钉"):
        return DINGTALK
    return None


def is_configured() -> bool:
    """是否已具备推送条件（开关打开 + URL 已填 + 类型合法）。"""
    return bool(
        settings.report_webhook_enabled
        and str(settings.report_webhook_url or "").strip()
        and normalize_type(settings.report_webhook_type)
    )


def build_payload(kind: str, title: str, content: str) -> dict:
    """按机器人类型构造报文。

    企业微信与钉钉都是 markdown 消息体，区别在于钉钉多一个 ``title``
    且正文字段叫 ``text``（钉钉客户端在会话列表里展示 title，
    不传会显示成空白标题）。
    """
    if kind == WECOM:
        return {"msgtype": "markdown", "markdown": {"content": content}}
    if kind == DINGTALK:
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }
    return {"title": title, "content": content}


def _extract_errcode(response: httpx.Response):
    """取出机器人返回的业务错误码；不是 JSON 或没有该字段时返回 ``None``。"""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for key in ("errcode", "errorCode", "code"):
        if key in body:
            try:
                return int(body[key])
            except (TypeError, ValueError):
                return None
    return None


async def push(title: str, content: str) -> PushResult:
    """推送一条 markdown 消息。**不抛异常**，失败信息在返回值里。"""
    if not settings.report_webhook_enabled:
        return PushResult(detail="未开启 report_webhook_enabled")

    url = str(settings.report_webhook_url or "").strip()
    if not url:
        return PushResult(detail="未配置 report_webhook_url")

    kind = normalize_type(settings.report_webhook_type)
    if kind is None:
        return PushResult(
            detail=f"未知的 report_webhook_type：{settings.report_webhook_type}"
                   f"（支持 {', '.join(SUPPORTED_TYPES)}）"
        )
    if not url.startswith(("http://", "https://")):
        return PushResult(detail="report_webhook_url 必须以 http:// 或 https:// 开头")

    label = _TYPE_LABELS.get(kind, kind)
    payload = build_payload(kind, title, content)

    try:
        async with httpx.AsyncClient(timeout=PUSH_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001 - 推送失败不得影响日报主流程
        logger.warning("日报推送请求失败（%s）：%s", label, exc)
        return PushResult(attempted=True, success=False, target=label, detail=f"请求异常：{exc}")

    if response.status_code != 200:
        logger.warning("日报推送返回非 200（%s）：%s", label, response.status_code)
        return PushResult(
            attempted=True, success=False, target=label,
            detail=f"HTTP {response.status_code}",
        )

    errcode = _extract_errcode(response)
    if errcode not in (None, 0):
        # HTTP 200 但业务失败 —— 必须按失败处理，否则 token 失效会一直是「成功」
        message = ""
        try:
            message = str(response.json().get("errmsg", ""))
        except ValueError:
            pass
        logger.warning("日报推送被拒（%s）：errcode=%s %s", label, errcode, message)
        return PushResult(
            attempted=True, success=False, target=label,
            detail=f"errcode={errcode} {message}".strip(),
        )

    logger.info("日报推送成功（%s）", label)
    return PushResult(attempted=True, success=True, target=label, detail="ok")
