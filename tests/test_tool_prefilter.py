"""工具识别前置过滤测试。

背景：朴素实现对**每条**用户消息都发起一次「是否需要查库」的 LLM 调用，
实测本机每次约 2.9s（云端则是一次额外 token 消耗）。过滤逻辑由此而来，
本文件锁定它的判定边界 —— 尤其是「不能漏判数据类问题」这条底线。
"""
import pytest

from app.config import settings
from app.services.tool_service import should_detect_tool


@pytest.fixture(autouse=True)
def _restore_prefilter_config():
    """还原被用例改动的配置，避免污染其他用例。"""
    enabled = settings.ai_tool_prefilter_enabled
    limit = settings.ai_tool_prefilter_max_chars
    yield
    settings.ai_tool_prefilter_enabled = enabled
    settings.ai_tool_prefilter_max_chars = limit


# ---------- 必须发起识别：涉及数据库的问题 ----------

@pytest.mark.parametrize("message", [
    "长沙有多少Java岗位",
    "深圳前端岗位薪资水平怎么样",
    "帮我查查北京的Python岗位",
    "我想找一份在长沙的工作",
    "薪资高的工作推荐一下",
    "现在招聘市场什么情况",
    "统计一下各城市的岗位分布",
    "哪家公司招Java架构师",
    "长沙",            # 只有城市名，也算信号
    "待遇怎么样",
])
def test_数据类问题必须发起识别(message):
    """漏判的代价是「模型凭空编数字」，属于硬伤，因此这些必须全部命中。"""
    assert should_detect_tool(message) is True


# ---------- 可以跳过：与数据库无关的短消息 ----------

@pytest.mark.parametrize("message", [
    "你好",
    "谢谢",
    "RAG是什么",
    "介绍一下这个系统",
    "你是谁",
])
def test_无领域信号的短消息跳过识别(message):
    assert should_detect_tool(message) is False


# ---------- 保守边界 ----------

def test_长消息即使无信号也不跳过():
    """长消息可能隐含数据需求，宁可多花一次调用也不冒险跳过。"""
    message = "能不能详细讲一下这个招聘数据分析系统它的整体架构和实现思路是什么样的"
    assert len(message) > settings.ai_tool_prefilter_max_chars
    assert should_detect_tool(message) is True


def test_空消息不发起识别():
    assert should_detect_tool("") is False
    assert should_detect_tool("   ") is False
    assert should_detect_tool(None) is False


def test_长度阈值可配置():
    """阈值可调，便于按实际对话语料调优。"""
    message = "这是一个有点长的但与数据库无关的问题"
    settings.ai_tool_prefilter_max_chars = 5
    assert should_detect_tool(message) is True

    settings.ai_tool_prefilter_max_chars = 200
    assert should_detect_tool(message) is False


def test_可整体关闭过滤():
    """一旦发现漏判，改配置即可回退，无需改代码。"""
    settings.ai_tool_prefilter_enabled = False
    assert should_detect_tool("你好") is True
    assert should_detect_tool("") is True
