import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator

from app.config import settings
from app.services.tool_service import should_detect_tool

logger = logging.getLogger(__name__)

MAX_SESSIONS = 1000
MAX_HISTORY_TURNS = 20
SESSION_TTL_SECONDS = 3600

# 会话历史容器。
# key 不是用户传入的原始 session_id，而是经过 _session_key() 计算后的
# 「用户维度隔离键」，格式为 "u{user_id}:{session_id}"。
# 这样即使两个不同用户传入同一个 session_id，也不会互相看到对方的历史；
# 未登录/未携带 user_id 的内部调用会退化为 "anonymous:{session_id}"。
conversation_history: dict[str, list] = {}
_session_touched_at: dict[str, float] = {}
_history_lock = asyncio.Lock()

# 进程内取消标记：记录被用户主动取消的会话。
# 同样按「用户 + session」维度隔离，避免 A 用户取消掉 B 用户的流式请求。
_cancelled_sessions: set = set()


def _session_key(session_id: str, user_id=None) -> str:
    """把 session_id 与 user_id 组合成进程内唯一的会话键。

    user_id 为空（内部调用 / 未登录场景）时退化为 anonymous 前缀，
    保持向后兼容，但不会与已登录用户的会话冲突。
    """
    sid = session_id or "default"
    uid = "anonymous" if user_id in (None, "") else str(user_id)
    return f"u{uid}:{sid}"


def request_cancel(session_id: str, user_id=None):
    """标记某个会话的流式请求需要取消。"""
    _cancelled_sessions.add(_session_key(session_id, user_id))


def is_cancelled(session_id: str, user_id=None) -> bool:
    return _session_key(session_id, user_id) in _cancelled_sessions


def clear_cancel(session_id: str, user_id=None):
    _cancelled_sessions.discard(_session_key(session_id, user_id))


def clear_session(session_id: str, user_id=None):
    """清空单个会话的历史与取消标记（用户主动「新建会话」时使用）。"""
    key = _session_key(session_id, user_id)
    conversation_history.pop(key, None)
    _session_touched_at.pop(key, None)
    _cancelled_sessions.discard(key)


def reset_runtime_state():
    """清空全部进程内状态，仅供测试隔离使用。"""
    conversation_history.clear()
    _session_touched_at.clear()
    _cancelled_sessions.clear()


def _evict_if_needed(now: float):
    """容量控制：先清理过期会话，仍超限则淘汰最久未使用的会话。"""
    for key in [k for k, ts in _session_touched_at.items() if now - ts > SESSION_TTL_SECONDS]:
        conversation_history.pop(key, None)
        _session_touched_at.pop(key, None)

    while len(conversation_history) >= MAX_SESSIONS:
        oldest = min(_session_touched_at, key=_session_touched_at.get)
        conversation_history.pop(oldest, None)
        _session_touched_at.pop(oldest, None)
        _cancelled_sessions.discard(oldest)


async def _get_conversation_history(session_id: str, user_id=None) -> list:
    key = _session_key(session_id, user_id)
    async with _history_lock:
        now = time.monotonic()
        records = conversation_history.get(key)
        if records is None:
            return []
        if now - _session_touched_at.get(key, now) > SESSION_TTL_SECONDS:
            conversation_history.pop(key, None)
            _session_touched_at.pop(key, None)
            return []
        return records


async def _update_conversation_history(session_id: str, user_message: str, ai_response: str, user_id=None):
    key = _session_key(session_id, user_id)
    async with _history_lock:
        now = time.monotonic()
        if key not in conversation_history:
            _evict_if_needed(now)
            conversation_history[key] = []

        conversation_history[key].append({"role": "user", "content": user_message})
        conversation_history[key].append({"role": "assistant", "content": ai_response})
        _session_touched_at[key] = now

        if len(conversation_history[key]) > MAX_HISTORY_TURNS:
            conversation_history[key] = conversation_history[key][-MAX_HISTORY_TURNS:]


def _emit_meta(on_meta, **payload) -> None:
    """通过 ``on_meta`` 回调透出「这一轮用了谁、花了多少、耗了多久」。

    回调失败一律吞掉：把观测量抛给调用方，就绝不该反过来影响被观测的请求。
    """
    if not on_meta:
        return
    try:
        on_meta(payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("模型元信息回调失败（已忽略）：%s", e)


async def _build_context(db, message: str, on_sources=None) -> str:
    """检索知识库并返回注入用上下文；有命中时通过 ``on_sources`` 回调透出引用来源。

    把「检索 + 来源回调」收敛到一处，避免云端 / 本地 / 规则引擎三条路径各写一遍
    （三处都做同一件事时，最容易出现的行为不一致就是有人忘了回调来源）。
    """
    if not db:
        return ""
    try:
        from app.services.knowledge_service import KnowledgeService

        context, sources = await KnowledgeService.get_context_with_sources(db, message)
    except Exception as e:
        logger.warning("知识库检索失败，本次不带 RAG 上下文：%s", e)
        return ""

    if sources and on_sources:
        try:
            on_sources(sources)
        except Exception as e:  # noqa: BLE001 - 回调失败不影响对话
            logger.warning("引用来源回调失败（已忽略）：%s", e)
    return context


def _usage_sink() -> tuple:
    """构造用量收集器：(容器, 回调)。

    云端（``llm_client``）与本地（``ollama_client``）上报的字段名刻意保持一致
    （prompt_tokens / completion_tokens / total_tokens），因此两条链路可以共用
    同一个收集器，无需各自分支。
    """
    box: dict = {}

    def sink(payload: dict) -> None:
        box.update(payload or {})

    return box, sink


async def _record_usage(
    scene: str,
    *,
    provider: str = "",
    model: str = "",
    tier: str = "cloud",
    usage: dict = None,
    latency_ms: int = 0,
    success: bool = True,
    error_msg: str = "",
    user_id=None,
) -> None:
    """记录一次 AI 调用的用量（统一入口，内部已吞掉异常）。"""
    from app.services import usage_service

    payload = usage or {}
    await usage_service.record(
        scene=scene,
        provider=provider,
        model=model,
        tier=tier,
        prompt_tokens=payload.get("prompt_tokens", 0) or 0,
        completion_tokens=payload.get("completion_tokens", 0) or 0,
        latency_ms=latency_ms,
        success=success,
        error_msg=error_msg,
        user_id=user_id,
    )


async def call_cloud_stream(
    provider,
    message: str,
    session_id: str = "",
    db=None,
    user_id=None,
    on_usage=None,
    max_tokens: int = None,
    on_sources=None,
) -> AsyncGenerator[str, None]:
    """调用指定云端服务商的流式对话（OpenAI 兼容协议，DeepSeek / 智谱通用）。

    流程：取会话历史 → 拼接知识库上下文（RAG）→ 交给 llm_client 发起请求。
    """
    history = await _get_conversation_history(session_id, user_id)

    context = await _build_context(db, message, on_sources)

    full_message = context + message if context else message
    messages = history + [{"role": "user", "content": full_message}]

    from app.services.llm_client import stream_chat

    async for piece in stream_chat(
        provider,
        messages,
        temperature=0.7,
        on_usage=on_usage,
        max_tokens=max_tokens or settings.ai_max_output_tokens,
    ):
        yield piece


async def _call_cloud_sync(provider, messages: list, on_usage=None, max_tokens: int = None) -> str:
    """非流式调用云端模型，返回完整回复文本。

    用低温度（0.1）保证「工具识别」阶段输出的 JSON 稳定可解析；
    输出上限收紧到 128 —— 这一阶段只需一行 JSON 或 NONE。
    """
    from app.services.llm_client import complete_chat

    return await complete_chat(
        provider,
        messages,
        temperature=0.1,
        on_usage=on_usage,
        max_tokens=max_tokens or settings.ai_tool_detect_max_tokens,
    )


async def _detect_tool_call(provider, message: str, user_id=None) -> dict | None:
    """判断用户消息是否需要调用岗位查询工具。

    用低温度 + 强约束的 system prompt 引导模型输出 JSON 工具调用或 NONE，
    再解析返回结果。返回 None 表示无需调用工具。

    这一步本身是一次独立的 LLM 调用，因此单独记一条 ``tool_detect`` 用量 ——
    否则「工具识别占了多少成本」永远查不出来。
    """
    from app.services.tool_service import build_tool_detection_prompt, extract_tool_call

    box, sink = _usage_sink()
    started = time.time()
    try:
        reply = await _call_cloud_sync(provider, [
            {"role": "system", "content": build_tool_detection_prompt()},
            {"role": "user", "content": message},
        ], on_usage=sink)
    except Exception as e:
        await _record_usage(
            "tool_detect",
            provider=getattr(provider, "provider", ""),
            model=getattr(provider, "model", ""),
            tier="cloud", usage=box,
            latency_ms=int((time.time() - started) * 1000),
            success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
        )
        raise

    await _record_usage(
        "tool_detect",
        provider=getattr(provider, "provider", ""),
        model=getattr(provider, "model", ""),
        tier="cloud", usage=box,
        latency_ms=int((time.time() - started) * 1000), user_id=user_id,
    )
    return extract_tool_call(reply)


async def call_ollama_stream(
    message: str,
    session_id: str = "",
    db=None,
    user_id=None,
    model: str = None,
    on_usage=None,
    max_tokens: int = None,
    on_sources=None,
) -> AsyncGenerator[str, None]:
    """本地模型流式对话。

    ``model`` 为空时由本地调用层按角色路由（默认「语言类」模型，负责对话表达）；
    传入具体模型名可精确指定，供模型切换接口使用。
    """
    from app.services import ollama_client

    history = await _get_conversation_history(session_id, user_id)

    context = await _build_context(db, message, on_sources)

    full_message = context + message if context else message
    messages = history + [{"role": "user", "content": full_message}]

    async for chunk in ollama_client.chat_stream(
        messages,
        model=model,
        role=ollama_client.ROLE_CHAT,
        on_usage=on_usage,
        max_tokens=max_tokens or settings.ai_max_output_tokens,
    ):
        yield chunk


async def call_chat_stream(
    message: str,
    session_id: str = "",
    db=None,
    user_id=None,
    on_sources=None,
    on_meta=None,
) -> AsyncGenerator[str, None]:
    """对话主入口，按降级链依次尝试各后端。

    降级链：**云端（DeepSeek 优先 → 智谱备选）→ 本地 Ollama → 规则引擎**。
    任一环节失败都自动落到下一级，保证 AI 服务不中断。

    流式场景的重复输出问题：一旦某个后端已经产出过内容才失败，就不再降级 ——
    否则用户会先看到半截回答、再看到另一份完整回答。
    """
    from app.services.llm_client import get_cloud_providers

    response_content = ""
    providers = get_cloud_providers()

    # ===== Function Calling：工具识别 → 执行 → 结果注入二次生成 =====
    # should_detect_tool 是廉价前置过滤：不涉及数据库的消息直接跳过这次识别调用，
    # 省下一次完整的 LLM 往返（实测本机约 2.9s，云端则是一次额外 token 消耗）。
    if db and providers and should_detect_tool(message):
        primary = providers[0]
        try:
            from app.services.tool_service import build_tool_result_context, execute_tool

            tool_call = await _detect_tool_call(primary, message)
            if tool_call:
                tool_result = await execute_tool(
                    tool_call["tool"], tool_call.get("args", {}), db
                )
                augmented = build_tool_result_context(tool_result) + f"\n用户的问题：{message}"
                box, sink = _usage_sink()
                started = time.time()
                async for chunk in call_cloud_stream(
                    primary, augmented, session_id, db, user_id,
                    on_usage=sink, on_sources=on_sources,
                ):
                    response_content += chunk
                    yield chunk
                latency = int((time.time() - started) * 1000)
                await _record_usage(
                    "chat", provider=getattr(primary, "provider", ""),
                    model=getattr(primary, "model", ""), tier="cloud", usage=box,
                    latency_ms=latency, user_id=user_id,
                )
                _emit_meta(
                    on_meta, provider=getattr(primary, "provider", ""),
                    model=getattr(primary, "model", ""), tier="cloud",
                    prompt_tokens=box.get("prompt_tokens", 0),
                    completion_tokens=box.get("completion_tokens", 0),
                    latency_ms=latency, tool_called=True,
                )
                if response_content:
                    from app.services.knowledge_service import KnowledgeService
                    await KnowledgeService.learn_from_response(
                        db, message, response_content,
                        source=getattr(primary, "provider", "") or "auto",
                    )
                return
        except Exception as e:
            logger.warning("Function calling 失败，回退到普通对话: %s", e)

    # ===== 第一级：云端模型（依次尝试各已配置的服务商）=====
    for provider in providers:
        box, sink = _usage_sink()
        started = time.time()
        try:
            async for chunk in call_cloud_stream(
                provider, message, session_id, db, user_id,
                on_usage=sink, on_sources=on_sources,
            ):
                response_content += chunk
                yield chunk
            latency = int((time.time() - started) * 1000)
            await _record_usage(
                "chat", provider=getattr(provider, "provider", ""),
                model=getattr(provider, "model", ""), tier="cloud", usage=box,
                latency_ms=latency, user_id=user_id,
            )
            _emit_meta(
                on_meta, provider=getattr(provider, "provider", ""),
                model=getattr(provider, "model", ""), tier="cloud",
                prompt_tokens=box.get("prompt_tokens", 0),
                completion_tokens=box.get("completion_tokens", 0),
                latency_ms=latency, tool_called=False,
            )
            if db and response_content:
                from app.services.knowledge_service import KnowledgeService
                await KnowledgeService.learn_from_response(
                    db, message, response_content,
                    source=getattr(provider, "provider", "") or "auto",
                )
            return
        except Exception as e:
            await _record_usage(
                "chat", provider=getattr(provider, "provider", ""),
                model=getattr(provider, "model", ""), tier="cloud", usage=box,
                latency_ms=int((time.time() - started) * 1000),
                success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
            )
            if response_content:
                logger.warning(
                    "云端模型 %s 中途失败，已输出部分内容，不再降级以避免重复回答: %s",
                    provider.name, e,
                )
                return
            logger.warning("云端模型 %s 调用失败，尝试下一个后端: %s", provider.name, e)

    # ===== 第二级：本地模型（按角色路由，同样支持 Function Calling）=====
    if settings.ollama_enabled:
        from app.services import ollama_client as local_llm

        # 本地路径也做工具识别，避免降级后「查库」这一核心能力直接失效。
        # 这里刻意用「结构化任务」模型：实测它做工具识别的准确率与语言模型
        # 相同（6/6 = 100%），但平均耗时明显更低（5.4s vs 7.4s）；
        # 实测数据见 reports/local_model_benchmark.md。
        if db and should_detect_tool(message):
            try:
                from app.services.tool_service import build_tool_result_context, execute_tool

                box, sink = _usage_sink()
                started = time.time()
                tool_call = await local_llm.detect_tool_call(message, on_usage=sink)
                await _record_usage(
                    "tool_detect",
                    provider="ollama",
                    model=local_llm.resolve_model(local_llm.ROLE_TOOL, settings),
                    tier="local", usage=box,
                    latency_ms=int((time.time() - started) * 1000), user_id=user_id,
                )
                if tool_call:
                    tool_result = await execute_tool(
                        tool_call["tool"], tool_call.get("args", {}), db
                    )
                    message = (
                        build_tool_result_context(tool_result) + f"\n用户的问题：{message}"
                    )
            except Exception as e:
                logger.warning("本地工具识别失败，按普通对话处理: %s", e)

        # 复用 model_service 的候选顺序（而非本地层的默认顺序），
        # 这样管理端切换的模型偏好对用户对话同样生效。
        from app.services.model_service import local_candidates

        for model in local_candidates():
            box, sink = _usage_sink()
            started = time.time()
            try:
                async for chunk in call_ollama_stream(
                    message, session_id, db, user_id, model=model,
                    on_usage=sink, on_sources=on_sources,
                ):
                    response_content += chunk
                    yield chunk
                latency = int((time.time() - started) * 1000)
                await _record_usage(
                    "chat", provider="ollama", model=model, tier="local", usage=box,
                    latency_ms=latency, user_id=user_id,
                )
                _emit_meta(
                    on_meta, provider="ollama", model=model, tier="local",
                    prompt_tokens=box.get("prompt_tokens", 0),
                    completion_tokens=box.get("completion_tokens", 0),
                    latency_ms=latency, tool_called=False,
                )
                return
            except Exception as e:
                await _record_usage(
                    "chat", provider="ollama", model=model, tier="local", usage=box,
                    latency_ms=int((time.time() - started) * 1000),
                    success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
                )
                if response_content:
                    logger.warning(
                        "本地模型 %s 中途失败，已输出部分内容，不再降级以避免重复回答: %s",
                        model, e,
                    )
                    return
                logger.warning("本地模型 %s 调用失败，尝试下一个本地模型: %s", model, e)

    from app.services.local_model_service import LocalModelService
    # 规则引擎兜底也接入知识库 RAG，保证降级时核心检索能力不失效
    rag_context = await _build_context(db, message, on_sources)
    started = time.time()
    response = await LocalModelService.chat(message, db, rag_context)
    # 规则引擎不消耗 token，但这条记录本身就是「降级事件」，用于统计
    # 「有多少请求最终没走到任何模型」——只统计 token 的话这类事件会完全消失。
    fallback_latency = int((time.time() - started) * 1000)
    await _record_usage(
        "chat", provider="local_fallback", model="规则引擎", tier="fallback",
        latency_ms=fallback_latency, user_id=user_id,
    )
    # 兜底也要透出 meta：前端据此显示「本次由规则引擎回答」，
    # 用户才知道自己看到的是降级结果而非模型输出。
    _emit_meta(
        on_meta, provider="local_fallback", model="规则引擎", tier="fallback",
        prompt_tokens=0, completion_tokens=0, latency_ms=fallback_latency,
        tool_called=False,
    )
    yield response


async def update_conversation_history(session_id: str, user_message: str, ai_response: str, user_id=None):
    await _update_conversation_history(session_id, user_message, ai_response, user_id)


async def generate_full_response(message: str, session_id: str = "", user_id=None) -> str:
    full_response = ""
    async for chunk in call_chat_stream(message, session_id, user_id=user_id):
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

    分析报告属于一次性任务，使用独立的临时会话键，避免与用户的聊天历史
    混在一起导致上下文污染。
    """
    prompt = _build_analysis_prompt(stats)
    # 每次生成独立会话，任务结束即释放，不污染用户聊天上下文
    temp_session = f"analysis-{uuid.uuid4().hex}"

    from app.services.llm_client import get_cloud_providers

    try:
        for provider in get_cloud_providers():
            box, sink = _usage_sink()
            started = time.time()
            try:
                report = ""
                async for chunk in call_cloud_stream(
                    provider, prompt, temp_session, db, None, on_usage=sink,
                    max_tokens=settings.ai_analysis_max_output_tokens,
                ):
                    report += chunk
                await _record_usage(
                    "analysis", provider=getattr(provider, "provider", ""),
                    model=getattr(provider, "model", ""), tier="cloud", usage=box,
                    latency_ms=int((time.time() - started) * 1000),
                )
                if report.strip():
                    return report, "primary"
            except Exception as e:
                logger.warning("分析报告 %s 生成失败：%s", provider.name, e)

        if settings.ollama_enabled:
            from app.services import ollama_client as local_llm

            report_model = local_llm.resolve_model(local_llm.ROLE_CHAT, settings)
            box, sink = _usage_sink()
            started = time.time()
            try:
                report = ""
                async for chunk in call_ollama_stream(
                    prompt, temp_session, db, None, on_usage=sink,
                    max_tokens=settings.ai_analysis_max_output_tokens,
                ):
                    report += chunk
                await _record_usage(
                    "analysis", provider="ollama", model=report_model,
                    tier="local", usage=box,
                    latency_ms=int((time.time() - started) * 1000),
                )
                if report.strip():
                    return report, "local"
            except Exception as e:
                logger.warning(f"分析报告 Ollama 失败：{e}")
    finally:
        clear_session(temp_session)

    return None, "none"