"""文档契约检查：**必须真跑**才能拿到的两个数字。

为什么需要这个脚本
------------------
文档里的下面两个数字，靠读代码 / 数文件都算不准，只能真跑：

1. **REST 端点数**（`docs/showcase.md` 写「104 个端点，其中 103 个在 `/api` 下」）。
   取数必须用 ``TestClient`` 的**上下文管理器**：驼峰兼容路由
   （``app/routers/compat.py``）是在 startup 阶段才挂载到 app 上的。
   直接 ``create_app().openapi()`` 只能拿到 89 条 —— 曾有人按 89 去「校正」文档，
   把一个**对的**数字改成了错的。
2. **测试用例数**（形如 ``937 tests collected``）。不等于 ``grep -c "def test"``：
   ``parametrize`` 会把一个函数展开成多条用例，读源码永远数不对。

输出与退出码
------------
逐个打印「文档值 vs 实测值」；任一不一致 → 退出码 1（CI 里作为独立一步跑）。
跑完不留文件：应用库落在临时目录，结束即删；pytest 带 ``-p no:cacheprovider``。

注意：本脚本只做「数字对不对」，**不**重复那些读文档就能判的断言
（那些在 ``tests/test_docs_contract.py`` 里，跑 pytest 时一并检查）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = REPO_ROOT / "docs" / "showcase.md"

# 直接 `python scripts/check_docs.py` 时 sys.path[0] 是 scripts/（不是仓库根），
# 裸 import app 会 ModuleNotFoundError —— 与 CI 里裸 pytest 的坑同源，这里自带兜底。
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 文档里出现数字的行文格式；正则留出空白弹性，但**匹配不到就是失败**
_ENDPOINT_TOTAL_RE = re.compile(r"\*\*(\d+)\s*个 REST 端点\*\*")
_ENDPOINT_API_RE = re.compile(r"\*\*(\d+)\s*个在\s*`?/api`?\s*下\*\*")
_TEST_TOTAL_RE = re.compile(r"\*\*(\d+)\s*项，全部通过\*\*")
_TEST_TOTAL_GREEN_RE = re.compile(r"(\d+)\s*项全绿")


class DocContractError(AssertionError):
    """文档与实测不一致。单列一类，便于调用方区分「断言失败」与「脚本崩了」。"""


# ------------------------------------------------------------------ 文档取值


def documented_endpoints(text: str) -> Tuple[int, int]:
    """从 showcase 文本里取出 (端点总数, /api 下的端点数)。

    防真空跑：正则必须各命中**恰好一次**。文档改措辞导致匹配不到时，
    这里直接抛错 —— 而不是「没找到就当 0，然后和 0 比得出相等」。
    """
    totals = _ENDPOINT_TOTAL_RE.findall(text)
    api = _ENDPOINT_API_RE.findall(text)
    if len(totals) != 1 or len(api) != 1:
        raise DocContractError(
            f"{SHOWCASE}: 端点数字声明应各出现 1 次，实际 "
            f"总数 {len(totals)} 次 / /api {len(api)} 次 —— 文档措辞变了？断言已失效，请同步正则"
        )
    return int(totals[0]), int(api[0])


def documented_test_total(text: str) -> List[int]:
    """从 showcase 文本里取出**所有**测试数声明（正文一处、快速开始一处）。"""
    found = [int(n) for n in _TEST_TOTAL_RE.findall(text)]
    found += [int(n) for n in _TEST_TOTAL_GREEN_RE.findall(text)]
    if not found:
        raise DocContractError(
            f"{SHOWCASE}: 没找到任何测试数声明（形如「937 项，全部通过」）—— 断言已失效"
        )
    return found


# ------------------------------------------------------------------ 实测取值


def count_endpoints() -> Dict[str, int]:
    """起应用（TestClient 上下文管理器）后读 /openapi.json，数端点。

    ``DB_NAME`` 必须指向临时目录**且在 import app 之前**设置：应用的 startup
    会 ``create_all`` + 播种管理员，默认落到 ``data/recsys.db``；
    不隔离就会把二进制库文件塞进仓库根目录。
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="docs-check-"))
    try:
        # 必须在 import app.config / app.main 之前设置，否则 settings 已经定型
        os.environ["DB_TYPE"] = "sqlite"
        os.environ["DB_NAME"] = str(tmpdir / "docs-check.db")
        os.environ.setdefault("JWT_SECRET", "docs-check-only-secret-at-least-32-chars")

        from fastapi.testclient import TestClient

        from app.main import create_app

        # 🔴 必须用 with（上下文管理器）—— 驼峰兼容路由在 startup 阶段才挂载
        with TestClient(create_app()) as client:
            paths = set(client.get("/openapi.json").json()["paths"])

        return {
            "total": len(paths),
            "api": len([p for p in paths if p.startswith("/api")]),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def count_collected_tests() -> int:
    """真跑 ``pytest --collect-only -q``，取末行的用例总数。

    不递归调用 pytest 的测试用例（只有本脚本会启动子进程，pytest 内部不调用它）。
    ``-p no:cacheprovider`` 避免在只读 / 无权限目录里写缓存。
    """
    env = dict(os.environ)
    # 与 CI / 本地全量同一套降级配置：不联网、不依赖 Redis / Ollama
    env.setdefault("RATE_LIMIT_BACKEND", "memory")
    env.setdefault("CIRCUIT_BREAKER_BACKEND", "memory")
    env.setdefault("QUOTA_BACKEND", "memory")
    env.setdefault("SESSION_BACKEND", "memory")
    env.setdefault("OLLAMA_ENABLED", "false")
    env.setdefault("JWT_SECRET", "docs-check-only-secret-at-least-32-chars")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = proc.stdout + proc.stderr
    match = re.search(r"(\d+) tests? collected", output)
    if not match:
        raise DocContractError(
            f"pytest --collect-only 的输出里没找到用例总数（退出码 {proc.returncode}）：\n"
            f"{output[-2000:]}"
        )
    return int(match.group(1))


# ------------------------------------------------------------------ 汇总


def _report(label: str, documented: int, measured: int, must_equal: bool = True) -> bool:
    ok = (documented == measured) if must_equal else (measured >= documented)
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {label}：文档值 {documented} / 实测值 {measured}")
    return ok


def main() -> int:
    text = SHOWCASE.read_text(encoding="utf-8")
    doc_total, doc_api = documented_endpoints(text)
    doc_tests = documented_test_total(text)

    measured = count_endpoints()
    collected = count_collected_tests()

    ok_total = _report("REST 端点总数", doc_total, measured["total"])
    ok_api = _report("/api 下的端点数", doc_api, measured["api"])
    # 测试数取「不少于」：文档写的是当时的实测基线，后续新增用例不该让文档变错；
    # 但**变少**说明有用例被删 —— 那必须红（这正是本轮要守的东西）。
    ok_tests = [
        _report("测试用例数（声明）", n, collected, must_equal=False) for n in doc_tests
    ]

    if not (ok_total and ok_api and all(ok_tests)):
        print("\n文档与实测不一致：请改 docs/showcase.md 里的数字（不要改脚本）")
        return 1
    print("\n文档契约（必须真跑的部分）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
