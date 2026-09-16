#!/usr/bin/env python3
"""协作状态检查：一条命令看清「对方做了什么、有没有新消息、合并会不会冲突」。

用法（在任一工作目录下）：

    collab-check.cmd            # Windows 双击即可
    python collab/check.py      # 或直接跑

## 为什么需要它

两个 AI 各自在独立工作区里干活，md 信箱是**拉取式**的 —— 不主动去看就不知道
对方写了什么。这个脚本把「读消息」压缩成一次点击，并顺带回答三个更要紧的问题：

1. 对方有哪些提交我还没有？（我是不是在过时的基础上改）
2. 我现在提交，合并时会不会冲突？**冲突在哪些文件？**（靠 ``git merge-tree`` 试算）
3. 收件箱有没有新消息？（自动检测，不需要手工标记已读）

## 为什么用「试算合并」而不是比对文件列表

``git diff --name-only`` 两次再求交集，只能看出「双方都动过哪些文件」，
但**双方都动过 ≠ 一定冲突**（改的是不同区域时 git 能自动合并）。
``git merge-tree --write-tree`` 直接给出权威答案，与实际合并的判定完全一致。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "collab" / ".state"
STATE_FILE = STATE_DIR / "seen.json"

INBOXES = {
    "dev-trae": "inbox-trae.md",          # WorkBuddy 写给 Trae
    "dev-workbuddy": "inbox-workbuddy.md",  # Trae 写给 WorkBuddy
}
SIDES = {"dev-workbuddy": "WorkBuddy", "dev-trae": "Trae"}


def git(*args: str) -> tuple:
    """执行 git 命令，返回 (返回码, 标准输出)。失败不抛异常。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 1, f"(无法执行 git：{exc})"
    return proc.returncode, (proc.stdout or "").strip()


def out(text: str = "") -> None:
    print(text)


def section(title: str) -> None:
    out()
    out(f"[{title}]")


def current_branch() -> str:
    _, name = git("rev-parse", "--abbrev-ref", "HEAD")
    return name.strip()


def branch_positions() -> list:
    rows = []
    for branch in ("dev-workbuddy", "dev-trae", "main"):
        rc, sha = git("rev-parse", "--short", branch)
        if rc == 0 and sha:
            rows.append((branch, sha))
    return rows


def ahead_behind(mine: str, other: str) -> tuple:
    """返回 (对方领先我的提交, 我领先对方的提交)。"""
    _, only_other = git("log", "--oneline", "--no-decorate", f"{mine}..{other}")
    _, only_mine = git("log", "--oneline", "--no-decorate", f"{other}..{mine}")
    return (
        [line for line in only_other.splitlines() if line.strip()],
        [line for line in only_mine.splitlines() if line.strip()],
    )


def merge_conflicts(mine: str, other: str) -> tuple:
    """试算合并，返回 (是否可自动合并, 冲突文件列表)。

    ``git merge-tree --write-tree A B`` 的输出：首行是结果树 ID，
    之后每行是一个冲突路径。返回码非 0 表示存在冲突。

    注意 ``--name-only`` 仍会把 ``Auto-merging ...`` / ``CONFLICT (...)`` 这类
    进度信息混在同一个流里，必须过滤掉 —— 否则「冲突文件列表」里会混进提示语，
    使用者根本分不清哪个是真文件。
    """
    rc, text = git("merge-tree", "--write-tree", "--name-only", mine, other)
    if not text or text.startswith("(无法执行"):
        return True, []

    noise = ("Auto-merging ", "CONFLICT (", "CONFLICT:", "Merge conflict in ")
    files = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(noise):
            continue
        # 首行是 40 位十六进制的树对象 ID
        if len(line) == 40 and all(ch in "0123456789abcdef" for ch in line):
            continue
        files.append(line)
    return (rc == 0 and not files), files


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 状态写不了不该影响检查本身


def check_inbox(state: dict) -> None:
    """检查收件箱。自动判断「上次查看后是否有改动」，无需手工标记已读。"""
    seen = state.setdefault("inbox", {})
    for branch, filename in INBOXES.items():
        path = ROOT / "collab" / filename
        who = SIDES.get(branch, branch)
        if not path.exists():
            out(f"  {filename}：尚未创建")
            continue

        import hashlib

        content = path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        prev = seen.get(filename)

        # 统计消息条数（约定：每条消息以 '## MSG' 开头）
        count = sum(1 for line in content.splitlines() if line.startswith("## MSG"))

        if prev is None:
            flag = "首次读取"
        elif prev != digest:
            flag = "★ 有更新，建议查看"
        else:
            flag = "无新消息"
        out(f"  {filename}（{who} 写给自己以外的一方）— 消息 {count} 条，{flag}")

        seen[filename] = digest
    state["branch"] = current_branch()


def main() -> int:
    mine = current_branch()
    other = "dev-trae" if mine == "dev-workbuddy" else "dev-workbuddy"
    who_me = SIDES.get(mine, mine)
    who_other = SIDES.get(other, other)

    out("=" * 62)
    out("  协作状态检查（WorkBuddy ↔ Trae）")
    out("=" * 62)
    out(f"当前工作区分支：{mine}（{who_me} 的工作区）")

    section("1. 分支位置")
    for branch, sha in branch_positions():
        tag = "← 当前" if branch == mine else ""
        out(f"  {branch:<16} {sha}  {tag}")

    only_other, only_mine = ahead_behind(mine, other)

    section(f"2. {who_other} 领先我的提交（若有，说明我在过时的基础上改）")
    if only_other:
        for line in only_other[:20]:
            out(f"  {line}")
        if len(only_other) > 20:
            out(f"  ...（共 {len(only_other)} 条）")
    else:
        out("  无 —— 我的基础是最新的")

    section(f"3. 我领先 {who_other} 的提交")
    if only_mine:
        for line in only_mine[:10]:
            out(f"  {line}")
        if len(only_mine) > 10:
            out(f"  ...（共 {len(only_mine)} 条）")
    else:
        out("  无")

    section("4. 合并试算（现在合并 dev-workbuddy 与 dev-trae 会怎样）")
    clean, conflicts = merge_conflicts("dev-workbuddy", "dev-trae")
    if clean:
        out("  ✅ 可自动合并，无冲突")
    else:
        out("  ⚠️ 以下文件会冲突（合并前先协商归属，或一方让开）：")
        for path in conflicts:
            out(f"       {path}")
        out("")
        out("  提示：若冲突只是在同一位置各加一行，通常「两边都保留」即可。")

    section("5. 收件箱")
    state = load_state()
    check_inbox(state)
    save_state(state)

    out()
    out("-" * 62)
    out("读消息：collab/inbox-<你的分支>.md")
    out("写消息：只追加自己的收件箱文件，不要改对方的（这样结构上不可能冲突）")
    out("-" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
