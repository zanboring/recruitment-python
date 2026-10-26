"""打包完整性的回归测试。

**背景（这是一个真实缺陷的回归锁）**

``RecSys.exe`` 曾在干净机器上启动即崩：

    ModuleNotFoundError: No module named 'aiomysql'
    [PYI-xxxx:ERROR] Failed to execute script 'run_entry'

根因**不是**代码问题，而是**打包环境与运行环境不一致且无人校验**：

- ``app/database.py`` 里 ``db_type`` 默认 ``mysql``，连的是
  ``mysql+aiomysql://`` —— 方言驱动是 SQLAlchemy 在 ``create_engine`` 时
  **运行时动态 import** 的，PyInstaller 的静态分析看不到这条依赖；
- ``recsys.spec`` 已经把 ``aiomysql`` 写进 ``hiddenimports``，
  但 ``hiddenimports`` 只能「让打进去」，**不能凭空变出没安装的包**；
- 那次打包用的解释器只装了 ``pyinstaller`` 和 ``aiosqlite``，没装 ``aiomysql``
  → 静默打出一个缺驱动的产物 → 到目标机器才炸。

四个测试锁住四个环节，缺一不可：

1. ``test_清单包含三种数据库驱动`` —— 依赖清单里必须有它们
   （**否则按清单装出来的环境仍然是残缺的**，这是最上游的一环）
2. ``test_spec_的_hiddenimports_覆盖全部驱动`` —— spec 与代码里的方言保持一致
3. ``test_build_script_打包前后都做校验`` —— 构建脚本必须能自己发现环境残缺
4. ``test_数据库驱动是运行时动态导入的`` —— 解释**为什么**必须写 hiddenimports，
   防止有人「清理」掉看似没被引用的 hiddenimports
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 代码里支持的三种方言 → 对应驱动包名（见 app/database.py 的 DATABASE_URL 构造）
DIALECT_DRIVERS = {
    "mysql+aiomysql": "aiomysql",
    "postgresql+asyncpg": "asyncpg",
    "sqlite+aiosqlite": "aiosqlite",
}


def _read(rel: str) -> str:
    path = PROJECT_ROOT / rel
    assert path.exists(), f"缺少文件：{rel}"
    return path.read_text(encoding="utf-8")


def test_清单包含三种数据库驱动():
    """requirements.txt 必须列出全部异步驱动。

    缺任何一个的后果不是「用不了那个数据库」，而是
    **按这个清单装出来的打包环境会打出残缺的 exe** ——
    因为打包脚本正是按 requirements.txt 装依赖的。
    """
    content = _read("requirements.txt")
    # 只看生效行，避免注释里的包名造成假阳性
    active = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    declared = " ".join(active).lower()

    missing = [pkg for pkg in DIALECT_DRIVERS.values() if pkg not in declared]
    assert not missing, (
        f"requirements.txt 缺少数据库驱动：{missing}。\n"
        "按此清单安装的打包环境会打出启动即崩的 exe。"
    )


def test_spec_的_hiddenimports_覆盖全部驱动():
    """recsys.spec 的 hiddenimports 必须与代码支持的方言一一对应。

    两者脱节的典型场景：有人给 database.py 加了新方言（比如
    ``mssql+aioodbc``），但忘了同步 hiddenimports → 源码模式一切正常，
    打包产物到目标机器才崩。
    """
    spec = _read("recsys.spec")

    # 抽出 hiddenimports=[...] 这一段（spec 是 Python 字面量写法）
    match = re.search(r"hiddenimports\s*=\s*\[(.*?)\]", spec, re.S)
    assert match, "recsys.spec 里找不到 hiddenimports 列表"
    block = match.group(1)

    missing = [pkg for pkg in DIALECT_DRIVERS.values() if f"'{pkg}'" not in block]
    assert not missing, (
        f"recsys.spec 的 hiddenimports 缺少：{missing}。\n"
        "SQLAlchemy 方言驱动是运行时动态 import 的，静态分析扫不到，"
        "必须靠 hiddenimports 显式声明。"
    )


def test_build_script_打包前后都做校验():
    """build-exe.bat 必须自带「环境残缺」自检。

    这是本缺陷的**真正修复点**：脚本原先无条件执行
    ``pip install -r requirements.txt`` 后就打包，从不校验结果 ——
    即使 pip 失败（网络问题、源不可用）也会继续打出一个残缺的 exe。

    两个校验缺一不可：
    - 打包**前**：依赖可导入（拦住环境问题）
    - 打包**后**：驱动确实在 _internal 里（拦住 spec 配置问题）
    """
    script = _read("build-exe.bat")

    assert "import aiosqlite" in script and "aiomysql" in script, (
        "build-exe.bat 缺少打包前的依赖导入校验 —— "
        "环境残缺时会静默打出启动即崩的 exe"
    )
    assert "_internal" in script, (
        "build-exe.bat 缺少打包后的产物校验 —— "
        "依赖装了但没打进包的情况会被漏掉"
    )
    # 校验失败必须中断，不能只打印警告后继续打包
    assert "exit /b 1" in script, (
        "build-exe.bat 的校验失败后没有中止构建（缺 exit /b 1）"
    )


def test_数据库驱动是运行时动态导入的():
    """解释「为什么必须写 hiddenimports」—— 防止有人清理掉它们。

    ``app/database.py`` 里驱动名是**拼在 URL 字符串里**的
    （``f"mysql+aiomysql://..."``），文件里没有任何一处 ``import aiomysql``。
    所以：

    - 静态分析（PyInstaller / linter / 「未使用依赖」检查）都认为它没被用到；
    - 但 SQLAlchemy 在 ``create_engine`` 时会 import 它；
    - 于是「清理未使用依赖」这个看似安全的操作会打出一个崩掉的 exe。

    这个测试把「驱动名藏在 URL 里」这一事实固化成断言：
    哪天有人把 URL 改写成真 import，它会失败并提醒更新 spec。
    """
    db_py = _read("app/database.py")

    # 驱动名出现在 URL 拼装里（而不是 import 语句里）
    for dialect, driver in DIALECT_DRIVERS.items():
        assert driver in db_py, (
            f"app/database.py 里找不到驱动 {driver}（方言 {dialect}）。"
            "若方言或驱动有变更，请同步更新 recsys.spec 的 hiddenimports "
            "与本测试的 DIALECT_DRIVERS。"
        )

    # 确认确实没有顶层 import —— 这正是必须写 hiddenimports 的原因
    for driver in DIALECT_DRIVERS.values():
        pattern = rf"^\s*import\s+{driver}\b|^\s*from\s+{driver}\b"
        assert not re.search(pattern, db_py, re.M), (
            f"app/database.py 出现了 {driver} 的顶层 import —— "
            "这种情况下 PyInstaller 能自动发现依赖，"
            "本测试的前提（动态导入）已不成立，"
            "请更新 recsys.spec 的 hiddenimports 说明与本测试。"
        )


# ---------------------------------------------------------------------------
# 第二组：打包版的默认数据库必须是零依赖的 SQLite
# ---------------------------------------------------------------------------

FROZEN_PROBE = """
import os, sys
# 模拟 PyInstaller 运行环境：frozen + 可执行文件位于「新电脑」的某个目录
sys.frozen = True
sys.executable = os.path.join(sys.argv[1], "RecSys.exe")
open(sys.executable, "a").close()
from app.config import settings
from app.database import DATABASE_URL
print("DB_TYPE=" + settings.db_type)
print("DB_NAME=" + settings.db_name)
print("DB_URL=" + DATABASE_URL)
"""


def _probe_frozen(tmp_path):
    """在子进程里以 frozen 模式导入配置，返回解析出的键值。"""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", FROZEN_PROBE, str(tmp_path)],
        cwd=str(PROJECT_ROOT),
        # 清掉可能干扰的数据库环境变量，确保测的是「代码默认值」
        env={
            **{k: v for k, v in __import__("os").environ.items()
               if not k.upper().startswith(("DB_", "DATABASE"))},
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"frozen 模式导入配置失败：\n{result.stderr[-2000:]}"
    )
    parsed = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key.strip()] = value.strip()
    return parsed


def test_打包版默认用零依赖数据库(tmp_path):
    """打包版（frozen）在没有任何配置文件时，必须默认用 SQLite。

    **这是一个真实缺陷的回归锁。**

    `config.template.env` 里写着 `DB_TYPE=sqlite`、注释明说「零依赖、不需要安装
    MySQL」，但代码里 `db_type` 的默认值曾是 `mysql` —— 于是有一个没人覆盖到的
    区间：用户**还没把 config.env 放进去**就双击 exe（新用户的第一个动作）
    → 默认值指向 `mysql+aiomysql://localhost:3306` → 目标机器没有 MySQL → 启动失败。

    为什么之前没被发现：开发机上确实跑着 MySQL，源码模式连得上，
    所有测试也都是源码模式跑的 —— **这个缺陷只在打包产物上成立**。

    SQLite 是内置的（aiosqlite 已打进包），永远连得上，所以打包版必须默认它。
    """
    parsed = _probe_frozen(tmp_path)

    assert parsed.get("DB_TYPE") == "sqlite", (
        f"打包版默认数据库类型是 {parsed.get('DB_TYPE')!r}，应为 'sqlite'。\n"
        "绿色软件不能假设目标机器装了 MySQL —— 用户还没放 config.env 时就会连不上。"
    )
    assert parsed.get("DB_URL", "").startswith("sqlite"), (
        f"打包版默认 DATABASE_URL 不是 sqlite：{parsed.get('DB_URL')!r}"
    )


def test_打包版数据库落在可执行文件同目录(tmp_path):
    """库文件必须在 exe 同目录（绝对路径），而不是依赖启动时的 CWD。

    用相对路径（`./recsys.db`）时，库落到哪取决于**当前工作目录**：
    - 双击图标启动 → CWD 是 exe 目录（碰巧对）；
    - 从命令行在别处执行、或快捷方式改了「起始位置」→ 库落到别处，
      用户会以为「我的数据丢了」。

    绿色软件承诺的是「整个目录拷走即用」，所以必须是绝对路径。
    """
    exe_dir = tmp_path / "portable"
    exe_dir.mkdir()
    parsed = _probe_frozen(exe_dir)

    db_name = parsed.get("DB_NAME", "")
    assert Path(db_name).is_absolute(), (
        f"打包版的 db_name 必须是绝对路径，实际是 {db_name!r} —— "
        "相对路径会让库文件随启动目录漂移"
    )
    assert Path(db_name).parent == exe_dir, (
        f"库文件应落在 exe 同目录 {exe_dir}，实际落在 {Path(db_name).parent}"
    )
