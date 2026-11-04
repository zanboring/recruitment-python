# -*- coding: utf-8 -*-
"""接口审计脚本（只读审计，写入只发生在调用方提供的临时库副本上）。

用法（工作树根目录，DB_URL 必须指向**临时副本**）：

    python scripts/audit_api.py --mode data --json out.json --markdown out.md
    python scripts/audit_api.py --mode empty --json out.json --markdown out.md

要点：
- 必须在 app 启动（lifespan）之后读 /openapi.json：驼峰兼容路由在 startup 才挂载，
  进程内提前调用 app.openapi() 只有 89 条。
- 写接口只调「幂等且可恢复」的；删除 / 清空 / 重置 / 真实网络采集一律不调，
  在表里写明「未调用 + 原因」。
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SKIP_OPS = {
    ("DELETE", "/api/ai/session"): "删除会话历史（破坏性），只读代码",
    ("DELETE", "/api/jobs/batch"): "批量删除岗位（破坏性），只读代码",
    ("DELETE", "/api/jobs/{job_id}"): "删除岗位（破坏性），只读代码",
    ("DELETE", "/api/jobs/{jobId}"): "删除岗位（破坏性），只读代码",
    ("DELETE", "/api/knowledge/{knowledge_id}"): "删除知识条目（破坏性），只读代码",
    ("DELETE", "/api/knowledge/{knowledgeId}"): "删除知识条目（破坏性），只读代码",
    ("DELETE", "/api/logs/clean"): "清空操作日志（破坏性），只读代码",
    ("DELETE", "/api/user/{user_id}"): "删除用户（破坏性），只读代码",
    ("DELETE", "/api/user/{userId}"): "删除用户（破坏性），只读代码",
    ("DELETE", "/api/users/{user_id}"): "删除用户（破坏性），只读代码",
    ("DELETE", "/api/users/{userId}"): "删除用户（破坏性），只读代码",
    ("DELETE", "/api/crawl/task/{task_id}"): "删除爬取任务（破坏性），只读代码",
    ("DELETE", "/api/crawl/task/{taskId}"): "删除爬取任务（破坏性），只读代码",
    ("POST", "/api/data/cleanup"): "清理数据（破坏性），只读代码",
    ("POST", "/api/data/import"): "文件导入（写库 + 需上传文件），未调用",
    ("POST", "/api/jobs/import"): "文件导入（写库 + 需上传文件），未调用",
    ("POST", "/api/jobs/vision-import"): "需上传图片并调用视觉模型（无 key），未调用",
    ("POST", "/api/jobs/vision-batch-import"): "需上传图片并调用视觉模型（无 key），未调用",
    ("POST", "/api/jobs/url-import"): "会发起真实外部网页抓取，未调用",
    ("POST", "/api/crawler/start"): "会发起真实网络采集（Playwright / 外站），未调用",
    ("POST", "/api/crawl/task"): "创建任务后立即后台真实采集（外站），未调用",
    ("POST", "/api/crawl/task/{task_id}/start"): "会触发真实网络采集，未调用",
    ("POST", "/api/crawl/task/{taskId}/start"): "会触发真实网络采集，未调用",
    ("POST", "/api/settings/provider"): "会改写本机 config.env（真实配置），审计不调用",
    ("POST", "/api/reports/{report_id}/push"): "对外推送报告（外发），未调用",
    ("POST", "/api/reports/{reportId}/push"): "对外推送报告（外发），未调用",
}

BODIES = {
    ("POST", "/api/auth/login"): {"username": "admin", "password": "admin123"},
    ("POST", "/api/jobs/"): {
        "title": "审计临时岗位", "source_site": "seed", "company_name": "审计公司",
        "city": "长沙", "experience": "3-5年", "education": "本科",
        "min_salary": 10000, "max_salary": 20000, "skills": "Python, FastAPI",
        "job_desc": "审计脚本创建的临时岗位",
    },
    ("POST", "/api/jobs/page"): {"page_num": 1, "page_size": 5},
    ("POST", "/api/jobs/ai-analysis"): {"keyword": "Python", "city": "长沙"},
    ("POST", "/api/jobs/recommend/intelligent"): {
        "skills": "Python,FastAPI", "city": "长沙", "experienceYears": 2, "limit": 5,
    },
    ("POST", "/api/jobs/skill-profile"): {"skills": "Python,FastAPI", "city": "长沙", "limit": 5},
    ("POST", "/api/jobs/export"): {"page_num": 1, "page_size": 5},
    ("POST", "/api/ai/chat-local"): {"message": "Python 岗位需要什么技能？", "session_id": "audit-session"},
    ("POST", "/api/ai/chat-stream"): {"message": "Python 岗位需要什么技能？", "session_id": "audit-session"},
    ("POST", "/api/ai/agent-stream"): {"message": "Python 岗位需要什么技能？", "session_id": "audit-session"},
    ("POST", "/api/ai/stream"): {"message": "Python 岗位需要什么技能？", "session_id": "audit-session"},
    ("POST", "/api/crawler/ingest"): {
        "source_site": "boss", "city": "长沙", "keyword": "Python",
        "jobs": [{
            "title": "审计浏览器上报岗位", "company_name": "审计公司", "city": "长沙",
            "experience": "1-3年", "education": "本科", "salary": "12-20K",
            "skills": "Python", "description": "负责推荐系统后端开发，要求熟悉 FastAPI 与数据库设计。",
            "url": "https://example.com/job/audit",
        }],
    },
    ("POST", "/api/knowledge"): {"question": "审计用知识条目", "answer": "审计用答案", "tags": "audit"},
    ("POST", "/api/model/switch"): {"model_name": "auto"},
    ("POST", "/api/model/chat"): {"message": "你好", "use_local_model": True},
    ("PUT", "/api/user/me"): {"email": "admin@example.com"},
    ("PUT", "/api/users/{user_id}"): {"email": "audit-probe@example.com", "role": "USER"},
    ("PUT", "/api/users/{userId}"): {"email": "audit-probe@example.com", "role": "USER"},
    ("PUT", "/api/jobs/{job_id}"): {"title": "审计临时岗位-已更新"},
    ("PUT", "/api/jobs/{jobId}"): {"title": "审计临时岗位-已更新"},
    ("PUT", "/api/knowledge/{knowledge_id}"): {"question": "审计用知识条目-已更新"},
    ("PUT", "/api/knowledge/{knowledgeId}"): {"question": "审计用知识条目-已更新"},
    ("PUT", "/api/knowledge/{knowledge_id}/status"): None,   # 参数在 query
    ("PUT", "/api/knowledge/{knowledgeId}/status"): None,
    ("PUT", "/api/knowledge/{knowledge_id}/score"): {},
    ("PUT", "/api/knowledge/{knowledgeId}/score"): {},
}

QUERY = {
    "page_num": 1, "pageNum": 1, "page_size": 10, "pageSize": 10, "page": 1,
    "limit": 10, "offset": 0, "days": 7,
    "keyword": "Python", "question": "审计学习条目（知识库入库验证）",
    "answer": "这是审计脚本写入的较长答案，用于验证知识库自动入库的质量门槛与落库行为。",
    "tags": "python,fastapi", "skills": "Python,FastAPI", "education": "本科",
    "experience": "3-5年", "city": "长沙", "session_id": "audit-session",
    "status": 1, "score": 8, "enabled": True, "username": "admin", "action": "login",
}

EXPECTED_4XX = {
    # 无日报数据时，按 id 查 / 下载 / 推送返回 404 是合理语义
    ("GET", "/api/reports/{report_id}"), ("GET", "/api/reports/{reportId}"),
    ("GET", "/api/reports/{report_id}/download"), ("GET", "/api/reports/{reportId}/download"),
    ("POST", "/api/reports/{report_id}/push"), ("POST", "/api/reports/{reportId}/push"),
}

EXPECTED_NOTES = {
    ("GET", "/api/auth/default-username"): "403 由回环地址校验触发（TestClient 的 host 是 testclient，非 127.0.0.1）——预期，非缺陷",
    ("POST", "/api/auth/auto-login"): "403 由回环地址校验触发（TestClient 的 host 是 testclient，非 127.0.0.1）——预期，非缺陷",
}
EXPECTED_4XX |= set(EXPECTED_NOTES)


def sample_ids(db_path: str) -> dict:
    ids = {"job_id": 1, "knowledge_id": 1, "task_id": 1, "user_id": 1, "report_id": 1}
    if not db_path or not Path(db_path).exists():
        return ids
    con = sqlite3.connect(db_path)
    try:
        def min_id(table):
            try:
                row = con.execute(f"select min(id) from {table}").fetchone()
                return row[0]
            except Exception:
                return None

        for key, table in (("job_id", "job"), ("knowledge_id", "knowledge_base"),
                           ("task_id", "crawl_task"), ("user_id", "user"), ("report_id", "daily_report")):
            v = min_id(table)
            if v:
                ids[key] = v
        # 作者 admin 的 id 才是合法 user_id
        try:
            row = con.execute("select id from user where username='admin'").fetchone()
            if row:
                ids["user_id"] = row[0]
        except Exception:
            pass
        # 爬取任务取最新的
        try:
            row = con.execute("select max(id) from crawl_task").fetchone()
            if row and row[0]:
                ids["task_id"] = row[0]
        except Exception:
            pass
    finally:
        con.close()
    return ids


def auth_map(app) -> dict:
    """(path, method) -> 是否需要 get_current_user / require_admin。"""
    from fastapi.routing import APIRoute

    result = {}

    def has_auth(dependant) -> bool:
        for dep in dependant.dependencies:
            name = getattr(dep.call, "__name__", "")
            if name in ("get_current_user", "require_admin"):
                return True
            if has_auth(dep):
                return True
        return False

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        needed = has_auth(route.dependant)
        for method in route.methods or []:
            result[(route.path, method.upper())] = needed
    return result


def resolve_ref(schema: dict, root: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 10:
        schema = root["components"]["schemas"][schema["$ref"].split("/")[-1]]
        seen += 1
    return schema


def validate(inst, schema, root, path="$", errs=None):
    """JSON Schema 子集校验（类型 / required / enum / 数组元素）。"""
    if errs is None:
        errs = []
    if len(errs) >= 5:
        return errs
    schema = resolve_ref(schema, root)
    if not isinstance(schema, dict) or not schema:
        return errs
    if "anyOf" in schema:
        # 取第一个非 null 分支做宽松校验
        branches = [b for b in schema["anyOf"] if b.get("type") != "null"] or schema["anyOf"]
        return validate(inst, branches[0], root, path, errs)
    t = schema.get("type")
    type_ok = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if t in type_ok and not type_ok[t](inst):
        errs.append(f"{path}: 期望 {t}，实际 {type(inst).__name__}")
        return errs
    if t == "object":
        for req in schema.get("required", []):
            if req not in inst:
                errs.append(f"{path}: 缺必填字段 {req}")
        for key, sub in schema.get("properties", {}).items():
            if key in inst:
                validate(inst[key], sub, root, f"{path}.{key}", errs)
    if t == "array" and "items" in schema and isinstance(inst, list):
        for i, v in enumerate(inst[:5]):
            validate(v, schema["items"], root, f"{path}[{i}]", errs)
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} 不在枚举 {schema['enum']}")
    return errs


def summarize(resp):
    """给出一眼可见的响应摘要。"""
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype:
        return f"二进制/文本响应 {len(resp.content)}B ({ctype.split(';')[0]})", None
    try:
        body = resp.json()
    except Exception:
        return "响应不是合法 JSON", None
    if isinstance(body, dict):
        if "detail" in body:
            return "detail=" + str(body["detail"])[:200], None
        if "data" in body and "code" in body:
            data = body.get("data")
            code = body.get("code")
            msg = body.get("message")
            prefix = f"code={code}" + (f", message={msg}" if msg else "")
            if isinstance(data, list):
                return f"{prefix}, data 列表 {len(data)} 条", data
            if isinstance(data, dict):
                return f"{prefix}, data 字段 {', '.join(list(data)[:8])}", data
            return f"{prefix}, data={str(data)[:60]}", data
        return f"keys={', '.join(list(body)[:8])}", body
    if isinstance(body, list):
        return f"列表 {len(body)} 条", body
    return str(body)[:80], body


def run_audit(mode: str, db_path: str) -> list:
    from fastapi.testclient import TestClient
    from app.main import create_app

    ids = sample_ids(db_path if mode == "data" else "")
    results = []
    ctx = {"job_id_for_write": ids["job_id"], "knowledge_id_for_write": ids["knowledge_id"]}

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        # ⚠️ 不能用 client.get("/openapi.json")：CamelCaseCompatMiddleware 会把 JSON 里
        # 的 key 也做 snake→camel 改写，导致 `/api/jobs/{job_id}` 被改写成
        # `/api/jobs/{jobId}`，凭空多出 15 条「路径」（104 = 89 + 15）。
        # 这里取未经过响应中间件的真实 schema，并补上 include_in_schema=False 的真实路由。
        openapi = client.app.openapi()
        paths = dict(openapi["paths"])
        extra_paths = []
        for route in client.app.routes:
            rp = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if not rp.startswith("/api") or not methods:
                continue
            if rp not in paths:
                paths[rp] = {m.lower(): {} for m in methods if m != "HEAD"}
                extra_paths.append(rp)
        served = client.get("/openapi.json").json()
        raw_ops = sum(1 for it in openapi["paths"].values() for m in it if m in ("get", "post", "put", "delete", "patch"))
        served_ops = sum(1 for it in served["paths"].values() for m in it if m in ("get", "post", "put", "delete", "patch"))
        print(
            f"[schema] 真实 paths={len(openapi['paths'])} ops={raw_ops}；"
            f"被中间件改写后的 /openapi.json paths={len(served['paths'])} ops={served_ops}；"
            f"额外真实路由={extra_paths}"
        )
        amap = auth_map(client.app)

        # ---- 登录 ----
        # 临时库副本里的 admin 口令来源未知（可能由 ADMIN_INITIAL_PASSWORD 创建），
        # 因此管理端 token 用 security.create_token 直接签发；login 接口本身用
        # 「本脚本注册的临时用户」来验，避免把「口令未知」误判成接口缺陷。
        login_body = {"username": "admin", "password": "admin123"}
        login = client.post("/api/auth/login", json=login_body)
        token = ""
        if login.status_code == 200 and isinstance(login.json().get("data"), dict):
            token = login.json()["data"].get("token", "")
        if not token:
            from app.utils.security import create_token
            token = create_token(ids["user_id"], "admin", "ADMIN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        if mode == "data":
            # ---- 预置动态 id（都是临时库上的可恢复写）----
            suffix = random.randint(1000, 9999)
            setup_job = dict(BODIES[("POST", "/api/jobs/")], title=f"审计临时岗位-{suffix}")
            r = client.post("/api/jobs/", json=setup_job, headers=headers)
            if r.status_code == 200 and isinstance(r.json().get("data"), (dict, int, str)):
                d = r.json()["data"]
                ctx["job_id_for_write"] = d.get("id") if isinstance(d, dict) else d
            BODIES[("POST", "/api/jobs/")]["title"] = f"审计临时岗位-审计后-{suffix}"
            r = client.post("/api/knowledge", json=BODIES[("POST", "/api/knowledge")], headers=headers)
            if r.status_code == 200:
                d = r.json().get("data")
                ctx["knowledge_id_for_write"] = d.get("id") if isinstance(d, dict) else d
            r = client.post("/api/reports/generate", headers=headers)
            if r.status_code == 200:
                d = r.json().get("data")
                if isinstance(d, dict) and isinstance(d.get("id"), int):
                    ids["report_id"] = d["id"]
            # 注册一个临时用户（用于 users 写接口，避免动 admin；同时用它验证 login）
            uname = f"audit_probe_{suffix}"
            r = client.post("/api/auth/register", json={"username": uname, "password": "audit_pw_1", "email": f"{uname}@example.com"})
            if r.status_code == 200:
                d = r.json().get("data")
                ctx["temp_user_id"] = d.get("id") if isinstance(d, dict) else None
                ctx["temp_user_name"] = uname
                BODIES[("POST", "/api/auth/login")] = {"username": uname, "password": "audit_pw_1"}
            # change-password 单独用一个临时用户，避免影响上面 login 用例的口令
            uname_b = f"audit_pw_{suffix}"
            rb = client.post("/api/auth/register", json={"username": uname_b, "password": "audit_pw_1", "email": f"{uname_b}@example.com"})
            if rb.status_code == 200:
                r2 = client.post("/api/auth/login", json={"username": uname_b, "password": "audit_pw_1"})
                if r2.status_code == 200:
                    ctx["temp_token"] = r2.json()["data"].get("token", "")
                BODIES[("POST", "/api/auth/change-password")] = {
                    "old_password": "audit_pw_1", "new_password": "audit_pw_2",
                }
            BODIES[("POST", "/api/auth/register")] = {
                "username": f"audit_register_{suffix}", "password": "audit_pw_1",
                "email": f"audit_register_{suffix}@example.com",
            }

        for path, item in paths.items():
            for method, op in item.items():
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                method_u = method.upper()
                key = (method_u, path)
                rec = {
                    "method": method_u, "path": path, "auth": amap.get((path, method_u), False),
                    "params": "", "status": "", "schema": "", "source": "", "note": "", "url": "",
                }
                if mode == "empty" and method_u != "GET":
                    rec.update(status="未调用", source="—", note="T3 空库复跑只跑只读接口（GET）")
                    results.append(rec)
                    continue
                if key in SKIP_OPS:
                    rec.update(status="未调用", source="—", note=SKIP_OPS[key] + "（代码已读）")
                    results.append(rec)
                    continue

                # ---- 组装 path / query / body ----
                parts = []
                url = path
                params_by_norm = {}
                for pname, info in [(p["name"], p) for p in op.get("parameters", []) if p["in"] == "path"]:
                    if pname in ("job_id", "jobId"):
                        val = ctx["job_id_for_write"] if method_u in ("PUT", "DELETE") else ids["job_id"]
                    elif pname in ("knowledge_id", "knowledgeId"):
                        val = ctx["knowledge_id_for_write"] if method_u in ("PUT", "PATCH") else ids["knowledge_id"]
                    elif pname in ("user_id", "userId"):
                        val = ctx.get("temp_user_id") or ids["user_id"]
                    elif pname in ("report_id", "reportId"):
                        val = ids["report_id"]
                    elif pname in ("task_id", "taskId"):
                        val = ids["task_id"]
                    elif pname == "keyword":
                        val = "Python"
                    elif pname == "username":
                        val = "admin"
                    else:
                        val = "audit"
                    params_by_norm[pname.replace("_", "").lower()] = val
                    parts.append(f"path:{pname}={val}")
                # 路径模板里的占位符可能与 openapi 参数名大小写不同（{jobId} vs job_id），按归一化名替换
                for placeholder in {seg.split("}")[0].lstrip("{") for seg in url.split("{")[1:]}:
                    norm = placeholder.replace("_", "").lower()
                    if norm in params_by_norm:
                        url = url.replace("{" + placeholder + "}", str(params_by_norm[norm]))

                query = {}
                for pname, info in [(p["name"], p) for p in op.get("parameters", []) if p["in"] == "query"]:
                    val = QUERY.get(pname, 1 if info.get("schema", {}).get("type") == "integer" else "audit")
                    query[pname] = val
                    parts.append(f"query:{pname}={val}")
                rec["params"] = "; ".join(parts) or "无"
                rec["url"] = url

                body = BODIES.get(key, "__none__")
                kwargs = {"headers": headers}
                if query:
                    kwargs["params"] = query
                if body != "__none__" and body is not None:
                    kwargs["json"] = body
                # 采集入口需要令牌头；change-password 走临时用户 token
                if path == "/api/crawler/ingest":
                    kwargs["headers"] = {**headers, "X-Collect-Token": os.environ.get("AUDIT_COLLECT_TOKEN", "")}
                elif path == "/api/auth/change-password" and ctx.get("temp_token"):
                    kwargs["headers"] = {"Authorization": f"Bearer {ctx['temp_token']}"}

                # 兼容路由“创建任务 / 启动任务”等已经跳过；这里发请求
                resp = client.request(method_u, url, **kwargs)
                rec["status"] = resp.status_code
                # 需鉴权的只读接口：顺带验“未登录是否 401”（鉴权不能只靠前端）
                if method_u == "GET" and rec["auth"]:
                    anon = client.get(url, params=query or None)
                    if anon.status_code != 401:
                        rec["note_anon"] = f"未登录返回 {anon.status_code}（期望 401）"
                summary, data = summarize(resp)

                # ---- schema 校验 ----
                resp_schema = (op.get("responses", {}).get("200", {}) or {}).get("content", {}).get(
                    "application/json", {}
                ).get("schema")
                if resp_schema and "json" in resp.headers.get("content-type", ""):
                    errs = validate(resp.json(), resp_schema, openapi)
                    rec["schema"] = "符合" if not errs else "不符：" + "；".join(errs[:3])
                else:
                    rec["schema"] = "无 response schema（未自动校验）"

                # ---- 数据来源 / 备注 ----
                if resp.status_code >= 500:
                    rec["source"] = "报错"
                    rec["note"] = summary[:120]
                elif resp.status_code >= 400:
                    if (method_u, path) in EXPECTED_NOTES:
                        rec["source"] = "—"
                        rec["note"] = EXPECTED_NOTES[(method_u, path)]
                    elif (method_u, path) in EXPECTED_4XX or (mode == "empty" and resp.status_code == 404):
                        rec["source"] = "—"
                        rec["note"] = "预期错误（无对应数据 / 按 id 查不存在）：" + summary[:80]
                    else:
                        rec["source"] = "报错"
                        rec["note"] = summary[:120]
                else:
                    if isinstance(data, list) and not data:
                        rec["source"] = "空"
                    elif isinstance(data, dict) and not data:
                        rec["source"] = "空"
                    elif data == 0:
                        rec["source"] = "空"
                    elif isinstance(data, list):
                        rec["source"] = "有数据"
                    elif isinstance(data, dict):
                        rec["source"] = "有数据"
                    else:
                        rec["source"] = "非业务 JSON"
                    rec["note"] = summary[:120]

                if path in extra_paths:
                    rec["note"] = (rec["note"] + "；" if rec["note"] else "") + "include_in_schema=False，未出现在 openapi 里"
                results.append(rec)
    return results


def to_markdown(results: list) -> str:
    lines = [
        "| # | 方法 + 路径 | 鉴权 | 参数 | 状态码 | schema | 数据来源 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        note = (r["note"] or "")
        if r.get("note_anon"):
            note = (note + "；" + r["note_anon"]).strip("；")
        note = note.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {i} | {r['method']} {r['path']} | {'是' if r['auth'] else '否'} | {r['params']} | "
            f"{r['status']} | {r['schema']} | {r['source']} | {note} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["data", "empty"], default="data")
    parser.add_argument("--json", default="")
    parser.add_argument("--markdown", default="")
    args = parser.parse_args()

    db_path = ""
    if os.environ.get("DB_URL"):
        db_path = os.environ["DB_URL"].replace("sqlite+aiosqlite:///", "")
    results = run_audit(args.mode, db_path)

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(results), encoding="utf-8")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("total:", len(results), "status:", dict(sorted(counts.items(), key=lambda x: str(x[0]))))
    for r in results:
        if r["status"] == "" or (isinstance(r["status"], int) and r["status"] >= 500):
            print("!!", r["method"], r["path"], r["status"], r["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
