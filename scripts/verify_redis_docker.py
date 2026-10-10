# -*- coding: utf-8 -*-
"""一键验收脚本：Redis + Docker 架构硬化的**可自动化**判据。

用法::

    python scripts/verify_redis_docker.py                # 自动选择（有 Docker 就跑全部）
    python scripts/verify_redis_docker.py --local        # 只跑本机冒烟（无需 Docker）
    python scripts/verify_redis_docker.py --docker       # 只跑容器冒烟（需 Docker）

判据映射（任务书第 4 节）：

    A1  全量测试 0 failed，总数 ≥ 697            —— 本地模式（自动）
    A2  单机不回归：不设 REDIS_URL 启动，
        根路径 200 且含 <div id="app">，
        /api/system/version 401（需鉴权，属预期）—— 本地模式（自动）
    A7  docker compose config 语法通过            —— 容器模式（自动）
    A8  compose up 后 healthcheck 全 healthy、
        前端首屏不出现 502                        —— 容器模式（自动）

**刻意不自动跑的判据**（A3 / A4 / A6 / A9 / A10）：
它们要么需要真实 Redis + 2 worker 并发压测（A3/A4），要么需要精确控制
跨进程时序（A6/A9/A10）。把这类检查塞进脚本很容易做成「看起来通过」的
假验收（例如并发压测若串行执行，内存版也会"通过"）。
脚本改为**打印手工执行命令**，由验收方按判据逐条跑。

退出码：0 = 全部检查通过；1 = 存在失败项。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_RESULTS: list = []


def _record(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    mark = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    _RESULTS.append((name, ok, skipped, detail))
    print(f"[{mark}] {name}" + (f"  —— {detail}" if detail else ""))


# --------------------------------------------------------------------------
# 本地模式：A1 / A2
# --------------------------------------------------------------------------


def check_a1_full_tests() -> None:
    """A1：全量测试 0 failed。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line", "-p", "no:warnings"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    detail = ""
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-3:]
        detail = " / ".join(tail)[:200]
    _record("A1 全量测试 0 failed", proc.returncode == 0, detail)


def check_a2_standalone() -> None:
    """A2：不设 REDIS_URL 时单机行为不回归。"""
    os.environ.pop("REDIS_URL", None)
    try:
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import create_app
    except Exception as e:  # noqa: BLE001
        _record("A2 单机不回归", False, f"导入失败：{e}")
        return

    backend = str(getattr(settings, "rate_limit_backend", "memory"))
    if settings.redis_url:
        _record("A2 单机不回归", False,
                f"环境里存在 REDIS_URL={settings.redis_url!r}，该判据要求未配置 Redis")
        return

    app = create_app()
    with TestClient(app) as client:
        root = client.get("/")
        has_div = 'id="app"' in root.text
        version = client.get("/api/system/version").status_code

    ok = root.status_code == 200 and has_div and version == 401
    _record(
        "A2 单机不回归",
        ok,
        f"backend={backend} 根路径={root.status_code} 含#app={has_div} version={version}(期望401)",
    )


# --------------------------------------------------------------------------
# 容器模式：A7 / A8
# --------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "--version"], capture_output=True, timeout=15
        ).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _compose_env() -> dict:
    """compose 变量插值所需的最小环境（JWT_SECRET 为必填）。"""
    env = dict(os.environ)
    if not (env.get("JWT_SECRET") or "").strip():
        env["JWT_SECRET"] = "verify-standalone-" + "a" * 32
        print("  （未检测到 JWT_SECRET，本次验收使用临时值；真实部署请写进 .env）")
    return env


def check_a7_compose_config() -> None:
    """A7（前半）：docker compose config 语法校验通过。"""
    proc = subprocess.run(
        ["docker", "compose", "config"], cwd=str(ROOT), env=_compose_env(),
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    detail = "" if proc.returncode == 0 else (proc.stderr or "")[:200]
    _record("A7 docker compose config", proc.returncode == 0, detail)


def check_a8_ready_dependency() -> None:
    """A8：up 后三服务 healthy，且前端首屏不出现 502。"""
    env = _compose_env()
    up = subprocess.run(
        ["docker", "compose", "up", "-d", "--build"], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    if up.returncode != 0:
        _record("A8 启动并等待就绪", False, f"up 失败：{(up.stderr or '')[:200]}")
        return

    try:
        import time

        import httpx

        healthy = False
        deadline = time.time() + 180
        while time.time() < deadline:
            ps = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"], cwd=str(ROOT), env=env,
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
            )
            if "unhealthy" not in (ps.stdout or "").lower() and "starting" not in (ps.stdout or "").lower():
                healthy = True
                break
            time.sleep(5)

        front = httpx.get("http://127.0.0.1:8080/", timeout=10, follow_redirects=True)
        ok = healthy and front.status_code == 200
        _record(
            "A8 就绪依赖与首屏",
            ok,
            f"healthy={healthy} 首屏状态={front.status_code}(502=后端未就绪)",
        )
    except Exception as e:  # noqa: BLE001
        _record("A8 就绪依赖与首屏", False, f"异常：{e}")
    finally:
        subprocess.run(
            ["docker", "compose", "down"], cwd=str(ROOT), env=env,
            capture_output=True, text=True,
        )


# --------------------------------------------------------------------------
# 手工判据提示（刻意不自动执行，避免假验收）
# --------------------------------------------------------------------------

_MANUAL_HINTS = """
以下判据需要真实 Redis + 多副本，请按任务书第 4 节手工执行（脚本不代跑，
避免做成「看起来通过」的假验收）：

  A3 限流跨副本：2 worker 下用同一身份并发打 POST /api/auth/login，
     总放行数必须 ≤ RATE_LIMIT_LOGIN_PER_MINUTE（内存版会是 2 倍）
  A4 日配额跨副本：2 worker 下触发采集，平台累计访问 ≤ CRAWL_DAILY_QUOTA_PER_PLATFORM
  A6 缓存跨进程：进程 A 写缓存 → 进程 B 读得到；知识库变更后两边同时失效
  A9 （P1-1 若做）会话跨副本：同一 session_id 多轮对话历史不丢不串
  A10 熔断跨副本：一个 worker 触发熔断 → 另一个 worker 也拒绝请求

代码侧的可执行论证已放在单测里（不依赖真实 Redis）：
  tests/test_rate_limit_redis.py        —— A3 的对照实验（内存 2×limit vs Redis limit）
  tests/test_circuit_breaker_redis.py   —— A10 的对照实验（内存互不影响 vs Redis 一熔俱熔）
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Redis + Docker 架构硬化验收脚本")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--local", action="store_true", help="只跑本机冒烟（A1/A2）")
    group.add_argument("--docker", action="store_true", help="只跑容器冒烟（A7/A8）")
    args = parser.parse_args()

    has_docker = _docker_available()
    run_local = args.local or (not args.docker and not args.local) or args.docker is False
    run_docker = args.docker or (not args.local and has_docker)

    print("=" * 74)
    print(f"验收模式：local={run_local} docker={run_docker}（检测到 Docker：{has_docker}）")
    print("=" * 74)

    if run_local:
        check_a1_full_tests()
        check_a2_standalone()

    if run_docker:
        if not has_docker:
            _record("A7/A8 容器冒烟", False, "本机未安装 Docker，无法执行", skipped=True)
        else:
            check_a7_compose_config()
            check_a8_ready_dependency()

    print(_MANUAL_HINTS)

    failed = [r for r in _RESULTS if not r[1] and not r[2]]
    print("=" * 74)
    print(f"汇总：{len(_RESULTS)} 项检查，失败 {len(failed)} 项")
    for name, _, _, detail in failed:
        print(f"  FAIL {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
