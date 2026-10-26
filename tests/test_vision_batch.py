# -*- coding: utf-8 -*-
"""批量视觉导入测试：mock 视觉识别，验证异常隔离 + 批量入库。"""
import pytest

from app.services import vision_service


class _FakeRecognize:
    """可编程的 mock：按图片序号返回成功/失败。"""

    def __init__(self, results: dict):
        self.results = results  # {index: (ok, job_or_error)}
        self.calls = []

    async def __call__(self, image_bytes, mime="image/jpeg"):
        # 简易：用字节长度区分序号（测试构造不同长度图片）
        idx = len(image_bytes) % 100
        self.calls.append(idx)
        ok, val = self.results.get(idx, (False, "未识别到岗位信息"))
        if ok:
            return val
        raise RuntimeError(val)


@pytest.mark.asyncio
async def test_batch_mixed_success_failure(monkeypatch):
    """混合场景：一张成功一张失败，成功条返回结果、失败条带错误，互不影响。"""
    job_a = {"title": "Python工程师", "company_name": "A公司", "city": "长沙",
             "experience": "3-5年", "education": "本科", "min_salary": 20000,
             "max_salary": 30000, "skills": "Python", "description": "开发"}
    fake = _FakeRecognize({1: (True, job_a), 2: (False, "未识别到岗位信息")})
    monkeypatch.setattr(vision_service, "recognize_job_image", fake)

    images = [
        {"index": 1, "bytes": b"A" * 101, "mime": "image/jpeg"},
        {"index": 2, "bytes": b"B" * 202, "mime": "image/jpeg"},
    ]
    results = await vision_service.recognize_job_images_batch(images)

    assert len(results) == 2
    ok_one = next(r for r in results if r["index"] == 1)
    bad_one = next(r for r in results if r["index"] == 2)
    assert ok_one["ok"] is True
    assert ok_one["result"]["title"] == "Python工程师"
    assert bad_one["ok"] is False
    assert "未识别到岗位信息" in bad_one["error"]


@pytest.mark.asyncio
async def test_batch_empty_raises():
    """空列表应抛 RuntimeError（整体性错误）。"""
    with pytest.raises(RuntimeError, match="未收到任何图片"):
        await vision_service.recognize_job_images_batch([])


@pytest.mark.asyncio
async def test_batch_exception_isolated(monkeypatch):
    """单张意外异常（非 RuntimeError）也不拖垮整批。"""
    job_a = {"title": "Java工程师", "company_name": "B公司", "city": "深圳",
             "experience": "", "education": "", "min_salary": None,
             "max_salary": None, "skills": "", "description": ""}

    async def boom(image_bytes, mime="image/jpeg"):
        if len(image_bytes) % 100 == 1:
            raise ValueError("网络抖动")
        return job_a

    monkeypatch.setattr(vision_service, "recognize_job_image", boom)
    images = [
        {"index": 1, "bytes": b"X" * 101, "mime": "image/jpeg"},
        {"index": 2, "bytes": b"Y" * 202, "mime": "image/jpeg"},
    ]
    results = await vision_service.recognize_job_images_batch(images)
    assert results[0]["ok"] is False
    assert results[1]["ok"] is True
    assert results[1]["result"]["title"] == "Java工程师"
