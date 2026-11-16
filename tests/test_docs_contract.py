"""文档契约：**不需要起进程**就能判的断言（起进程的那两个数字在 `scripts/check_docs.py`）。

为什么单独一个文件
------------------
这个项目反复栽在「文档承诺大于实现」上：这次 1.1.0 的 CHANGELOG 甚至把两项
**已经实现**的能力（robots.txt 门禁、油猴采集通道）写成「只是方向 / 从未被调用」。
所以这里把文档里的**数字**与**实现指认**钉成断言：文档一漂移，pytest 就变红。

每条断言都带防真空跑保护：正则没匹配到任何声明时**失败**（"断言已失效"），
而不是「没找到 → 不比较 → 假装通过」。失败信息统一带 文件路径 + 期望值 + 实测值，
让下一个人知道该改哪儿。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
README = REPO_ROOT / "README.md"
SHOWCASE = REPO_ROOT / "docs" / "showcase.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"文档不存在：{path}（本文档契约测试依赖它，缺失即失败）"
    return path.read_text(encoding="utf-8")


def _load_check_docs():
    """按文件路径加载 `scripts/check_docs.py`，复用它的纯函数与实测计数。

    不走 `import`：`scripts/` 不是包（没有 `__init__.py`）；
    **也不**在 pytest 里 subprocess 再跑一次 pytest —— 那会递归收集本文件、还会超时。
    """
    script = REPO_ROOT / "scripts" / "check_docs.py"
    assert script.is_file(), f"契约脚本不存在：{script}（test_docs_contract 依赖它）"
    spec = importlib.util.spec_from_file_location("_check_docs_contract", script)
    assert spec is not None and spec.loader is not None, f"无法按路径加载：{script}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK_DOCS = _load_check_docs()

# `## [1.1.0] - 2026-10-29` / `## 1.2.3` 两种写法都认
_VERSION_HEADING_RE = re.compile(r"^##\s*\[?(\d+\.\d+\.\d+)\]?", re.MULTILINE)
_DECLARED_TEST_FILES_RE = re.compile(r"(\d+)\s*个测试文件")
_TABLE_TEST_FILE_RE = re.compile(r"`(test_[0-9A-Za-z_]+\.py)`")
_REPO_PATH_RE = re.compile(r"`((?:docs|docker|scripts|userscript|app|tests|frontend)/[^`\s]+)`")


# --------------------------------------------------------------- 版本 / 测试数


def test_app_version_is_the_latest_changelog_section():
    """`app/version.py::APP_VERSION` 必须**就是**最新版本小节的标题，不是"某处出现过"。"""
    from app.version import APP_VERSION

    headings = _VERSION_HEADING_RE.findall(_read(CHANGELOG))
    assert headings, (
        f"{CHANGELOG}: 没匹配到任何「## [x.y.z]」版本小节标题 —— 文档措辞变了？断言已失效"
    )
    latest = headings[0]
    assert latest == APP_VERSION, (
        f"{CHANGELOG}: 最新版本小节是 [{latest}]，而 app/version.py 的 APP_VERSION 是 {APP_VERSION}。"
        f"期望 {APP_VERSION}（发版时两处必须一致，否则更新检查永远提示「已是最新」），实测 {latest}"
    )


def test_showcase_test_file_count_matches_disk():
    """`docs/showcase.md` 声明「N 个测试文件」必须等于 `tests/test_*.py` 的实际个数。"""
    declared = [int(n) for n in _DECLARED_TEST_FILES_RE.findall(_read(SHOWCASE))]
    assert declared, (
        f"{SHOWCASE}: 没找到「N 个测试文件」声明 —— 断言已失效，请同步本文件的正则"
    )
    measured = len(list(TESTS_DIR.glob("test_*.py")))
    for n in declared:
        assert n == measured, (
            f"{SHOWCASE}: 声明 {n} 个测试文件，实测 tests/test_*.py = {measured} 个。"
            f"期望 {measured}（新增/删除测试文件后请同步文档）"
        )


# --------------------------------------------------------------- 必须真跑的数字


def test_showcase_endpoint_counts_match_runtime():
    """`docs/showcase.md` 的「104 个端点 / 103 个在 /api 下」必须等于**服务真正启动后**的数。

    取数逻辑在 `scripts/check_docs.py::count_endpoints`（`TestClient` 上下文管理器，
    否则驼峰兼容路由没挂载，只会数到 89 条）。
    """
    doc_total, doc_api = CHECK_DOCS.documented_endpoints(_read(SHOWCASE))
    measured = CHECK_DOCS.count_endpoints()
    assert doc_total == measured["total"], (
        f"{SHOWCASE}: 声明 {doc_total} 个 REST 端点，"
        f"实测（启动后 /openapi.json）{measured['total']} 个"
    )
    assert doc_api == measured["api"], (
        f"{SHOWCASE}: 声明 {doc_api} 个端点以 /api 开头，实测 {measured['api']} 个"
    )


# --------------------------------------------------------------- 引用完整性


def test_readme_test_table_lists_existing_files():
    """README 测试清单表格里出现的每个 `test_xxx.py` 都必须真实存在于 `tests/`。"""
    rows = [ln for ln in _read(README).splitlines() if ln.startswith("|") and "`test_" in ln]
    names: List[str] = []
    for row in rows:
        names.extend(_TABLE_TEST_FILE_RE.findall(row))
    assert len(names) >= 20, (
        f"{README}: 测试清单表格只解析到 {len(names)} 个 `test_xxx.py` —— "
        f"表格措辞变了？断言已失效（正常应有 20+ 个）"
    )
    missing = sorted({n for n in names if not (TESTS_DIR / n).is_file()})
    assert not missing, (
        f"{README}: 测试清单表格引用了不存在的文件 {missing}。"
        f"期望它们都存在于 {TESTS_DIR}"
    )


def _ref_exists(ref: str) -> bool:
    if "*" in ref:  # 形如 app/crawlers/specs/*.yaml 的通配引用
        return any(True for _ in REPO_ROOT.glob(ref))
    return (REPO_ROOT / ref).exists()


@pytest.mark.parametrize("doc", [README, SHOWCASE], ids=["readme", "showcase"])
def test_referenced_repo_paths_exist(doc: Path):
    """文档里反引号包着的仓库内路径（docs/xxx.md、docker/xxx、scripts/xxx.py…）必须都存在。"""
    refs = _REPO_PATH_RE.findall(_read(doc))
    assert len(refs) >= 5, (
        f"{doc}: 只解析到 {len(refs)} 个仓库内路径引用 —— 断言已失效（正常应有 5+ 个）"
    )
    missing = sorted({p for p in refs if not _ref_exists(p)})
    assert not missing, (
        f"{doc}: 引用了不存在的仓库路径 {missing}（期望相对仓库根 {REPO_ROOT} 存在）"
    )


# --------------------------------------------------------------- 假陈述回归


_STALE_CLAIMS = (
    "这两条**没有实现**",
    "从未被任何调用方使用",
)


def test_changelog_has_no_stale_not_implemented_claims():
    """CHANGELOG 不得再出现「已实现却说没做」的原句（本轮 1.1.0 的两处假陈述）。

    这两句是历史上真实出现过的原文，钉成回归项：任何人把它们加回去，这里就红。
    """
    text = _read(CHANGELOG)
    assert "更正" in text, (
        f"{CHANGELOG}: 没找到「更正」小节 —— 本断言扫的是整篇 CHANGELOG，"
        f"先确认上面两项的更正说明还在"
    )
    hits = [claim for claim in _STALE_CLAIMS if claim in text]
    assert not hits, (
        f"{CHANGELOG}: 仍含「已实现却说没做」的陈述 {hits}。"
        f"期望：改为指认实现位置（见 test_changelog_points_to_real_implementations）"
    )


_IMPLEMENTATIONS = {
    "userscript/recsys-collector.user.js": "油猴采集通道",
    "app/crawlers/robots.py": "robots.txt 解析与门禁",
    "tests/test_browser_ingest.py": "油猴通道用例",
    "tests/test_throttle.py": "robots 门禁用例",
}


def test_changelog_points_to_real_implementations():
    """更正小节里指认的实现路径，既要写在文档里，也必须在仓库里真的存在。"""
    text = _read(CHANGELOG)
    missing_in_doc = [p for p in _IMPLEMENTATIONS if p not in text]
    assert not missing_in_doc, (
        f"{CHANGELOG}: 更正小节没有指认这些实现位置 {missing_in_doc}（期望它们出现在文中）"
    )
    missing_on_disk = [p for p in _IMPLEMENTATIONS if not (REPO_ROOT / p).exists()]
    assert not missing_on_disk, (
        f"{CHANGELOG}: 指认的实现文件在仓库里不存在 {missing_on_disk}"
    )


def test_robots_gate_is_actually_wired():
    """CHANGELOG 说 robots 门禁已在调用点生效 —— 由**代码**证明，不靠文档自证。"""
    service = REPO_ROOT / "app" / "services" / "crawler_service.py"
    source = _read(service)
    assert "robots.gate(" in source, (
        f"{service}: 没找到 `robots.gate(` 调用点 —— CHANGELOG 声称「robots 已实际接入」，"
        f"若调用被删掉，文档表述也必须改回去"
    )


# ------------------------------------------- 发布前敏感扫描：本机绝对路径门禁
#
# 这道判据原本只存在于 `sync-public.sh`（仓库外，不进版本控制），要到**发布时**
# 才跑。于是"文档里粘了一条本机命令"这件事，一路绿灯到最后一步才炸：
# 2026-11-16 的 rag-evaluation-history.md 就是把复现命令连项目解释器路径一起粘了
# 进去，导出 301 个文件、扫描到第 6 步才中止，白跑一遍发布。
#
# 把同一组模式下沉到测试层：判据与被判对象同层，发布前就能红。
# 模式刻意与脚本保持一致，否则会在这里通过、在发布时仍被拦。
_MACHINE_PATH_PATTERNS = (
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/]Administrator"), "Windows 用户目录"),
    (re.compile(r"[A-Za-z]:[\\/]Dev[\\/]recruitment"), "项目本机路径"),
    (re.compile(r"/[a-z]/Users/Administrator"), "Git Bash 形式的用户目录"),
    (re.compile(r"/[a-z]/Dev/recruitment"), "Git Bash 形式的项目路径"),
)

# 这些文档在 `sync-public.sh` 的 EXCLUDES 里，本来就不进公开版，
# 扫它们只会制造假红（内部协作说明写本机路径是有意的）。
_INTERNAL_DOCS = frozenset(
    {
        "AGENTS.md",
        "HANDOFF.md",
        "docs/AGENT-ROSTER.md",
        "docs/AGENT-WORKFLOW.md",
        "docs/onboarding.md",
        "docs/interview-prep.md",
        "docs/demo-playbook.md",
    }
)
_SCAN_DIRS = ("docs", "app", "scripts", "tests", "frontend/src")
_SCAN_SUFFIXES = frozenset({".md", ".py", ".ts", ".vue", ".yml", ".yaml"})


def _public_surface_files():
    """会进公开版的文本文件（近似集合：根目录 md + 主要源码/文档目录）。"""
    candidates = list(REPO_ROOT.glob("*.md"))
    for rel in _SCAN_DIRS:
        candidates.extend((REPO_ROOT / rel).rglob("*"))
    return [
        p
        for p in candidates
        if p.is_file()
        and p.suffix in _SCAN_SUFFIXES
        and "node_modules" not in p.parts
        and p.relative_to(REPO_ROOT).as_posix() not in _INTERNAL_DOCS
    ]


def test_no_machine_absolute_paths_in_public_surface():
    """仓库里不得出现本机绝对路径 —— 公开版快照的敏感扫描会因此中止发布。"""
    hits = []
    for path in _public_surface_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in _MACHINE_PATH_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append(f"{rel}:{line_no}  {label}  {match.group(0)}")
    assert not hits, (
        "以下位置出现了本机绝对路径，公开版敏感扫描会中止发布。\n"
        "改成相对路径，或在命令前后注明「在项目根目录下执行」：\n  "
        + "\n  ".join(hits)
    )
