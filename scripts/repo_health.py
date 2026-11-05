#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""仓库与工作区健康检查。

目的：把「工作区混乱」「分支混乱」这类问题从「靠人想起来」变成「一条命令报出来」。

用法（任意 checkout 内均可执行）：

    python scripts/repo_health.py            # 报告
    python scripts/repo_health.py --strict   # 有 🔴 时返回非零退出码，可用于提交前拦截

设计原则：
- 不硬编码开发者的目录名。worktree 从 `git worktree list` 自动发现，
  别人 clone 后只有一个 checkout，脚本会正确地报告「没有多余工位」。
- 只读不写。任何清理动作都要人来做，脚本只负责指出。
- 结论分三级：🔴 必须处理 / 🟡 建议处理 / ✅ 正常。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RED, YELLOW, GREEN, RESET = "🔴", "🟡", "✅", ""

# 允许长期存在的分支类型（不提示删除）
KEEP_PREFIXES = ("main", "uat", "dev", "agents/", "rescue/", "archive/", "chore/legacy")


def git(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    """执行 git，返回 (返回码, 标准输出)。失败不抛异常，由调用方判断。"""
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"<git 调用失败: {e}>"


def common_dir(start: str) -> str:
    """找到共享的 git 目录，从而定位主仓库根（worktree 共用同一份 objects）。"""
    code, out = git(["rev-parse", "--git-common-dir"], start)
    if code != 0:
        return start
    p = out if os.path.isabs(out) else os.path.join(start, out)
    # <root>/.git  →  <root>
    return os.path.dirname(os.path.abspath(p))


class Report:
    def __init__(self) -> None:
        self.red: list[str] = []
        self.yellow: list[str] = []
        self.ok: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")

    def line(self, level: str, msg: str) -> None:
        print(f"  {level} {msg}")
        if level == RED:
            self.red.append(msg)
        elif level == YELLOW:
            self.yellow.append(msg)
        elif level == GREEN:
            self.ok.append(msg)

    def summary(self) -> int:
        print(f"\n{'=' * 72}")
        print(f"结论：🔴 {len(self.red)} 项 · 🟡 {len(self.yellow)} 项 · ✅ {len(self.ok)} 项")
        if self.red:
            print("\n必须处理：")
            for m in self.red:
                print(f"  - {m}")
        if self.yellow:
            print("\n建议处理：")
            for m in self.yellow:
                print(f"  - {m}")
        return 1 if self.red else 0


def check_worktrees(root: str, rep: Report) -> None:
    rep.section("一、工作区（worktree）")

    code, out = git(["worktree", "list", "--porcelain"], root)
    if code != 0:
        rep.line(RED, "无法读取 worktree 列表")
        return

    trees: list[dict] = []
    cur: dict = {}
    for raw in out.splitlines():
        if raw.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": raw[len("worktree "):]}
        elif raw.startswith("branch "):
            cur["branch"] = raw[len("branch "):].replace("refs/heads/", "")
        elif raw.startswith("HEAD "):
            cur["head"] = raw[len("HEAD "):][:9]
        elif raw == "detached":
            cur["detached"] = True
        elif raw == "bare":
            cur["bare"] = True
    if cur:
        trees.append(cur)

    rep.line(GREEN, f"共 {len(trees)} 个工位")

    # 主仓库 dev 的最新提交，用于计算落后量
    _, dev_sha = git(["rev-parse", "dev"], root)

    for t in trees:
        path = t["path"]
        name = os.path.basename(path.rstrip("\\/"))
        branch = t.get("branch") or ("(detached)" if t.get("detached") else "?")
        head = t.get("head", "?")

        if not os.path.isdir(path):
            rep.line(RED, f"{name}：路径不存在（工位已失效，应 worktree prune）")
            continue

        _, st = git(["status", "--porcelain"], path)
        dirty = len([x for x in st.splitlines() if x.strip()])

        behind = ""
        if branch != "(detached)" and dev_sha:
            code_b, cnt = git(["rev-list", "--count", f"HEAD..dev"], path)
            if code_b == 0 and cnt and cnt != "0":
                behind = f"，落后 dev {cnt} 个提交"

        if dirty:
            rep.line(YELLOW, f"{name} [{branch}@{head}]：有 {dirty} 个未提交改动{behind}")
        else:
            rep.line(GREEN, f"{name} [{branch}@{head}]：干净{behind}")


def check_branches(root: str, rep: Report) -> None:
    rep.section("二、分支")

    # 远端
    remotes: list[str] = []
    code, out = git(["ls-remote", "--heads", "gitee"], root, timeout=60)
    if code == 0:
        remotes = [ln.split("\t")[-1].replace("refs/heads/", "") for ln in out.splitlines() if ln.strip()]
        rep.line(GREEN, f"gitee 远端共 {len(remotes)} 条分支")
        leftover = [b for b in remotes if b.startswith("dev-merge/")]
        if leftover:
            for b in leftover:
                rep.line(RED, f"远端残留一次性合并分支：{b}（合入后应删除）")
    else:
        rep.line(YELLOW, "无法连接 gitee，跳过远端分支检查")

    # 本地
    code, out = git(["for-each-ref", "--format=%(refname:short)|%(objectname:short)|%(upstream:short)", "refs/heads"], root)
    if code != 0:
        rep.line(RED, "无法读取本地分支")
        return

    stale_merged: list[str] = []
    unmerged: list[str] = []
    no_upstream: list[str] = []

    for ln in out.splitlines():
        parts = ln.split("|")
        if len(parts) < 3:
            continue
        name, sha, upstream = parts[0], parts[1], parts[2]
        if name in ("main", "uat", "dev"):
            continue
        # 没配 upstream 不等于「本地独有」：远端可能有同名分支，只是没设跟踪关系。
        # 两种都要查，否则会给正常的开发分支报假警。
        if not upstream and name not in remotes:
            no_upstream.append(f"{name}@{sha}")
        # 相对 dev 是否已完全包含
        code_a, _ = git(["merge-base", "--is-ancestor", name, "dev"], root)
        if code_a == 0:
            if not name.startswith(KEEP_PREFIXES):
                stale_merged.append(f"{name}@{sha}")
        else:
            unmerged.append(f"{name}@{sha}")

    if stale_merged:
        for b in stale_merged:
            rep.line(YELLOW, f"已完全并入 dev 但未清理：{b}")
    else:
        rep.line(GREEN, "没有「已并入 dev 却仍留着」的本地分支")

    if no_upstream:
        for b in no_upstream:
            if not b.startswith("chore/legacy"):
                rep.line(YELLOW, f"本地独有分支（无远端对照）：{b}")

    if unmerged:
        for b in unmerged:
            # agents/* 与 rescue/* 是刻意保留的独立线
            if b.startswith(("agents/", "rescue/", "chore/legacy")):
                rep.line(GREEN, f"刻意保留的独立分支：{b}")
            else:
                rep.line(RED, f"有未并入 dev 的独有提交：{b}（确认是否该丢弃）")


def check_readonly(root: str, rep: Report) -> None:
    rep.section("三、只读参考区")

    code, out = git(["worktree", "list", "--porcelain"], root)
    if code != 0:
        return

    paths = [ln[len("worktree "):] for ln in out.splitlines() if ln.startswith("worktree ")]
    ro = [p for p in paths if os.path.basename(p.rstrip("\\/")).endswith("-readonly")]

    if not ro:
        rep.line(YELLOW, "未发现 *-readonly 参考区（新 Agent 接手时没有只读快照可看）")
        return

    _, main_sha = git(["rev-parse", "main"], root)
    for p in ro:
        name = os.path.basename(p.rstrip("\\/"))
        _, head = git(["rev-parse", "HEAD"], p)
        _, st = git(["status", "--porcelain"], p)
        dirty = len([x for x in st.splitlines() if x.strip()])
        if dirty:
            rep.line(RED, f"{name}：出现 {dirty} 个改动（只读区不该被修改）")
        elif head != main_sha:
            _, cnt = git(["rev-list", "--count", f"{head}..main"], p)
            rep.line(YELLOW, f"{name}：落后 main {cnt} 个提交（刷新：git -C {p} checkout --detach main）")
        else:
            rep.line(GREEN, f"{name}：与 main@{main_sha[:9]} 一致，无改动")


def check_data(root: str, rep: Report) -> None:
    rep.section("四、数据状态")

    db = os.path.join(root, "data", "recsys.db")
    if not os.path.exists(db):
        rep.line(YELLOW, "data/recsys.db 不存在（首次启动会自动建库）")
        return

    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            counts = {}
            for t in ("job", "company", "knowledge_base", "crawl_task"):
                try:
                    counts[t] = con.execute(f"select count(*) from {t}").fetchone()[0]
                except Exception:  # noqa: BLE001
                    counts[t] = -1
            mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        rep.line(YELLOW, f"读库失败：{e}")
        return

    desc = " · ".join(f"{k}={v}" for k, v in counts.items())
    if counts.get("job", 0) == 0:
        rep.line(YELLOW, f"job 表 0 行（{desc}）—— 演示前看板会是空的")
    else:
        rep.line(GREEN, desc)

    if mode.lower() == "wal":
        rep.line(RED, "journal_mode=wal —— 本机并发写会随机 readonly，必须是 delete")
    else:
        rep.line(GREEN, f"journal_mode={mode}")


def check_main_copy(root: str, rep: Report) -> None:
    rep.section("五、主副本所在分支")

    _, cur = git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    _, common = git(["rev-parse", "--show-toplevel"], root)

    if cur == "main":
        rep.line(YELLOW,
                "主副本当前在 main（生产分支）。集成端在 main 上直接提交会让"
                "「固定 SHA 复现」失去稳定基点，建议日常停在 dev，晋升时再切。")
    else:
        rep.line(GREEN, f"当前 checkout 在 {cur}")


def main() -> int:
    ap = argparse.ArgumentParser(description="仓库与工作区健康检查")
    ap.add_argument("--strict", action="store_true", help="存在 🔴 时返回非零退出码")
    ap.add_argument("--root", default=None, help="主仓库根目录（默认自动探测）")
    args = ap.parse_args()

    start = args.root or os.getcwd()
    root = args.root or common_dir(start)
    if not os.path.isdir(os.path.join(root, ".git")) and not os.path.exists(os.path.join(root, ".git")):
        root = start

    print(f"主仓库：{root}")

    rep = Report()
    check_main_copy(root, rep)
    check_worktrees(root, rep)
    check_branches(root, rep)
    check_readonly(root, rep)
    check_data(root, rep)

    rc = rep.summary()
    return rc if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
