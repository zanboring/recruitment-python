"""react_agent（ReAct 多步推理 Agent）的单元测试。

覆盖：
- parse_react_output：Thought / Action / Final Answer 解析，容忍各种格式
- execute_agent_tool：三个工具的执行与降级（不依赖真实数据库）
- run_react_loop：多步循环、工具调用、max_steps 兜底、无格式输出容错

所有用例离线可跑（mock step_fn 与工具执行，不联网、不连数据库）。
"""
import json
from unittest.mock import AsyncMock, patch

from app.services.react_agent import (
    build_agent_system_prompt,
    execute_agent_tool,
    parse_react_output,
    run_react_loop,
)


# ---------------- 解析器 ----------------

class TestParseReactOutput:
    def test_final_answer(self):
        r = parse_react_output("Thought: 信息足够\nFinal Answer: 长沙有 42 个 Java 岗位")
        assert r["final_answer"] == "长沙有 42 个 Java 岗位"
        assert r["action"] is None
        assert r["thought"] == "信息足够"

    def test_action_with_thought(self):
        r = parse_react_output(
            'Thought: 需要查岗位\nAction: query_jobs {"city": "长沙", "keyword": "Java"}'
        )
        assert r["thought"] == "需要查岗位"
        assert r["action"][0] == "query_jobs"
        assert r["action"][1] == {"city": "长沙", "keyword": "Java"}
        assert r["final_answer"] is None

    def test_action_嵌套JSON参数(self):
        r = parse_react_output(
            'Action: query_jobs {"city": "长沙", "nested": {"a": 1, "b": [2, 3]}}'
        )
        assert r["action"][1]["nested"] == {"a": 1, "b": [2, 3]}

    def test_action_无参数(self):
        r = parse_react_output("Action: get_city_stats")
        assert r["action"] == ("get_city_stats", {})

    def test_action_无参数但带大括号(self):
        r = parse_react_output("Action: get_city_stats {}")
        assert r["action"] == ("get_city_stats", {})

    def test_final_answer优先级高于action(self):
        r = parse_react_output(
            'Thought: x\nAction: query_jobs {}\nFinal Answer: 直接回答'
        )
        assert r["final_answer"] == "直接回答"
        assert r["action"] is None

    def test_大小写不敏感(self):
        r = parse_react_output("final answer: 答案")
        assert r["final_answer"] == "答案"

    def test_中文最终答案标签(self):
        r = parse_react_output("最终答案：这是回答")
        assert r["final_answer"] == "这是回答"

    def test_空输入(self):
        r = parse_react_output("")
        assert r["final_answer"] is None
        assert r["action"] is None

    def test_无格式输出(self):
        r = parse_react_output("你好，直接回答你")
        assert r["final_answer"] is None
        assert r["action"] is None

    def test_final_answer多行(self):
        r = parse_react_output("Final Answer: 第一行\n第二行\n第三行")
        assert r["final_answer"] == "第一行\n第二行\n第三行"


# ---------------- 工具执行 ----------------

class TestExecuteAgentTool:
    async def test_query_jobs复用单步工具(self):
        with patch(
            "app.services.tool_service.execute_tool",
            new=AsyncMock(return_value='{"total": 42}'),
        ) as mock_exec:
            result = await execute_agent_tool(
                "query_jobs", {"city": "长沙"}, db=object()
            )
        assert json.loads(result)["total"] == 42
        mock_exec.assert_awaited_once()

    async def test_recommend_jobs(self):
        recs = [
            {"title": "Java工程师", "company_name": "A公司", "city": "长沙",
             "min_salary": 8000, "max_salary": 12000, "score": 0.9},
        ]
        with patch(
            "app.recommender.analyzer.recommend_jobs",
            new=AsyncMock(return_value=recs),
        ):
            result = await execute_agent_tool(
                "recommend_jobs", {"skills": "Java", "city": "长沙"}, db=object()
            )
        data = json.loads(result)
        assert data["total"] == 1
        assert data["recommendations"][0]["title"] == "Java工程师"
        assert data["recommendations"][0]["salary"] == "8000-12000元"

    async def test_recommend_jobs缺skills返回错误(self):
        result = await execute_agent_tool("recommend_jobs", {"city": "长沙"}, db=object())
        assert "error" in json.loads(result)

    async def test_get_city_stats(self):
        with patch(
            "app.services.job_service.JobService.stat_by_city",
            new=AsyncMock(return_value=[{"name": "长沙", "count": 30}]),
        ):
            result = await execute_agent_tool("get_city_stats", {}, db=object())
        data = json.loads(result)
        assert data["city_distribution"][0]["name"] == "长沙"

    async def test_未知工具返回错误(self):
        result = await execute_agent_tool("no_such_tool", {}, db=object())
        assert "error" in json.loads(result)


# ---------------- ReAct 循环 ----------------

async def _collect(gen):
    return [e async for e in gen]


class TestRunReactLoop:
    async def test_单步直接final_answer(self):
        async def fake_step(messages, on_usage):
            return "Thought: 够了\nFinal Answer: 直接回答"

        events = await _collect(run_react_loop("问", None, step_fn=fake_step))
        answers = [e for e in events if e["type"] == "answer"]
        assert len(answers) == 1
        assert answers[0]["content"] == "直接回答"

    async def test_多步调用工具后收尾(self):
        step_outputs = iter([
            'Thought: 查岗位\nAction: query_jobs {"city": "长沙"}',
            "Thought: 信息足够\nFinal Answer: 长沙有 42 个岗位",
        ])

        async def fake_step(messages, on_usage):
            return next(step_outputs)

        with patch(
            "app.services.react_agent.execute_agent_tool",
            new=AsyncMock(return_value='{"total": 42}'),
        ) as mock_exec:
            events = await _collect(run_react_loop("问", None, step_fn=fake_step))

        types = [e["type"] for e in events]
        assert "step" in types
        assert "observation" in types
        assert types[-1] == "answer"
        assert events[-1]["content"] == "长沙有 42 个岗位"
        mock_exec.assert_awaited_once()

    async def test_达到max_steps强制收尾(self):
        async def fake_step(messages, on_usage):
            # 一直返回 Action，永远不给 Final Answer；最后一轮被强制收尾
            return 'Thought: 继续查\nAction: query_jobs {"city": "长沙"}'

        with patch(
            "app.services.react_agent.execute_agent_tool",
            new=AsyncMock(return_value='{"total": 1}'),
        ):
            events = await _collect(
                run_react_loop("问", None, step_fn=fake_step, max_steps=3)
            )

        # 3 步都是 action，第 3 步后强制收尾，最终仍是 answer 结尾
        assert events[-1]["type"] == "answer"
        # 工具被调用了 max_steps 次（每一步都执行了）
        step_events = [e for e in events if e["type"] == "step" and e["action"]]
        assert len(step_events) == 3

    async def test_无格式输出容错为答案(self):
        async def fake_step(messages, on_usage):
            return "直接回答，没有 Thought/Action 标签"

        events = await _collect(run_react_loop("问", None, step_fn=fake_step))
        assert events[-1]["type"] == "answer"
        assert events[-1]["content"] == "直接回答，没有 Thought/Action 标签"


# ---------------- prompt ----------------

class TestBuildPrompt:
    def test_prompt包含全部工具与格式说明(self):
        p = build_agent_system_prompt()
        assert "query_jobs" in p
        assert "recommend_jobs" in p
        assert "get_city_stats" in p
        assert "Thought" in p
        assert "Final Answer" in p
