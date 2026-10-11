"""源码卫生守卫 —— 防止不可见字符污染（双机 + 多编辑器协作的典型风险）。

背景：本项目由**两台机器 + 多个 AI 编辑器**共同维护
（见 ``collab/DUAL-MACHINE.md``）。不同编辑器会在文件里留下肉眼看不见的
字符，它们不会让程序崩溃，却会让工具链以难以定位的方式出错：

- **UTF-8 BOM**（``\\xef\\xbb\\xbf``）：Python 的 tokenizer 会自动跳过，
  所以 ``import`` 正常；但 ``ast.parse``、代码检查、diff 工具会直接报
  ``SyntaxError: invalid non-printable character U+FEFF``。
  本项目 ``app/services/url_import_service.py`` 就曾被它污染 ——
  正是本文件的第一条用例发现的。
- **裸 CR**（``\\r`` 后面不是 ``\\n``）：会破坏 diff 的可读性、干扰正则与
  shell 脚本解析。正常的 CRLF 行尾不计入（``core.autocrlf`` 在工作树里
  会产生 CRLF，这是预期的）。

只在源码目录（``app/``、``tests/``、``scripts/``）下检查。
"""
import pathlib

ROOTS = ("app", "tests", "scripts")
_BOM = b"\xef\xbb\xbf"


def _source_files():
    base = pathlib.Path(__file__).resolve().parent.parent
    for root in ROOTS:
        directory = base / root
        if directory.is_dir():
            yield from sorted(directory.rglob("*.py"))


def test_源码文件不带UTF8_BOM():
    """文件必须以 UTF-8（无 BOM）保存。

    修复方式：把文件按 ``utf-8-sig`` 读出、以 ``utf-8`` 写回；
    或在编辑器里「另存为 → UTF-8（无 BOM）」。
    """
    offenders = [str(p) for p in _source_files() if p.read_bytes().startswith(_BOM)]
    assert not offenders, (
        "以下文件带 UTF-8 BOM —— import 能过，但 ast.parse 等工具会报语法错误：\n"
        + "\n".join("    " + f for f in offenders)
    )


def test_源码文件不含裸CR():
    """不得出现裸 CR（``\\r`` 后不是 ``\\n``）；CRLF 属正常行尾，不计入。"""
    offenders = []
    for path in _source_files():
        data = path.read_bytes()
        bare_cr = data.count(b"\r") - data.count(b"\r\n")
        if bare_cr:
            offenders.append(f"{path}  ({bare_cr} 处)")
    assert not offenders, (
        "以下文件含裸 CR（\\r 后非 \\n）—— 会破坏 diff 与工具解析：\n"
        + "\n".join("    " + f for f in offenders)
    )
