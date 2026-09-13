"""ReAct（Reasoning + Acting）多步推理 Agent。

在现有「单步 Function Calling」之上扩展为多步：模型在
``Thought → Action → Observation`` 循环中逐步推理，直到给出 ``Final Answer``。

用于回答需要「先查数据、再根据结果决定下一步」的复合问题，例如
「我学 Java 想找长沙的工作，先看看岗位多不多、薪资怎么样、有没有推荐」——
单步 Function Calling 只会查一次，而这类问题往往需要「先查总量 → 再看分布 →
再要推荐」多个回合。

设计取舍：
1. **工具集独立**于单步 Function Calling 的 ``TOOLS``（Agent 需要推荐/分布等
   更丰富的动作，而单步链路只识别「要不要查库」这一种意图，两者职责不同）。
2. **文本格式而非纯 JSON**：ReAct 用 ``Thought / Action / Final Answer`` 文本标签，
   本地小模型（qwen2.5:14b）也能稳定输出；Action 参数仍用 JSON 便于解析。
3. **max_steps 硬上限 + 兜底**：每步都是一次 LLM 调用（有成本、有延迟），
   必须给循环一个上界；达到上限仍无 Final Answer 时，强制模型基于已有
   Observation 收尾，绝不无限循环。
4. **逐步透出推理过程**：每步的 Thought / Action / Observation 以事件流形式
   交给调用方，前端可展示 Agent 的「思考链」，回答可解释、可核查。
"""
import json
import logging
import re
import time
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# Agent 工具集：三个动作覆盖「查统计 / 做推荐 / 看分布」三类查询。
# 与 tool_service.TOOLS 的差异：这里多了 recommend_jobs / get_city_stats，
# 因为 Agent 场景要回答的是复合问题，需要比「数岗位」更丰富的取数能力。
AGENT_TOOLS = [
    {
        "name": "query_jobs",
        "desc": "查询某城市 / 某关键词的岗位数量与示例",
        "params": {"city": "城市名（可选）", "keyword": "岗位关键词（可选）"},
    },
    {
        "name": "recommend_jobs",
        "desc": "按候选人技能推荐匹配岗位（Jaccard 相似度加权排序）",
        "params": {"skills": "技能，逗号分隔（必填）", "city": "城市名（可选）"},
    },
    {
        "name": "get_city_stats",
        "desc": "查询各城市的岗位数量分布",
        "params": {},
    },
]

# ReAct 每步输出上限：Thought + 一行 Action 或 Final Answer，1024 足够。
_AGENT_STEP_MAX_TOKENS = 1024


def build_agent_system_prompt() -> str:
    """构建 ReAct Agent 的 system prompt。

    明确要求模型按 Thought → Action → Observation 循环推理，
    并列出可用工具与参数，引导「查真实数据、不编造数字」。
    """
    tool_lines = []
    for t in AGENT_TOOLS:
        params = ", ".join(f"{k}: {v}" for k, v in t["params"].items()) or "无参数"
        tool_lines.append(f"- {t['name']}：{t['desc']}。参数 {{{params}}}")
    tools_desc = "\n".join(tool_lines)

    return (
        "你是招聘数据分析助手，可以调用工具查询真实岗位数据来回答用户问题。\n\n"
        "请按以下格式逐步推理（Thought 与 Action 成对出现，Observation 由系统自动回填）：\n\n"
        "Thought: 你当前的思考\n"
        "Action: 工具名 {\"参数\": \"值\"}\n\n"
        "（可以重复多个 Thought/Action 轮次，直到信息足够）\n\n"
        "当信息足够后，用以下格式给出最终答案：\n\n"
        "Thought: 信息已足够\n"
        "Final Answer: 给用户的最终回答\n\n"
        "可用工具：\n"
        f"{tools_desc}\n\n"
        "规则：\n"
        "1. 一次只调用一个工具；每次输出一个 Thought 和最多一个 Action。\n"
        "2. 涉及岗位数量、薪资、分布、推荐等问题，必须先调用工具查真实数据，绝不编造数字。\n"
        "3. 信息足够后立即输出 Final Answer，不要重复调用。\n"
        "4. Final Answer 要引用工具返回的真实数字，面向用户友好表达。"
    )


def _extract_action(text: str) -> Optional[tuple]:
    """从模型输出中解析 Action：返回 (工具名, 参数字典)；无 Action 返回 None。

    工具名后跟一个 JSON 对象；用括号平衡匹配提取完整 JSON，
    可正确处理参数里的嵌套花括号，容忍前后夹杂其他文本。
    """
    m = re.search(r"Action\s*[:：]\s*([a-zA-Z_][\w]*)", text, re.IGNORECASE)
    if not m:
        return None
    tool = m.group(1)
    rest = text[m.end():]
    start = rest.find("{")
    if start == -1:
        return (tool, {})
    depth = 0
    for i in range(start, len(rest)):
        if rest[i] == "{":
            depth += 1
        elif rest[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    args = json.loads(rest[start:i + 1])
                except json.JSONDecodeError:
                    args = {}
                return (tool, args)
    return (tool, {})


def parse_react_output(text: str) -> dict:
    """解析模型单步输出。

    返回::

        {
            "thought": str | "",          # Thought 后的思考文本
            "action": (工具名, 参数字典) | None,
            "final_answer": str | None,   # 出现 Final Answer 时为最终答案
        }

    优先级：Final Answer > Action。模型若在同一条输出里既给了 Action 又给了
    Final Answer（不应发生但需容错），按 Final Answer 处理，避免多调一次工具。
    """
    text = (text or "").strip()

    final = re.search(
        r"(?:Final\s*Answer|最终答案)\s*[:：]\s*([\s\S]*)",
        text, re.IGNORECASE,
    )
    if final:
        answer = final.group(1).strip()
        if answer:
            thought = ""
            tm = re.search(r"Thought\s*[:：]\s*([^\n]*)", text, re.IGNORECASE)
            if tm:
                thought = tm.group(1).strip()
            return {"thought": thought, "action": None, "final_answer": answer}

    thought = ""
    tm = re.search(r"Thought\s*[:：]\s*([^\n]*)", text, re.IGNORECASE)
    if tm:
        thought = tm.group(1).strip()

    action = _extract_action(text)
    return {"thought": thought, "action": action, "final_answer": None}


async def execute_agent_tool(name: str, args: dict, db: AsyncSession) -> str:
    """执行 Agent 工具，返回 JSON 字符串结果（供模型下一步推理引用）。

    ``query_jobs`` 直接复用单步 Function Calling 的实现，保证两处口径一致；
    ``recommend_jobs`` / ``get_city_stats`` 复用推荐器与统计服务。
    """
    args = args or {}

    if name == "query_jobs":
        from app.services.tool_service import execute_tool
        return await execute_tool("query_jobs", args, db)

    if name == "recommend_jobs":
        from app.recommender.analyzer import recommend_jobs

        skills = str(args.get("skills", "") or "").strip()
        city = str(args.get("city", "") or "").strip()
        if not skills:
            return json.dumps({"error": "recommend_jobs 需要 skills 参数"}, ensure_ascii=False)

        recs = await recommend_jobs(db, skills=skills, city=city, limit=5)
        # 精简字段，避免把整段 job_desc 喂回模型（徒增 token 且无助于判断）
        slim = [
            {
                "title": r["title"],
                "company": r["company_name"],
                "city": r["city"],
                "salary": (
                    f"{int(r['min_salary'])}-{int(r['max_salary'])}元"
                    if r.get("min_salary") and r.get("max_salary") else None
                ),
                "score": r["score"],
            }
            for r in recs
        ]
        return json.dumps(
            {"recommendations": slim, "total": len(slim)},
            ensure_ascii=False,
        )

    if name == "get_city_stats":
        from app.services.job_service import JobService

        stats = await JobService.stat_by_city(db)
        return json.dumps(
            {"city_distribution": stats[:10]},
            ensure_ascii=False,
        )

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


async def _cloud_step(provider, messages: list, on_usage=None) -> str:
    """云端模型单步推理（非流式，低温度保证输出格式稳定）。"""
    from app.services.llm_client import complete_chat

    return await complete_chat(
        provider,
        messages,
        temperature=0.1,
        on_usage=on_usage,
        max_tokens=_AGENT_STEP_MAX_TOKENS,
    )


async def _local_step(messages: list, on_usage=None) -> str:
    """本地 Ollama 模型单步推理（非流式）。"""
    from app.services import ollama_client

    reply = await ollama_client.chat_sync(
        messages,
        role=ollama_client.ROLE_CHAT,
        on_usage=on_usage,
    )
    return (reply or {}).get("content", "") or ""


async def run_react_loop(
    message: str,
    db: AsyncSession,
    *,
    step_fn,
    max_steps: int = 5,
    on_usage=None,
) -> AsyncGenerator[dict, None]:
    """ReAct 主循环：逐步推理 → 调工具 → 观察 → 继续，直到 Final Answer。

    ``step_fn`` 是「单步推理」函数签名 ``async (messages, on_usage) -> str``，
    由调用方注入云端或本地实现，从而让循环逻辑与后端解耦。

    yield 的事件（调用方自行决定如何转发给前端）::

        {"type": "step", "step": n, "thought": ..., "action": ..., "args": ...}
        {"type": "observation", "step": n, "content": ...}
        {"type": "answer", "content": ...}   # 最终答案
    """
    messages = [
        {"role": "system", "content": build_agent_system_prompt()},
        {"role": "user", "content": message},
    ]

    for step in range(1, max_steps + 1):
        reply = await step_fn(messages, on_usage)
        parsed = parse_react_output(reply)

        # 有最终答案 → 结束
        if parsed["final_answer"]:
            if parsed["thought"]:
                yield {
                    "type": "step", "step": step,
                    "thought": parsed["thought"], "action": None, "args": None,
                }
            yield {"type": "answer", "content": parsed["final_answer"]}
            return

        # 有工具调用 → 执行 → 观察 → 回填继续
        if parsed["action"]:
            tool_name, args = parsed["action"]
            yield {
                "type": "step", "step": step,
                "thought": parsed["thought"],
                "action": tool_name, "args": args,
            }
            try:
                observation = await execute_agent_tool(tool_name, args, db)
            except Exception as e:  # noqa: BLE001 - 工具失败不能中断整个 Agent
                logger.warning("Agent 工具 %s 执行失败：%s", tool_name, e)
                observation = json.dumps(
                    {"error": f"{tool_name} 执行失败: {e}"}, ensure_ascii=False
                )
            yield {"type": "observation", "step": step, "content": observation}

            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {"role": "user", "content": f"Observation: {observation}"}
            )
            continue

        # 既无 Action 也无 Final Answer（模型没按格式输出）→ 容错：当最终答案
        yield {"type": "answer", "content": reply.strip() or "抱歉，我暂时无法回答这个问题。"}
        return

    # 达到 max_steps 仍无 Final Answer：强制收尾，绝不无限循环。
    yield {
        "type": "step", "step": max_steps,
        "thought": "已达最大推理步数，基于已有观察直接总结", "action": None, "args": None,
    }
    messages.append(
        {"role": "user", "content": "请基于以上所有 Observation 直接输出 Final Answer，不要再调用工具。"}
    )
    final = await step_fn(messages, on_usage)
    parsed = parse_react_output(final)
    yield {"type": "answer", "content": parsed["final_answer"] or final.strip()}


async def _record_agent_usage(
    *,
    provider: str,
    model: str,
    tier: str,
    usage: dict,
    latency_ms: int,
    success: bool,
    error_msg: str = "",
    user_id=None,
) -> None:
    """记录一次 Agent 会话的用量（内部吞异常，不影响对话主流程）。"""
    from app.services import usage_service

    try:
        await usage_service.record(
            scene="agent",
            provider=provider,
            model=model,
            tier=tier,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            success=success,
            error_msg=error_msg,
            user_id=user_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Agent 用量记录失败（已忽略）：%s", e)


async def run_agent_stream(
    message: str,
    db: AsyncSession,
    user_id=None,
) -> AsyncGenerator[dict, None]:
    """Agent 高层入口，按三级降级链依次尝试，逐步 yield 推理事件。

    降级链与普通对话一致：**云端（DeepSeek 优先 → 智谱备选）→ 本地 Ollama →
    规则引擎兜底**。ReAct 每步都是一次完整 LLM 调用，任一后端失败自动落到
    下一级，保证服务不中断。

    yield 的事件结构见 ``run_react_loop`` 文档；额外透出::

        {"type": "meta", "provider": ..., "model": ..., "tier": ...}

    降级策略：一旦某个后端已经产出过内容（yield 过 step 事件）才失败，
    就不再降级，避免用户看到「半段推理 + 另一份完整回答」的割裂输出。
    """
    from app.services.llm_client import get_cloud_providers

    max_steps = int(getattr(settings, "agent_max_steps", 5))
    providers = get_cloud_providers()
    yielded = False

    # ===== 第一级：云端 ReAct =====
    for provider in providers:
        box: dict = {}

        def sink(payload: dict) -> None:
            box.update(payload or {})

        started = time.time()
        try:
            async def cloud_step(msgs, on_usage):
                return await _cloud_step(provider, msgs, on_usage)

            async for event in run_react_loop(
                message, db, step_fn=cloud_step, max_steps=max_steps, on_usage=sink
            ):
                yielded = True
                yield event
            await _record_agent_usage(
                provider=getattr(provider, "provider", ""),
                model=getattr(provider, "model", ""),
                tier="cloud", usage=box,
                latency_ms=int((time.time() - started) * 1000),
                success=True, user_id=user_id,
            )
            yield {
                "type": "meta",
                "provider": getattr(provider, "provider", ""),
                "model": getattr(provider, "model", ""),
                "tier": "cloud",
                "steps": box.get("_steps", 0),
            }
            return
        except Exception as e:  # noqa: BLE001
            await _record_agent_usage(
                provider=getattr(provider, "provider", ""),
                model=getattr(provider, "model", ""),
                tier="cloud", usage=box,
                latency_ms=int((time.time() - started) * 1000),
                success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
            )
            if yielded:
                logger.warning("云端 Agent %s 中途失败，已输出部分内容，不再降级: %s", provider.name, e)
                yield {"type": "answer", "content": "抱歉，回答过程中出现了错误，请稍后重试。"}
                return
            logger.warning("云端 Agent %s 失败，尝试下一后端: %s", provider.name, e)

    # ===== 第二级：本地 Ollama ReAct =====
    if settings.ollama_enabled:
        from app.services import ollama_client

        box: dict = {}

        def local_sink(payload: dict) -> None:
            box.update(payload or {})

        started = time.time()
        model = ollama_client.resolve_model(ollama_client.ROLE_CHAT, settings)
        try:
            async def local_step(msgs, on_usage):
                return await _local_step(msgs, on_usage)

            async for event in run_react_loop(
                message, db, step_fn=local_step, max_steps=max_steps, on_usage=local_sink
            ):
                yielded = True
                yield event
            await _record_agent_usage(
                provider="ollama", model=model, tier="local", usage=box,
                latency_ms=int((time.time() - started) * 1000),
                success=True, user_id=user_id,
            )
            yield {
                "type": "meta", "provider": "ollama", "model": model, "tier": "local",
            }
            return
        except Exception as e:  # noqa: BLE001
            await _record_agent_usage(
                provider="ollama", model=model, tier="local", usage=box,
                latency_ms=int((time.time() - started) * 1000),
                success=False, error_msg=f"{type(e).__name__}: {e}", user_id=user_id,
            )
            if yielded:
                logger.warning("本地 Agent 中途失败，已输出部分内容，不再降级: %s", e)
                yield {"type": "answer", "content": "抱歉，回答过程中出现了错误，请稍后重试。"}
                return
            logger.warning("本地 Agent 失败，退回规则引擎: %s", e)

    # ===== 第三级：规则引擎兜底 =====
    from app.services.local_model_service import LocalModelService

    answer = await LocalModelService.chat(message, db, "")
    yield {
        "type": "meta", "provider": "local_fallback", "model": "规则引擎", "tier": "fallback",
    }
    yield {"type": "answer", "content": answer}
