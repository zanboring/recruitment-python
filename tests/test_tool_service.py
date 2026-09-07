"""tool_service 的单元测试。

覆盖：
- extract_tool_call：各种格式的工具调用解析（正常 JSON / markdown 包裹 / NONE / 夹带）
- build_tool_detection_prompt / build_tool_result_context：prompt 构造
- execute_tool：query_jobs 工具执行（mock JobService，不依赖真实数据库）

运行方式（项目根目录）：
    pip install pytest
    python -m pytest tests/test_tool_service.py -v
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.tool_service import (
    build_tool_detection_prompt,
    build_tool_result_context,
    execute_tool,
    extract_tool_call,
)


class TestExtractToolCall:
    def test_正常JSON(self):
        text = '{"tool": "query_jobs", "args": {"city": "长沙", "keyword": "Java"}}'
        r = extract_tool_call(text)
        assert r["tool"] == "query_jobs"
        assert r["args"]["city"] == "长沙"
        assert r["args"]["keyword"] == "Java"

    def test_markdown代码块包裹(self):
        text = '```json\n{"tool": "query_jobs", "args": {"city": "长沙"}}\n```'
        r = extract_tool_call(text)
        assert r["tool"] == "query_jobs"
        assert r["args"]["city"] == "长沙"

    def test_NONE返回None(self):
        assert extract_tool_call("NONE") is None

    def test_普通文本返回None(self):
        assert extract_tool_call("你好，我是招聘助手。") is None

    def test_文本夹带JSON(self):
        text = '好的，我帮你查一下 {"tool": "query_jobs", "args": {"keyword": "前端"}}'
        r = extract_tool_call(text)
        assert r["tool"] == "query_jobs"
        assert r["args"]["keyword"] == "前端"

    def test_空输入返回None(self):
        assert extract_tool_call("") is None

    def test_空args(self):
        r = extract_tool_call('{"tool": "query_jobs", "args": {}}')
        assert r["tool"] == "query_jobs"


class TestBuildPrompts:
    def test_detection_prompt包含工具名与NONE(self):
        p = build_tool_detection_prompt()
        assert "query_jobs" in p
        assert "NONE" in p

    def test_result_context包含结果(self):
        c = build_tool_result_context('{"total": 10}')
        assert "total" in c


class TestExecuteTool:
    def test_query_jobs返回总数与筛选条件(self):
        fake_db = object()
        with patch(
            "app.services.tool_service.JobService.count_jobs",
            new=AsyncMock(return_value=42),
        ) as mock_count, patch(
            "app.services.tool_service.JobService.query_jobs",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(
                execute_tool("query_jobs", {"city": "长沙", "keyword": "Java"}, fake_db)
            )
        data = json.loads(result)
        assert data["total"] == 42
        assert data["city"] == "长沙"
        assert data["keyword"] == "Java"
        assert data["samples"] == []
        mock_count.assert_called_once()

    def test_未知工具返回错误(self):
        fake_db = object()
        result = asyncio.run(execute_tool("unknown_tool", {}, fake_db))
        data = json.loads(result)
        assert "error" in data
