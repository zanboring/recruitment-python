import json
import asyncio
import logging
from typing import AsyncGenerator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000
conversation_history: dict[str, list] = {}
_history_lock = asyncio.Lock()

# 进程内取消标记：记录被用户主动取消的 session_id，
# SSE 生成器在每次产出 chunk 前检查，命中则提前终止。
_cancelled_sessions: set = set()


def request_cancel(session_id: str):
    """标记某个会话的流式请求需要取消。"""
    _cancelled_sessions.add(session_id)


def is_cancelled(session_id: str) -> bool:
    return session_id in _cancelled_sessions


def clear_cancel(session_id: str):
    _cancelled_sessions.discard(session_id)


async def _get_conversation_history(session_id: str) -> list:
    async with _history_lock:
        return conversation_history.get(session_id, [])


async def _update_conversation_history(session_id: str, user_message: str, ai_response: str):
    async with _history_lock:
        if session_id not in conversation_history:
            if len(conversation_history) >= MAX_SESSIONS:
                oldest = next(iter(conversation_history.keys()))
                conversation_history.pop(oldest, None)
            conversation_history[session_id] = []

        conversation_history[session_id].append({"role": "user", "content": user_message})
        conversation_history[session_id].append({"role": "assistant", "content": ai_response})

        if len(conversation_history[session_id]) > 20:
            conversation_history[session_id] = conversation_history[session_id][-20:]


async def call_glm4_stream(message: str, session_id: str = "", db=None) -> AsyncGenerator[str, None]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.zhipuai_api_key}"
    }

    history = await _get_conversation_history(session_id)

    context = ""
    if db:
        from app.services.knowledge_service import KnowledgeService
        context = await KnowledgeService.get_context_for_ai(db, message)

    full_message = context + message if context else message
    messages = history + [{"role": "user", "content": full_message}]

    payload = {
        "model": "glm-4-flash",
        "messages": messages,
        "stream": True,
        "temperature": 0.7
    }

    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("POST", settings.zhipuai_api_url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue


async def call_ollama_stream(message: str, session_id: str = "", db=None) -> AsyncGenerator[str, None]:
    history = await _get_conversation_history(session_id)

    context = ""
    if db:
        from app.services.knowledge_service import KnowledgeService
        context = await KnowledgeService.get_context_for_ai(db, message)

    full_message = context + message if context else message
    messages = history + [{"role": "user", "content": full_message}]

    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": True
    }

    url = f"{settings.ollama_base_url}/api/chat"

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue


async def call_chat_stream(message: str, session_id: str = "", db=None) -> AsyncGenerator[str, None]:
    response_content = ""

    if settings.zhipuai_api_key:
        try:
            async for chunk in call_glm4_stream(message, session_id, db):
                response_content += chunk
                yield chunk
            if db and response_content:
                from app.services.knowledge_service import KnowledgeService
                await KnowledgeService.learn_from_response(db, message, response_content)
            return
        except Exception as e:
            logger.warning(f"GLM-4 failed: {e}")

    if settings.ollama_enabled:
        try:
            async for chunk in call_ollama_stream(message, session_id, db):
                response_content += chunk
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Ollama failed: {e}")

    from app.services.local_model_service import LocalModelService
    response = await LocalModelService.chat(message, db)
    yield response


async def update_conversation_history(session_id: str, user_message: str, ai_response: str):
    await _update_conversation_history(session_id, user_message, ai_response)


async def generate_full_response(message: str, session_id: str = "") -> str:
    full_response = ""
    async for chunk in call_chat_stream(message, session_id):
        full_response += chunk
    return full_response


def _build_analysis_prompt(stats: dict) -> str:
    """把聚合统计数据拼接成给 LLM 的分析 prompt。"""
    summary = stats.get("summary", {})
    lines = [
        "你是招聘市场数据分析师。请基于以下统计数据，输出一份简洁的招聘市场分析报告（150-300字），",
        "包含：总体概况、薪资水平、热门城市、热门技能、学历与经验要求趋势，以及给求职者的建议。",
        "",
        "统计数据：",
        f"- 岗位总数：{summary.get('total', 0)}，在招岗位：{summary.get('active', 0)}，平均薪资：{summary.get('avg_salary', 0)}元/月",
    ]

    city = stats.get("city", [])
    if city:
        city_str = "、".join([f"{c['name']}({c['count']})" for c in city[:5]])
        lines.append(f"- 热门城市：{city_str}")

    skill = stats.get("skill", [])
    if skill:
        skill_str = "、".join([f"{s['name']}({s['count']})" for s in skill[:5]])
        lines.append(f"- 热门技能：{skill_str}")

    salary_range = stats.get("salary_range", [])
    if salary_range:
        sal_str = "、".join([f"{s['name']}({s['count']})" for s in salary_range])
        lines.append(f"- 薪资分布：{sal_str}")

    education = stats.get("education", [])
    if education:
        edu_str = "、".join([f"{e['name']}({e['count']})" for e in education[:5]])
        lines.append(f"- 学历要求：{edu_str}")

    experience = stats.get("experience", [])
    if experience:
        exp_str = "、".join([f"{e['name']}({e['count']})" for e in experience[:5]])
        lines.append(f"- 经验要求：{exp_str}")

    lines.append("")
    lines.append("请直接输出报告正文，不要使用标题或列表符号之外的复杂格式。")
    return "\n".join(lines)


async def generate_analysis_report(stats: dict, db=None):
    """规则聚合 + LLM 增强分析报告。

    返回 (report, used_model)：
      - report     LLM 生成的分析报告正文；LLM 不可用时为 None
      - used_model 实际使用的模型：primary(GLM-4) / local(Ollama) / none(不可用)

    当 GLM-4 与 Ollama 均不可用时返回 (None, "none")，由调用方回退到
    规则引擎生成的纯统计摘要，诚实不夸大「LLM 增强」能力。
    """
    prompt = _build_analysis_prompt(stats)

    if settings.zhipuai_api_key:
        try:
            report = ""
            async for chunk in call_glm4_stream(prompt, "", db):
                report += chunk
            if report.strip():
                return report, "primary"
        except Exception as e:
            logger.warning(f"分析报告 GLM-4 失败：{e}")

    if settings.ollama_enabled:
        try:
            report = ""
            async for chunk in call_ollama_stream(prompt, "", db):
                report += chunk
            if report.strip():
                return report, "local"
        except Exception as e:
            logger.warning(f"分析报告 Ollama 失败：{e}")

    return None, "none"