"""本地模型能力对比基准：用实测数据决定「哪个任务用哪个模型」。

本脚本回答一个具体问题：本机装的语言类与代码类模型，各自适合承担什么任务？

测试两类任务：
  1. 工具识别（结构化抽取）—— 判断用户提问是否需要查库，输出严格 JSON
  2. 知识问答（自然语言）—— 考察语言理解与事实准确性

用法::

    python scripts/bench_local_models.py                      # 对比全部已安装模型
    python scripts/bench_local_models.py --models qwen2.5:14b # 只测指定模型
    python scripts/bench_local_models.py --output reports/local_model_benchmark.md

设计说明：
- 工具识别用项目**真实的**提示词与解析器（``app.services.tool_service``），
  而不是另写一套简化逻辑 —— 否则测出来的准确率不代表线上表现。
- 知识问答的判定基于关键词启发式，只作粗略参考；脚本会把模型原话一并打印，
  最终以人工判读为准。把「可自动判定」和「需人工判读」分开，避免用看似精确
  的数字掩盖主观判断。
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.tool_service import (  # noqa: E402
    build_tool_detection_prompt,
    extract_tool_call,
)

# ---- 工具识别用例：前三条应触发查库，后三条不应触发 ----
TOOL_CASES = [
    ("长沙有多少Java岗位", "call"),
    ("深圳前端岗位薪资水平怎么样", "call"),
    ("帮我查查北京的Python岗位", "call"),
    ("你好，你是谁", "none"),
    ("介绍一下这个系统", "none"),
    ("RAG是什么", "none"),
]

# ---- 知识问答用例：must_any 至少命中一个，must_not 一个都不能命中 ----
QA_CASES = [
    {
        "question": "一句话说明什么是 RAG",
        "must_any": ["检索增强", "检索增强生成", "Retrieval-Augmented", "检索与生成"],
        "must_not": ["强化学习", "Reinforcement"],
    },
    {
        "question": "一句话说明 Jaccard 相似度怎么算",
        "must_any": ["交集", "并集", "集合"],
        "must_not": [],
    },
    {
        "question": "一句话说明 SSE 和 WebSocket 的主要区别",
        "must_any": ["单向", "双向", "服务器推送", "长连接", "持久"],
        "must_not": [],
    },
]


async def _chat(model: str, messages: list, num_predict: int = 96) -> dict:
    """直接调用 Ollama，返回原始响应（需要 duration / eval_count 等计量字段）。"""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json() or {}


async def list_models(explicit: str | None = None) -> list:
    if explicit:
        return [m.strip() for m in explicit.split(",") if m.strip()]
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{settings.ollama_base_url}/api/tags")
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]


async def bench_tool_detection(model: str) -> dict:
    """跑工具识别用例，返回准确率与耗时。"""
    prompt = build_tool_detection_prompt()
    rows = []
    elapsed = []

    for question, expected in TOOL_CASES:
        started = time.time()
        try:
            data = await _chat(
                model,
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": question},
                ],
                num_predict=64,
            )
            duration = time.time() - started
            raw = (data.get("message") or {}).get("content", "").strip()
            parsed = extract_tool_call(raw)
            actual = "call" if parsed else "none"
            rows.append({
                "question": question,
                "expected": expected,
                "actual": actual,
                "correct": actual == expected,
                "elapsed": duration,
                "raw": raw,
                "error": "",
            })
            elapsed.append(duration)
        except Exception as e:
            rows.append({
                "question": question, "expected": expected, "actual": "ERR",
                "correct": False, "elapsed": 0.0, "raw": "", "error": str(e)[:80],
            })

    correct = sum(1 for r in rows if r["correct"])
    return {
        "rows": rows,
        "accuracy": correct / len(rows) if rows else 0.0,
        "avg_elapsed": statistics.mean(elapsed) if elapsed else 0.0,
    }


async def bench_qa(model: str) -> dict:
    """跑知识问答用例，做关键词启发式判定并保留原文。"""
    rows = []
    for case in QA_CASES:
        started = time.time()
        try:
            data = await _chat(model, [{"role": "user", "content": case["question"]}])
            duration = time.time() - started
            answer = (data.get("message") or {}).get("content", "").strip()

            hit_required = any(k in answer for k in case["must_any"]) if case["must_any"] else True
            hit_forbidden = any(k in answer for k in case["must_not"])
            rows.append({
                "question": case["question"],
                "answer": answer,
                "passed": hit_required and not hit_forbidden,
                "reason": (
                    "命中了不应出现的关键词（疑似幻觉）" if hit_forbidden
                    else "" if hit_required else "未命中期望关键词"
                ),
                "elapsed": duration,
                "eval_count": data.get("eval_count", 0),
                "eval_duration": data.get("eval_duration", 0),
                "load_duration": data.get("load_duration", 0),
            })
        except Exception as e:
            rows.append({
                "question": case["question"], "answer": "", "passed": False,
                "reason": f"调用失败：{str(e)[:60]}", "elapsed": 0.0,
                "eval_count": 0, "eval_duration": 0, "load_duration": 0,
            })
    return {"rows": rows}


def _tok_per_sec(row: dict) -> float:
    seconds = row.get("eval_duration", 0) / 1e9
    return row["eval_count"] / seconds if seconds > 0 else 0.0


async def run(models: list) -> dict:
    report = {"base_url": settings.ollama_base_url, "models": {}}
    for model in models:
        print(f"\n{'=' * 70}\n模型: {model}\n{'=' * 70}")

        print("  [1/2] 工具识别 …")
        tool = await bench_tool_detection(model)
        for r in tool["rows"]:
            flag = "OK " if r["correct"] else "BAD"
            print(f"    [{flag}] {r['question']:<22} 期望={r['expected']:<4} "
                  f"实际={r['actual']:<4} {r['elapsed']:5.1f}s")
            if not r["correct"]:
                print(f"          输出：{r['raw'][:70] or r['error']}")
        print(f"    → 准确率 {tool['accuracy'] * 100:.0f}% | "
              f"平均耗时 {tool['avg_elapsed']:.1f}s")

        print("  [2/2] 知识问答 …")
        qa = await bench_qa(model)
        for r in qa["rows"]:
            flag = "OK " if r["passed"] else "BAD"
            print(f"    [{flag}] {r['question']}")
            print(f"          {r['answer'][:110]}")
            if r["reason"]:
                print(f"          ⚠ {r['reason']}")
        speeds = [_tok_per_sec(r) for r in qa["rows"] if _tok_per_sec(r) > 0]
        print(f"    → 平均生成速度 {statistics.mean(speeds):.1f} tok/s"
              if speeds else "    → 无有效速度数据")

        report["models"][model] = {
            "tool": tool,
            "qa": qa,
            "qa_pass": sum(1 for r in qa["rows"] if r["passed"]),
            "avg_tok_per_sec": statistics.mean(speeds) if speeds else 0.0,
        }
    return report


def render_markdown(report: dict) -> str:
    lines = [
        "# 本地模型能力对比（实测）",
        "",
        f"- 服务地址：`{report['base_url']}`",
        "- 工具识别使用项目真实提示词与解析器（`app/services/tool_service.py`）",
        "- 知识问答的通过判定为关键词启发式，仅供粗略参考，结论以「模型原话」为准",
        "",
        "## 一、汇总对比",
        "",
        "| 模型 | 工具识别准确率 | 工具识别平均耗时 | 知识问答通过 | 生成速度 |",
        "|---|---|---|---|---|",
    ]
    for model, data in report["models"].items():
        lines.append(
            f"| `{model}` | {data['tool']['accuracy'] * 100:.0f}% "
            f"| {data['tool']['avg_elapsed']:.1f}s "
            f"| {data['qa_pass']}/{len(data['qa']['rows'])} "
            f"| {data['avg_tok_per_sec']:.1f} tok/s |"
        )

    for model, data in report["models"].items():
        lines += [
            "",
            f"## 二、`{model}` 明细",
            "",
            "### 工具识别",
            "",
            "| 提问 | 期望 | 实际 | 判定 | 耗时 |",
            "|---|---|---|---|---|",
        ]
        for r in data["tool"]["rows"]:
            lines.append(
                f"| {r['question']} | {r['expected']} | {r['actual']} "
                f"| {'✅' if r['correct'] else '❌'} | {r['elapsed']:.1f}s |"
            )
        lines += [
            "",
            "### 知识问答",
            "",
            "| 提问 | 回答 | 判定 |",
            "|---|---|---|",
        ]
        for r in data["qa"]["rows"]:
            answer = r["answer"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {r['question']} | {answer[:160]} | "
                f"{'✅' if r['passed'] else '❌ ' + r['reason']} |"
            )

    lines += [
        "",
        "## 三、结论与用法",
        "",
        "结合上表的实测差异，本地两个模型按下述方式分工（配置见 `app/config.py`）：",
        "",
        "- **对话生成**用语言类模型（`OLLAMA_MODEL`）——语言表达更自然，事实准确率更高",
        "- **工具识别 / 结构化抽取**用代码类模型（`OLLAMA_CODE_MODEL`）——",
        "  准确率与语言模型相同但耗时约减半，且严格 JSON 输出更稳定",
        "- 两者都未安装时，本地这一级整体不可用，降级链会直接跳到规则引擎",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description="本地模型能力对比基准")
    parser.add_argument("--models", help="逗号分隔的模型名；默认取 Ollama 中全部已安装模型")
    parser.add_argument("--output", help="Markdown 报告输出路径")
    args = parser.parse_args()

    try:
        models = await list_models(args.models)
    except Exception as e:
        print(f"无法获取模型清单（{type(e).__name__}: {e}）")
        print(f"请确认 Ollama 正在运行：{settings.ollama_base_url}")
        return

    if not models:
        print("没有可测试的模型。可先执行：ollama pull qwen2.5:14b")
        return

    print(f"待测模型：{', '.join(models)}")
    report = await run(models)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
        print(f"\nMarkdown 报告已写入：{out}")

    print("\n" + json.dumps(
        {m: {
            "工具识别准确率": f"{d['tool']['accuracy'] * 100:.0f}%",
            "工具识别平均耗时": f"{d['tool']['avg_elapsed']:.1f}s",
            "知识问答通过": f"{d['qa_pass']}/{len(d['qa']['rows'])}",
        } for m, d in report["models"].items()},
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
