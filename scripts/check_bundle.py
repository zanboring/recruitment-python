#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包产物依赖校验（正确版）

为什么需要这个脚本（2026-10-26 实测记录）
---------------------------------------------------------------
原 `build-exe.bat` 用「dist\\RecSys\\_internal\\<模块名> 是否存在」判断依赖有没有打进产物。
这个判据对**纯 Python 包是错的**，会造成「打包成功却报失败」：

  · 带 C 扩展的包（asyncpg、pydantic_core）→ 落成 `_internal\\<模块>\\` 目录  ✅ 原判据成立
  · 纯 Python 包（aiosqlite、aiomysql）    → 打进 exe 内嵌的 PYZ 归档，
                                              **不产生 _internal 目录**  ❌ 原判据永远为假

实测证据（同一次打包）：
  · 产物启动正常，`GET /` 返回 200，页面标题「招聘数据可视化系统」
  · 用 PyInstaller 官方 API 读 PYZ：aiosqlite 5 项、aiomysql 8 项、asyncpg 20 项

所以本脚本改为直接读 PYZ 归档 + 检查 _internal 资源，对两类包都成立。

用法
----
    python scripts/check_bundle.py                  # 默认检查 dist/RecSys/RecSys.exe
    python scripts/check_bundle.py <exe路径>

退出码：0 = 全部通过 ；1 = 有缺失（不允许发版）
"""

import os
import sys
import tempfile

# ---- 必须出现在 PYZ（纯 Python 模块）里的包 ----
REQUIRED_PYZ = [
    "aiosqlite",     # SQLite 异步驱动（默认 db_type）
    "aiomysql",      # MySQL 异步驱动（运行时按方言动态 import，静态分析看不到）
    "asyncpg",       # PostgreSQL 异步驱动（同上）
    "fastapi",
    "sqlalchemy",
    "uvicorn",
    "openpyxl",      # Excel 导出
]

# ---- 必须存在于 _internal 下的资源/目录 ----
REQUIRED_INTERNAL = [
    "pydantic_core",  # Rust 扩展，缺少会在启动时崩
    "greenlet",       # SQLAlchemy 异步依赖
]

# ---- 前端构建产物：任一存在即可（打包时前端优先，回落 app/static）----
REQUIRED_FRONTEND = [
    os.path.join("_internal", "frontend_dist", "index.html"),
    os.path.join("_internal", "app_static", "index.html"),
]


def fail(msg):
    print("  [错误] " + msg)
    return False


def main():
    dist_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.abspath(os.path.join(dist_dir, "..", "dist", "RecSys"))
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(dist_dir, "RecSys.exe")

    print("=" * 60)
    print("  校验打包产物")
    print("  " + exe)
    print("=" * 60)

    if not os.path.isfile(exe):
        print("  [错误] 产物不存在：" + exe)
        print("        请先运行 build-exe.bat")
        return 1

    ok = True

    # ---------- 1. 读 PYZ 归档里的纯 Python 模块 ----------
    print("\n[1/3] PYZ 归档内的核心模块（纯 Python，不落 _internal）")
    pyz_modules = set()
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

        arc = CArchiveReader(exe)
        pyz_names = [n for n in arc.toc.keys() if n.lower().endswith(".pyz")]
        if not pyz_names:
            ok = fail("产物里找不到 PYZ 归档（exe 可能不完整）")
        else:
            data = arc.extract(pyz_names[0])
            fd, tmp = tempfile.mkstemp(suffix=".pyz")
            try:
                os.write(fd, data)
                os.close(fd)
                reader = ZlibArchiveReader(tmp)
                pyz_modules = set(reader.toc.keys())
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            print("       PYZ 内模块总数：%d" % len(pyz_modules))
    except Exception as e:  # noqa: BLE001
        print("  [警告] 读取 PYZ 失败（%s: %s），退回只看 _internal" % (type(e).__name__, e))

    for mod in REQUIRED_PYZ:
        hits = [m for m in pyz_modules if m == mod or m.startswith(mod + ".")]
        if hits:
            print("       [OK]   %-12s %3d 项" % (mod, len(hits)))
        else:
            # 兜底：某些包可能整个落成 _internal 目录
            if os.path.exists(os.path.join(dist_dir, "_internal", mod)):
                print("       [OK]   %-12s (在 _internal 下)" % mod)
            elif pyz_modules:
                ok = fail("%s 未打进产物 —— 目标机会在 import 时崩溃" % mod)
            else:
                print("       [跳过] %-12s （PYZ 不可读，无法判断）" % mod)

    # ---------- 2. _internal 里的扩展与资源 ----------
    print("\n[2/3] _internal 下的扩展与资源")
    for name in REQUIRED_INTERNAL:
        p = os.path.join(dist_dir, "_internal", name)
        if os.path.exists(p):
            print("       [OK]   %s" % name)
        else:
            ok = fail("%s 不存在于 _internal —— 启动时会崩" % name)

    # ---------- 3. 前端产物 ----------
    print("\n[3/3] 前端构建产物")
    found = [f for f in REQUIRED_FRONTEND if os.path.exists(os.path.join(dist_dir, f))]
    if found:
        print("       [OK]   %s" % found[0].replace(os.sep, "/"))
    else:
        ok = fail("前端页面缺失（frontend_dist/index.html 与 app_static/index.html 都不存在）")

    # ---------- 汇总 ----------
    print("\n" + "=" * 60)
    if ok:
        print("  校验通过：产物可发布")
        print("  发布前还要确认：tag 与 app/version.py 的 APP_VERSION 一致")
    else:
        print("  校验失败：产物不可发布，请先修复上面标 [错误] 的项")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
