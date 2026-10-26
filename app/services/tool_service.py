"""Function Calling 工具服务：让 AI 对话具备调用真实数据查询工具的能力。

采用「prompt 引导 + JSON 解析」的通用实现，不依赖具体模型的 native tool calling，
任何支持 JSON 输出的 LLM 均可复用。核心流程：

    用户提问 → 工具识别(模型判断是否要查库) → 执行工具(真实查库)
            → 结果注入上下文 → 二次生成(模型基于结果组织回答)

这与 OpenAI 的 function calling 协议思想一致：模型只负责「表达要调哪个工具、
带什么参数」，真正的函数执行由代码完成，执行结果再喂回模型继续生成。
"""
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.job import JobQueryDTO
from app.services.job_service import JobService

logger = logging.getLogger(__name__)

# 工具定义（JSON Schema，对齐 OpenAI function calling 风格）
# 当前仅实现 query_jobs 一个工具；TOOLS 列表架构支持横向扩展，新增工具只需追加定义 + 在 execute_tool 分发。
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_jobs",
            "description": (
                "查询招聘岗位统计信息。当用户询问某个城市、某个岗位关键词的岗位数量或岗位情况时使用，"
                "例如「长沙有多少 Java 岗位」「深圳前端岗位薪资多少」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如：长沙、深圳、北京"},
                    "keyword": {"type": "string", "description": "岗位关键词，如：Java、前端、Python"},
                },
            },
        },
    },
]


def build_tool_detection_prompt() -> str:
    """构建「工具识别」阶段的 system prompt。

    引导模型：需要查库时只输出 JSON 工具调用，否则只输出 NONE。
    低温度 + 强约束，保证输出可被 extract_tool_call 稳定解析。
    """
    return (
        "你是招聘助手，需要判断用户的问题是否需要查询岗位数据库。\n"
        "规则：\n"
        "1. 当用户询问岗位数量、岗位情况、薪资水平等需要查数据库的问题时，"
        "只输出一行 JSON（不要任何解释、代码块或标点）：\n"
        '   {"tool": "query_jobs", "args": {"city": "长沙", "keyword": "Java"}}\n'
        "   city 与 keyword 均为可选，从用户问题中提取；无法确定就省略该字段。\n"
        "2. 其他情况（闲聊、知识问答、自我介绍等），只输出一个词：NONE\n"
        "只输出上述两种结果之一，不要输出其他任何内容。"
    )


# ---------------- 工具识别的廉价前置过滤 ----------------
#
# 为什么需要它：朴素实现会对**每一条**用户消息都发起一次「是否需要查库」的
# LLM 调用。实测本机每次约 2.9s，云端则是一次额外的 token 消耗；而真实对话里
# 大量消息根本不涉及数据库（打招呼、概念问答、自我介绍…），这笔开销纯属浪费。
#
# 过滤原则：**宁可多判，不可漏判**。
#   漏判代价 —— 本该查库的问题被当成闲聊，模型凭空编数字，属于硬伤；
#   多判代价 —— 白花一次调用，答案依然正确。
# 因此规则刻意保守：只有「消息很短 **且** 不含任何领域信号」时才跳过。
_DOMAIN_SIGNALS = (
    # 岗位与求职
    "岗位", "职位", "招聘", "招人", "求职", "找工作", "工作机会", "内推", "hc",
    # 薪资待遇
    "薪资", "工资", "薪水", "薪酬", "月薪", "年薪", "待遇", "多少钱",
    # 组织主体
    "公司", "企业", "单位", "雇主", "大厂",
    # 数量与统计口径
    "多少", "几个", "几家", "几条", "数量", "统计", "占比", "分布", "排名",
    # 分析意图
    "分析", "对比", "趋势", "平均", "最高", "最低",
)

_CITY_SIGNALS = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "苏州",
    "天津", "重庆", "长沙", "郑州", "青岛", "合肥", "福州", "厦门", "济南", "大连",
    "宁波", "无锡", "佛山", "东莞", "昆明", "南昌", "贵阳", "南宁", "太原", "石家庄",
    "沈阳", "哈尔滨", "长春", "兰州", "银川", "西宁", "乌鲁木齐", "呼和浩特",
    "海口", "三亚", "珠海", "中山", "惠州",
)


def _looks_like_data_query(message: str) -> bool:
    """消息里是否出现了「可能涉及数据库」的信号词。"""
    return any(sig in message for sig in _DOMAIN_SIGNALS) or any(
        city in message for city in _CITY_SIGNALS
    )


def should_detect_tool(message: str) -> bool:
    """判断是否值得为这条消息发起一次「工具识别」LLM 调用。

    过滤逻辑（保守优先，见上方说明）：

    ==============================  ==========
    条件                            结果
    ==============================  ==========
    含领域信号或城市名               发起识别
    不含信号，且长度 > 上限          发起识别（保守起见不跳过）
    不含信号，且长度 <= 上限         跳过，按普通对话处理
    空消息                           跳过
    ==============================  ==========

    可通过 ``AI_TOOL_PREFILTER_ENABLED=false`` 整体关闭该过滤，
    一旦发现漏判可立即回退，无需改代码。
    """
    from app.config import settings

    if not getattr(settings, "ai_tool_prefilter_enabled", True):
        return True

    text = (message or "").strip()
    if not text:
        return False
    if _looks_like_data_query(text):
        return True

    limit = int(getattr(settings, "ai_tool_prefilter_max_chars", 24))
    if len(text) <= limit:
        logger.debug("跳过工具识别（短消息且无领域信号）：%s", text[:30])
        return False
    return True


def build_tool_result_context(tool_result_json: str) -> str:
    """把工具执行结果包装成注入最终生成阶段的消息。"""
    return (
        "以下是查询招聘岗位数据库得到的真实结果，请基于它回答用户的问题：\n"
        f"{tool_result_json}\n\n"
        "要求：若岗位总数为 0，如实说明没有查到，不要编造；回答要引用真实数字。"
    )


def extract_tool_call(text: str) -> Optional[dict]:
    """从模型输出中解析工具调用请求。

    返回 {"tool": "query_jobs", "args": {...}}；非工具调用返回 None。
    容忍 markdown 代码块包裹、前后空白、文本中夹带 JSON 等情况。
    """
    if not text:
        return None
    text = text.strip()

    # 去 markdown 代码块（```json ... ```）
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    # 直接解析整个文本
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    # 兜底：用括号平衡匹配提取第一个完整 JSON 对象（可正确处理 args 的嵌套花括号）
    if not isinstance(data, dict):
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            data = None
                        break

    if isinstance(data, dict) and data.get("tool"):
        return data
    return None


async def execute_tool(name: str, args: dict, db: AsyncSession) -> str:
    """执行指定工具，返回 JSON 字符串结果（供模型二次生成时引用）。"""
    if name == "query_jobs":
        return await _execute_query_jobs(db, args or {})
    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


async def _execute_query_jobs(db: AsyncSession, args: dict) -> str:
    """query_jobs 工具实现：按城市 / 关键词统计岗位数量并返回示例。"""
    city = str(args.get("city") or "").strip()
    keyword = str(args.get("keyword") or "").strip()

    dto = JobQueryDTO(
        keyword=keyword or None,
        city=city or None,
        page_num=1,
        page_size=5,
    )
    total = await JobService.count_jobs(db, dto)
    jobs = await JobService.query_jobs(db, dto)

    samples = []
    for j in jobs:
        salary = None
        if j.min_salary is not None and j.max_salary is not None:
            salary = f"{int(j.min_salary)}-{int(j.max_salary)}元"
        elif j.max_salary is not None:
            salary = f"≤{int(j.max_salary)}元"
        samples.append(
            {
                "title": j.title,
                "company": j.company_name,
                "salary": salary,
                "city": j.city,
            }
        )

    return json.dumps(
        {
            "total": total,
            "city": city or "全部城市",
            "keyword": keyword or "全部岗位",
            "samples": samples,
        },
        ensure_ascii=False,
    )
