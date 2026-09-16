"""招聘截图视觉识别：上传图片 → GLM-4V 多模态识别 → 结构化岗位信息。

**为什么用视觉大模型而不是传统 OCR**：
- 招聘截图（BOSS 详情页 / 海报 / 手机相册）是真实求职场景最常见的
  信息载体，用户看到合适岗位截图一张就能入库，不依赖任何爬虫；
- GLM-4V-Flash 免费、支持 base64 本地图，能跳过 OCR 引擎的本地安装
  与中文排版对齐问题，直接把「图 → 字段」一步到位；
- 语义理解比纯 OCR 更强：能识别「20-30K」「K」单位换算、技能标签、
  学历经验区间，甚至补全截图里缺失的字段含义。

这是系统的第三条真实数据通路：爬虫（可选）→ CSV 批量 → 视觉识别。
"""
import base64
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# GLM-4V-Flash：智谱免费视觉模型（支持 base64 本地图，单图）
VISION_MODEL = "glm-4v-flash"
VISION_TIMEOUT = 30

EXTRACT_PROMPT = (
    "你是招聘信息提取助手。请识别图片中的招聘岗位信息，严格输出 JSON（不要 Markdown 代码块包装）。"
    "字段："
    "  title: 岗位名称，如「Python后端开发工程师」；"
    "  company_name: 公司名称；"
    "  city: 工作城市；"
    "  salary_min_k: 最低薪资（单位 K，整数，如 20 表示 20K，无法识别为 0）；"
    "  salary_max_k: 最高薪资（单位 K，整数）；"
    "  experience: 经验要求原文，如「3-5年」「经验不限」；"
    "  education: 学历要求，如「本科」「硕士」；"
    "  skills: 技能要点，逗号分隔（如 Python, LLM, RAG）；"
    "  description: 岗位职责/要求的原文摘要（200 字以内）。"
    "无法识别或图片中没有的字段留空字符串或 null。只输出 JSON 对象本身。"
)


def _encode_image(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """图片字节 → data URL（GLM-4V 支持的本地图格式）。"""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _parse_json_reply(reply: str) -> dict:
    """容错解析模型输出：剥离 Markdown 代码块与前后文本，再取 JSON。"""
    text = (reply or "").strip()
    if not text:
        raise ValueError("模型返回为空")
    if "```" in text:
        import re

        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
        if blocks:
            text = blocks[0].strip()
        else:
            text = text.replace("```json", "").replace("```", "").strip()
    # 兜底：直接找第一个 { 到最后 }
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"模型输出非合法 JSON：{text[:200]}") from e
    if not isinstance(data, dict):
        raise ValueError("模型输出非对象")
    return data


def _to_int(v) -> int:
    """"20" / 20 / "20K" / null → 20 / 0；容忍单位后缀。"""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    text = str(v).strip().lower().replace("k", "").replace("k", "").strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _normalize_job(data: dict) -> dict:
    """把模型输出规整为 create_job 可用的字段（含薪资 K → 元）。"""
    k_min = _to_int(data.get("salary_min_k"))
    k_max = _to_int(data.get("salary_max_k"))
    if k_max <= 0:
        k_max = k_min
    return {
        "title": str(data.get("title") or "").strip(),
        "company_name": str(data.get("company_name") or "").strip(),
        "city": str(data.get("city") or "").strip(),
        "min_salary": k_min * 1000,
        "max_salary": k_max * 1000,
        "experience": str(data.get("experience") or "").strip(),
        "education": str(data.get("education") or "").strip(),
        "skills": str(data.get("skills") or "").strip(),
        "job_desc": str(data.get("description") or "").strip()[:2000],
        "source_site": "vision",
    }


async def recognize_job_image(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    """上传图片 → 视觉识别 → 规范化岗位字典。

    需要 ZHIPUAI_API_KEY（视觉模型走智谱）；未配置时抛 RuntimeError，
    由路由层转成带安装提示的业务错误。
    """
    if not settings.zhipuai_api_key:
        raise RuntimeError(
            "未配置 ZHIPUAI_API_KEY：视觉识别依赖智谱 GLM-4V-Free 模型（免费），"
            "请在 .env 设置 ZHIPUAI_API_KEY 后重试"
        )

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACT_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": _encode_image(image_bytes, mime)},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.zhipuai_api_key}",
    }

    async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
        resp = await client.post(
            settings.zhipuai_api_url, headers=headers, json=payload
        )
        if resp.status_code == 401:
            raise RuntimeError("智谱 API Key 无效或已过期，请检查 ZHIPUAI_API_KEY")
        resp.raise_for_status()
        body = resp.json()

    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    parsed = _parse_json_reply(content)
    return _normalize_job(parsed)


async def recognize_job_images_batch(images: list) -> list:
    """批量视觉识别：一次传多张图片，逐张识别，异常隔离。

    ``images`` 为 [{index, bytes, mime}]。返回同长度列表，每项:
        {"index": i, "ok": bool, "result": dict|None, "error": str|None}
    单张失败不影响其余（识别是独立网络调用，不应一张坏图拖垮整批）；
    全部图片为空/超限等整体性错误直接抛 RuntimeError 由路由层处理。
    """
    if not images:
        raise RuntimeError("未收到任何图片")
    results = []
    for item in images:
        index = item.get("index", 0)
        image_bytes = item.get("bytes") or b""
        mime = item.get("mime") or "image/jpeg"
        try:
            if not image_bytes:
                raise RuntimeError("图片内容为空")
            job_data = await recognize_job_image(image_bytes, mime=mime)
            if not job_data.get("title"):
                raise RuntimeError("未识别到岗位信息，请换更清晰的截图")
            results.append({"index": index, "ok": True, "result": job_data, "error": None})
        except RuntimeError as e:
            results.append({"index": index, "ok": False, "result": None, "error": str(e)})
        except Exception as e:  # noqa: BLE001 - 单张异常隔离
            logger.warning("批量识别第 %s 张失败：%s", index, e)
            results.append({"index": index, "ok": False, "result": None, "error": "图片识别失败，请重试"})
    return results
