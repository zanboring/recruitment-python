# -*- coding: utf-8 -*-
"""视觉识别服务单元测试：JSON 解析容错 / 字段规整 / 无 key 报错。"""
import pytest

from app.services import vision_service
from app.config import settings


def test_parse_json_plain():
    data = vision_service._parse_json_reply('{"title": "Python工程师", "city": "长沙"}')
    assert data["title"] == "Python工程师"
    assert data["city"] == "长沙"


def test_parse_json_with_markdown_fence():
    reply = '```json\n{"title": "AI应用工程师", "salary_min_k": 20}\n```'
    data = vision_service._parse_json_reply(reply)
    assert data["title"] == "AI应用工程师"
    assert data["salary_min_k"] == 20


def test_parse_json_with_surrounding_text():
    reply = '好的，识别结果如下：{"title": "Java开发", "city": "深圳"} 请查收。'
    data = vision_service._parse_json_reply(reply)
    assert data["title"] == "Java开发"
    assert data["city"] == "深圳"


def test_parse_json_invalid_raises():
    with pytest.raises(ValueError):
        vision_service._parse_json_reply("这不是 JSON")


def test_parse_json_empty_raises():
    with pytest.raises(ValueError):
        vision_service._parse_json_reply("")


# 真实模型返回样本（2026-11-04 用 GLM-4V-Flash 实拍抓取，**不要"美化"它**）：
# 三个特征同时出现——Markdown 围栏、缩进 4 空格、最后一个字段后带尾逗号。
# 这条用例存在的意义：之前所有用例喂的都是手写的标准 JSON，于是线上必然复现的
# 「尾逗号导致整条视觉导入失败」在测试里一次都没暴露过。
REAL_MODEL_REPLY = """```json
{
    "title": "AI应用开发工程师",
    "company_name": "深圳智元科技有限公司",
    "city": "深圳",
    "salary_min_k": 25,
    "salary_max_k": 40,
    "experience": "3-5年",
    "education": "本科",
    "skills": "Python, FastAPI, RAG, LangChain, Milvus, K8s",
    "description": "负责AI应用后端服务设计与开发。",
}
```"""


def test_parse_real_model_reply_with_trailing_comma():
    """真实返回带尾逗号时仍能解析（否则视觉导入 100% 失败）。"""
    data = vision_service._parse_json_reply(REAL_MODEL_REPLY)
    assert data["title"] == "AI应用开发工程师"
    assert data["salary_min_k"] == 25
    assert data["salary_max_k"] == 40
    assert data["skills"].startswith("Python")


def test_parse_json_trailing_comma_inside_array():
    """尾逗号出现在数组里同样要容忍。"""
    reply = '{"skills": ["Python", "FastAPI", ], "city": "深圳"}'
    data = vision_service._parse_json_reply(reply)
    assert data["skills"] == ["Python", "FastAPI"]
    assert data["city"] == "深圳"


def test_parse_json_truncated_still_raises():
    """尾逗号容错不能顺带把"真的坏了"的输出也放过去。"""
    with pytest.raises(ValueError):
        vision_service._parse_json_reply('{"title": "X", "city": ')


def test_to_int_handles_k_suffix():
    assert vision_service._to_int("20K") == 20
    assert vision_service._to_int("20") == 20
    assert vision_service._to_int(18.5) == 18
    assert vision_service._to_int(None) == 0
    assert vision_service._to_int("abc") == 0


def test_normalize_job_converts_salary():
    raw = {
        "title": "Python工程师",
        "company_name": "某公司",
        "city": "长沙",
        "salary_min_k": 15,
        "salary_max_k": 25,
        "experience": "3-5年",
        "education": "本科",
        "skills": "Python, FastAPI",
        "description": "负责后端开发",
    }
    job = vision_service._normalize_job(raw)
    assert job["min_salary"] == 15000
    assert job["max_salary"] == 25000
    assert job["source_site"] == "vision"
    assert job["skills"] == "Python, FastAPI"


def test_normalize_job_fallback_salary():
    """只识别出上限（或只有下限）时，视为两者相等。"""
    raw = {"title": "T", "salary_min_k": 20, "salary_max_k": None}
    job = vision_service._normalize_job(raw)
    assert job["min_salary"] == 20000
    assert job["max_salary"] == 20000


def test_recognize_requires_key(monkeypatch):
    """未配置 ZHIPUAI_API_KEY 时应给出明确提示（不发起网络请求）。"""
    monkeypatch.setattr(settings, "zhipuai_api_key", "")
    with pytest.raises(RuntimeError, match="ZHIPUAI_API_KEY"):
        asyncio_run(vision_service.recognize_job_image(b"fake-image"))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)