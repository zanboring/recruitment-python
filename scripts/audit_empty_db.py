# -*- coding: utf-8 -*-
"""空库边界审计（只读）：把演示库**复制**到临时目录，在副本上跑对外接口。

用途（E1）：job 表 0 行时，逐个调用对外接口，找出「因为没数据而 500」的地方。

用法（工作树根目录）::

    python scripts/audit_empty_db.py --db data/recsys.db \
        --json out.json --markdown out.md

红线：
- 源库只做 ``shutil.copy2`` 读取，绝不写入；
- 所有请求打在临时副本上（``DB_URL`` 指向副本），不改仓库里的任何数据；
- 只调用 GET 与「读语义」的 POST（分页 / 统计 / 推荐 / 导出 / 日报生成），
  写接口、删除接口、真实采集与外部推送一律不调用，表里注明原因。
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

# 读语义的 POST：不改变业务数据（唯一例外是"生成日报"会在副本落一条日报，
# 但这是它的正常职责，且只发生在临时副本上）。
READ_POSTS = {
    "/api/jobs/page": {"page_num": 1, "page_size": 5},
    "/api/jobs/ai-analysis": {"keyword": "Python", "city": "长沙"},
    "/api/jobs/recommend/intelligent": {
        "skills": "Python,FastAPI", "city": "长沙", "experienceYears": 2, "limit": 5,
    },
    "/api/jobs/skill-profile": {"skills": "Python,FastAPI", "city": "长沙", "limit": 5},
    "/api/jobs/export": {"page_num": 1, "page_size": 5},
    "/api/reports/generate": None,
    "/api/ai/chat-local": {"message": "Python 岗位需要什么技能？", "session_id": "audit-session"},
    "/api/auth/auto-login": None,
}

# 明确不调用的写/删除/外部动作（表里说明原因，避免"没跑"被当成"通过"）
SKIP_REASONS = {
    ("POST", "/api/jobs/"): "创建岗位（写库）",
    ("PUT", "/api/jobs/{job_id}"): "更新岗位（写库）",
    ("PUT", "/api/jobs/{jobId}"): "更新岗位（写库）",
    ("DELETE", "/api/jobs/batch"): "批量删除（破坏性）",
    ("DELETE", "/api/jobs/{job_id}"): "删除岗位（破坏性）",
    ("DELETE", "/api/jobs/{jobId}"): "删除岗位（破坏性）",
    ("POST", "/api/jobs/import"): "文件导入（写库 + 需上传文件）",
    ("POST", "/api/jobs/vision-import"): "视觉识别导入（需图片 + 模型）",
    ("POST", "/api/jobs/vision-batch-import"): "视觉批量导入（需图片 + 模型）",
    ("POST", "/api/jobs/url-import"): "抓取外站页面（外网）",
    ("POST", "/api/crawler/start"): "真实网络采集",
    ("POST", "/api/crawler/ingest"): "写库（浏览器上报入口）",
    ("POST", "/api/crawl/task"): "创建任务并后台真实采集",
    ("POST", "/api/crawl/task/{task_id}/start"): "触发真实采集",
    ("DELETE", "/api/crawl/task/{task_id}"): "删除爬取任务（破坏性）",
    ("POST", "/api/data/cleanup"): "清空岗位数据（破坏性）",
    ("POST", "/api/data/import"): "文件导入（写库）",
    ("POST", "/api/knowledge"): "创建知识条目（写库）",
    ("PUT", "/api/knowledge/{knowledge_id}"): "更新知识条目（写库）",
    ("DELETE", "/api/knowledge/{knowledge_id}"): "删除知识条目（破坏性）",
    ("POST", "/api/knowledge/learn"): "写知识库（自动入库）",
    ("PUT", "/api/knowledge/{knowledge_id}/status"): "改状态（写库）",
    ("PUT", "/api/knowledge/{knowledge_id}/score"): "打分（写库）",
    ("PUT", "/api/knowledge/{knowledgeId}/status"): "改状态（写库）",
    ("PUT", "/api/knowledge/{knowledgeId}/score"): "打分（写库）",
    ("POST", "/api/auth/login"): "登录（凭据校验，空库无关）",
    ("POST", "/api/auth/register"): "注册（写库）",
    ("POST", "/api/auth/change-password"): "改口令（写库）",
    ("PUT", "/api/user/me"): "改个人资料（写库）",
    ("PUT", "/api/users/{user_id}"): "改用户（写库）",
    ("DELETE", "/api/user/{user_id}"): "删除用户（破坏性）",
    ("DELETE", "/api/users/{user_id}"): "删除用户（破坏性）",
    ("DELETE", "/api/logs/clean"): "清空日志（破坏性）",
    ("POST", "/api/settings/provider"): "改写本机 config.env",
    ("POST", "/api/model/switch"): "切换模型（改运行态）",
    ("POST", "/api/model/reload"): "重载模型（改运行态）",
    ("POST", "/api/model/chat"): "调用大模型（外部调用 + 会话写库）",
    ("POST", "/api/ai/chat-stream"): "流式对话（会话写库）",
    ("POST", "/api/ai/stream"): "流式对话（会话写库）",
    ("POST", "/api/ai/agent-stream"): "Agent 多步推理（外部调用 + 会话写库）",
    ("POST", "/api/ai/cancel"): "取消会话（改运行态）",
    ("DELETE", "/api/ai/session"): "删除会话（破坏性）",
    ("POST", "/api/reports/{report_id}/push"): "对外推送（外发）",
    ("POST", "/api/crawl/task/{taskId}/start"): "触发真实采集",
    ("DELETE", "/api/crawl/task/{taskId}"): "删除爬取任务（破坏性）",
}

# 空库下 4xx 是合理语义（按 id 查不存在 / 无日报）
EXPECTED_4XX = {
    ("GET", "/api/reports/{report_id}"),
    ("GET", "/api/reports/{reportId}"),
    ("GET", "/api/reports/{report_id}/download"),
    ("GET", "/api/reports/{reportId}/download"),
    ("GET", "/api/reports/latest"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/jobs/{jobId}"),
    ("GET", "/api/users/{user_id}"),
    ("GET", "/api/users/{userId}"),
    ("GET", "/api/crawl/tasks/{task_id}"),
    ("GET", "/api/crawl/tasks/{taskId}"),
}

EXPECTED_NOTES = {
    ("GET", "/api/auth/default-username"): "403：TestClient host 不是回环地址，预期",
    ("POST", "/api/auth/auto-login"): "403：TestClient host 不是回环地址，预期",
}


def copy_demo_db(src: str) -> tuple:
    """把源库复制到临时目录，返回 (副本路径, 临时根目录)。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="trae-empty-audit-"))
    dst = tmp_root / "recsys.db"
    shutil.copy2(src, dst)
    return str(dst), str(tmp_root)


def table_counts(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    try:
        counts = {}
        for t in ("job", "company", "knowledge_base", "user", "crawl_task", "daily_report"):
            try:
                counts[t] = con.execute(f"select count(*) from {t}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = None
        return counts
    finally:
        con.close()


def admin_identity(db_path: str) -> tuple:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("select id, username, role from user order by id limit 1").fetchone()
        return (row[0], row[1], row[2] or "ADMIN") if row else (1, "admin", "ADMIN")
    except sqlite3.OperationalError:
        return (1, "admin", "ADMIN")
    finally:
        con.close()


def collect_ops(client) -> tuple:
    """返回 (paths, extra_paths)：真实 schema + include_in_schema=False 的路由。"""
    openapi = client.app.openapi()
    paths = dict(openapi["paths"])
    extra = []
    for route in client.app.routes:
        rp = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not rp.startswith("/api") or not methods:
            continue
        if rp not in paths:
            paths[rp] = {m.lower(): {} for m in methods if m != "HEAD"}
            extra.append(rp)
    return paths, extra


class TraceCollector:
    """收集 500 请求的异常堆栈，便于定位到具体服务函数。"""

    def __init__(self):
        import logging

        self.current = {"path": ""}
        self.traces = {}
        collector = self

        class _Handler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR and record.exc_info:
                    msg = self.format(record)
                    collector.traces.setdefault(collector.current["path"], msg)
                elif record.levelno >= logging.ERROR:
                    collector.traces.setdefault(collector.current["path"], record.getMessage())

        handler = _Handler()
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.ERROR)


def prepare_fresh_db() -> tuple:
    """建一个空库路径（表由应用 lifespan 的 create_all 创建），返回 (路径, 临时根目录)。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="trae-fresh-audit-"))
    return str(tmp_root / "recsys.db"), str(tmp_root)


def run(db_src: str, fresh: bool = False) -> tuple:
    if fresh:
        dst, tmp_root = prepare_fresh_db()
        counts = {}
    else:
        dst, tmp_root = copy_demo_db(db_src)
        counts = table_counts(dst)
    # 临时目录内工作：日报 Excel 会落在 "reports/daily"，不能写进仓库工作树
    os.chdir(tmp_root)
    os.environ["DB_URL"] = "sqlite+aiosqlite:///" + dst.replace("\\", "/")
    for k, v in (
        ("RATE_LIMIT_BACKEND", "memory"), ("CIRCUIT_BREAKER_BACKEND", "memory"),
        ("QUOTA_BACKEND", "memory"), ("SESSION_BACKEND", "memory"),
        ("OLLAMA_ENABLED", "false"),
        ("JWT_SECRET", "ci_only_secret_at_least_32_characters_long"),
    ):
        os.environ.setdefault(k, v)

    import audit_api as A  # noqa: E402  复用 summarize / auth_map / 参数拼装
    from fastapi.testclient import TestClient  # noqa: E402
    from app.main import create_app  # noqa: E402
    from app.utils.security import create_token  # noqa: E402

    ids = A.sample_ids(dst)
    uid, uname, role = admin_identity(dst)
    token = create_token(uid, uname, role)
    headers = {"Authorization": f"Bearer {token}"}

    collector = TraceCollector()
    results = []
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        paths, extra = collect_ops(client)
        amap = A.auth_map(client.app)

        for path, item in paths.items():
            for method, op in item.items():
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                mu = method.upper()
                key = (mu, path)
                rec = {"method": mu, "path": path, "auth": amap.get(key, False),
                       "status": "", "summary": "", "note": "", "source": ""}
                if path in extra:
                    rec["note"] = "include_in_schema=False，不在 openapi 里"

                if mu != "GET" and path not in READ_POSTS:
                    rec["status"] = "未调用"
                    rec["source"] = "—"
                    rec["note"] = SKIP_REASONS.get(key, "写操作 / 非只读语义，未调用")
                    results.append(rec)
                    continue

                url = path
                params = {}
                placeholder_vals = {}
                for pname, info in [(p["name"], p) for p in op.get("parameters", []) if p["in"] == "path"]:
                    norm = pname.replace("_", "").lower()
                    if norm in ("jobid",):
                        val = ids["job_id"]
                    elif norm in ("knowledgeid",):
                        val = ids["knowledge_id"]
                    elif norm in ("userid",):
                        val = ids["user_id"]
                    elif norm in ("reportid",):
                        val = ids["report_id"]
                    elif norm in ("taskid",):
                        val = ids["task_id"]
                    elif norm == "keyword":
                        val = "Python"
                    elif norm == "username":
                        val = uname
                    else:
                        val = "audit"
                    placeholder_vals[norm] = val
                for placeholder in {seg.split("}")[0].lstrip("{") for seg in url.split("{")[1:]}:
                    norm = placeholder.replace("_", "").lower()
                    if norm in placeholder_vals:
                        url = url.replace("{" + placeholder + "}", str(placeholder_vals[norm]))

                for pname, info in [(p["name"], p) for p in op.get("parameters", []) if p["in"] == "query"]:
                    params[pname] = A.QUERY.get(
                        pname, 1 if info.get("schema", {}).get("type") == "integer" else "audit"
                    )

                body = READ_POSTS.get(path, "__none__")
                kwargs = {"headers": headers}
                if params:
                    kwargs["params"] = params
                if body:
                    kwargs["json"] = body

                collector.current["path"] = f"{mu} {url}"
                resp = client.request(mu, url, **kwargs)
                rec["status"] = resp.status_code
                summary, data = A.summarize(resp)
                rec["summary"] = summary
                if resp.status_code >= 500:
                    rec["source"] = "报错"
                    trace = collector.traces.get(collector.current["path"], "")
                    if trace:
                        rec["note"] = trace.replace("\n", " ")[-400:]
                elif resp.status_code >= 400:
                    if key in EXPECTED_NOTES:
                        rec["source"] = "—"
                        rec["note"] = EXPECTED_NOTES[key]
                    elif key in EXPECTED_4XX or resp.status_code in (404, 422):
                        rec["source"] = "—"
                        rec["note"] = "预期 4xx（按 id 查不存在 / 参数校验）"
                    else:
                        rec["source"] = "报错"
                else:
                    if isinstance(data, list) and not data:
                        rec["source"] = "空"
                    elif isinstance(data, dict) and not data:
                        rec["source"] = "空"
                    elif data == 0:
                        rec["source"] = "空"
                    else:
                        rec["source"] = "有数据"
                results.append(rec)
    if fresh:
        counts = table_counts(dst)
    return results, counts, tmp_root


def to_markdown(results: list) -> str:
    lines = ["| # | 方法 + 路径 | 状态码 | 响应摘要 | 判定 | 是否修复 | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        note = (r["note"] or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        summary = (r["summary"] or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        fixed = "无需修复" if not (isinstance(r["status"], int) and r["status"] >= 500) else "待修复"
        lines.append(
            f"| {i} | {r['method']} {r['path']} | {r['status']} | "
            f"{summary} | {r['source']} | {fixed} | {note} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    # 默认从「脚本位置 → 项目根 → data/recsys.db」推导，不写死作者本机绝对路径：
    # 硬编码时别人 clone 下来不传 --db，就会静止指向一个不存在的目录，
    # 而且这个绝对路径还会被公开版快照的本机路径扫描拦下。
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent.parent / "data" / "recsys.db"),
    )
    parser.add_argument("--fresh", action="store_true", help="用全新空库（全部表 0 行）而非演示库副本")
    parser.add_argument("--json", default="")
    parser.add_argument("--markdown", default="")
    args = parser.parse_args()

    results, counts, tmp_root = run(args.db, fresh=args.fresh)
    print("源库各表行数：", counts)
    print("临时副本：", tmp_root)
    print("应查接口数：", len(results))
    codes = {}
    for r in results:
        codes[str(r["status"])] = codes.get(str(r["status"]), 0) + 1
    print("状态分布：", dict(sorted(codes.items())))
    bad = [r for r in results if isinstance(r["status"], int) and r["status"] >= 500]
    print("5xx 数量：", len(bad))
    for r in bad:
        print("!!", r["method"], r["path"], r["summary"], "|", r["note"][:200])

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
