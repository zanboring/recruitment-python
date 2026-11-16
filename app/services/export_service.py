"""岗位数据 Excel 导入导出。

设计要点：
- **导入去重口径与爬虫、管理端新增完全一致**（app.utils.job_key）。原先导入用的
  job_key 含行号与整行内容哈希，同一份文件二次导入会生成不同的键，结果要么
  重复入库、要么撞唯一约束导致整个事务回滚、一条都进不去。
- 已存在的记录按「跳过」处理并计入 skip，而不是报错回滚。
- 导出用 model_copy 复制 DTO：直接改传入对象会污染调用方状态（原实现有此副作用）。
- 导出与导入都设了体量上限，避免一次操作把内存打满。
"""
import logging
from io import BytesIO
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.services.job_service import JobService
from app.schemas.job import JobQueryDTO
from app.utils.job_key import generate_job_key

logger = logging.getLogger(__name__)

# 单次导出行数上限：避免误点导出把几十万行一次性读进内存
EXPORT_MAX_ROWS = 50000

# 单次导入文件大小上限（字节）。xlsx 是压缩包，10MB 已可容纳数十万行
IMPORT_MAX_BYTES = 10 * 1024 * 1024

EXPORT_HEADERS = [
    "标题", "公司", "城市", "薪资(min)", "薪资(max)", "经验",
    "学历", "技能", "来源平台", "发布时间", "描述",
]


def _to_float(value) -> float:
    """把单元格值安全转成 float；空值或脏数据返回 0.0。

    不用 float(...) 裸转：单元格为空时 float(None) 会抛 TypeError，
    整行会被判为失败行。
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def export_jobs_to_excel(db: AsyncSession, query_dto: JobQueryDTO):
    """按查询条件导出岗位为 xlsx 文件流。"""
    # 复制一份再改分页：直接改传入的 DTO 会污染调用方对象状态
    export_dto = query_dto.model_copy(update={"page_num": 1, "page_size": EXPORT_MAX_ROWS})
    jobs = await JobService.query_jobs(db, export_dto)

    wb = Workbook()
    ws = wb.active
    ws.title = "岗位数据"
    ws.append(EXPORT_HEADERS)

    for job in jobs:
        publish_time = job.publish_time.strftime("%Y-%m-%d") if job.publish_time else ""
        ws.append([
            job.title,
            job.company_name,
            job.city,
            job.min_salary,
            job.max_salary,
            job.experience,
            job.education,
            job.skills,
            job.source_site,
            publish_time,
            job.job_desc or "",
        ])

    for col in ws.columns:
        max_length = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except (TypeError, AttributeError):
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws.column_dimensions[col[0].column_letter].width = min(adjusted_width, 50)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"jobs_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )



async def _import_rows(db: AsyncSession, rows, *, label: str) -> dict:
    """逐行导入岗位（CSV 与 Excel **共用这一份**逻辑）。

    为什么抽成一个函数：这两个入口原本是同构的两份循环，缺陷也就成对存在
    （A1/A2 都是"改一处漏一处"的典型）。现在只有一份可改，也就不会再分叉。

    **计数口径**（返回体的每个数字都必须能与库里的行对上）：

    ===============  ==================================================
    ``total``        数据行数（不含表头；空行也算一行）
    ``success``      真正写进库的行数 —— ``flush()`` 成功之后才 +1
    ``skip``         库里已存在，或本批前面出现过同一个 ``job_key``
    ``skipped_empty`` 标题为空的行（空行 / 只有逗号的脏行）
    ``fail``         解析失败或写库失败的行
    ===============  ==================================================

    恒等式：``success + skip + fail + skipped_empty == total``。
    有了它，调用方才可能发现「文件里有 100 行，但只有 60 行被处理过」。

    **批内去重显式用 ``seen``，不依赖 ``Session.autoflush``**：
    原实现靠「第二次 SELECT 能看到第一条还没提交的行」来判重，而这个行为完全来自
    ``autoflush=True`` 的默认值 —— 代码里没有一个字提到它，也没有一条测试覆盖它。
    ``autoflush=False`` 是常见的批量导入提性能手段，一旦有人这么改，两行都会进
    ``add``，唯一约束要等到最后的 ``commit()`` 才抛，函数抛 ``ValueError``，
    **已经算好的 success 会整体丢弃**。现在把「我见过什么」自己记下来。

    **为什么不再用 ``begin_nested()``（2026-11-18 修 F1/F2）**：
    本项目跑的是 SQLite 的 legacy 事务模式（``app/database.py`` 只设了
    ``busy_timeout``，没有给连接挂 ``isolation_level=None`` + ``BEGIN`` 钩子）。
    这种模式下 ``Session.begin()`` 只开**逻辑**事务，DBAPI 不会发物理 ``BEGIN`` ——
    于是第一条 ``SAVEPOINT`` 就是最外层事务，每个 ``RELEASE`` 都等于一次 COMMIT：
    * 出错点之前的行已经永久落盘，而异常路径只拿到 500，三个计数全丢（半提交）；
    * 1 万行实测 32.37s，比整批一个事务的 0.35s 慢 **92.5 倍**（逐行建删日志文件）。
    现在改成「一个事务 + 逐行 flush + 失败重放」：正常路径只有最后一次 COMMIT；
    某一行写库失败时回滚整个事务、把那一行标 fail，然后**重放其余行**，
    既保住"单行失败不影响整批"，又不会出现"库里几行、接口 500"。
    """
    total = 0
    seen: set = set()
    # 每行一个条目，状态只有四种，最终计数直接从这里汇总 ——
    # 恒等式 success+skip+fail+skipped_empty == total 是**结构上**成立的，
    # 不靠各分支自己记得 +1。
    entries: list = []

    # ---------------- 阶段一：纯内存预检 + 一次批量查库 ----------------
    for raw in rows:
        total += 1
        entry = {"status": "pending", "kwargs": None, "line": total, "reason": ""}
        entries.append(entry)
        try:
            row = raw or {}
            title = str(row.get("标题") or "").strip()
            if not title:
                entry["status"] = "skipped_empty"
                continue

            company_name = str(row.get("公司") or "").strip()
            city = str(row.get("城市") or "").strip()
            source_site = str(row.get("来源平台") or "").strip() or "import"
            job_key = generate_job_key(source_site, title, company_name, city)

            # 批内去重：显式记账，与 flush 时机无关（autoflush 开/关结果一致）
            if job_key in seen:
                entry["status"] = "skip"
                continue
            seen.add(job_key)

            entry["kwargs"] = {
                "title": title,
                "company_name": company_name,
                "city": city,
                "min_salary": _to_float(row.get("薪资(min)")),
                "max_salary": _to_float(row.get("薪资(max)")),
                "experience": str(row.get("经验") or ""),
                "education": str(row.get("学历") or ""),
                "skills": str(row.get("技能") or ""),
                "source_site": source_site,
                "job_key": job_key,
                "job_status": "ACTIVE",
                "job_desc": str(row.get("描述") or ""),
            }
        except Exception as e:  # noqa: BLE001 - 单行脏数据不影响其余行
            entry["status"] = "fail"
            entry["reason"] = f"{type(e).__name__}: {e}"
            logger.warning("%s 第 %s 行解析失败：%s", label, total, e)

    # 库内去重：分块 IN 查询（SQLite 变量上限 999，500 一块留足余量），
    # 不再每行一条 SELECT。1 万行从 1 万条语句降到 20 条。
    candidates = [e for e in entries if e["status"] == "pending"]
    keys = [e["kwargs"]["job_key"] for e in candidates]
    existing_keys: set = set()
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        found = await db.execute(select(Job.job_key).where(Job.job_key.in_(chunk)))
        existing_keys.update(found.scalars().all())
    for entry in candidates:
        if entry["kwargs"]["job_key"] in existing_keys:
            entry["status"] = "skip"
            entry["kwargs"] = None

    # ---------------- 阶段二：一个事务写库，失败行重放时跳过 ----------------
    # 正常路径：每条 flush 只发 INSERT，pysqlite 在第一条 INSERT 前自动 BEGIN，
    # 最后调用方一次 commit() → 要么全进，要么全不进。
    # 异常路径：flush 抛错 → rollback 掉本事务（此前 flush 的行一并撤销），
    # 把那一行永久标 fail，剩下的重新 add+flush。最多重放 N 次（N=候选行数），
    # 每行只可能被标一次 fail，因此必然终止。
    writable = [e for e in entries if e["status"] == "pending"]
    payloads = [e["kwargs"] for e in writable]
    _INSERT = insert(Job.__table__)   # Core INSERT：不带 RETURNING，可 executemany
    for _attempt in range(len(writable) + 1):
        try:
            if payloads and _attempt == 0:
                # 常规路径：**一次** executemany 写完所有行。
                # 实测（1 万行，同一台机器）：解析 0.036s + 批量去重 SELECT 0.031s
                # + 这里 0.090s ≈ 0.16s；而 ORM 逐行 add/flush 要 4.75~8.5s，
                # 旧的逐行 SAVEPOINT 更是 32.37s。
                await db.execute(_INSERT, payloads)
                for entry in writable:
                    entry["status"] = "ok"
                break
            # 重放路径（只有常规路径失败时才走）：逐条 INSERT 才能定位到具体哪一行。
            for entry in writable:
                if entry["status"] != "pending":
                    continue
                await db.execute(_INSERT, entry["kwargs"])
                entry["status"] = "ok"
            break
        except Exception as exc:  # noqa: BLE001 - 定位到具体那一行后重放
            await db.rollback()
            flushed = sum(1 for e in writable if e["status"] == "ok")
            for e in writable:
                if e["status"] == "ok":
                    e["status"] = "pending"           # 随事务一起没了，要重来
            if _attempt == 0:
                # 批量 executemany 失败时**无法知道是哪一行**导致的（驱动只报一句
                # UNIQUE constraint failed），不能随便把第一行当坏行 —— 直接重放：
                # 逐条 INSERT 才能定位。多行里只有一行坏时，这里只多花一次重放。
                continue
            remaining = [e for e in writable if e["status"] == "pending"]
            if flushed < len(remaining):
                # flush 失败时，那一块的所有行都还标着 pending；
                # remaining[flushed] 就是本轮第一个没写进去的行 —— 把它永久剔除，
                # 下一轮重放其余行。若错误其实是"整批性的"，每行都会被逐个剔除，
                # 最终 fail = 全部，但**计数如实返回**，不会有半提交。
                bad = remaining[flushed]
                bad["status"] = "fail"
                bad["reason"] = f"{type(exc).__name__}: {exc}"
                bad["kwargs"] = None
                logger.warning(
                    "%s 第 %s 行写库失败（已跳过并继续）：%s", label, bad["line"], exc
                )
                continue
            # 所有行都 flush 成功才抛错（例如 commit 阶段故障）→ 整批算失败
            for e in writable:
                e["status"] = "fail"
                e["reason"] = f"{type(exc).__name__}: {exc}"
                e["kwargs"] = None
            break

    counts = {"ok": 0, "skip": 0, "fail": 0, "skipped_empty": 0}
    for entry in entries:
        # 任何没走到终态的行都算 fail，不让恒等式因为一条漏网之鱼而不成立
        status = entry["status"] if entry["status"] in counts else "fail"
        counts[status] += 1
    return {
        "success": counts["ok"],
        "skip": counts["skip"],
        "fail": counts["fail"],
        "skipped_empty": counts["skipped_empty"],
        "total": total,
    }


async def import_jobs_from_csv(db: AsyncSession, file_content: bytes) -> dict:
    """从 CSV 导入岗位，返回 {"success","skip","fail","skipped_empty","total"}（幂等）。

    表头字段与 xlsx 导出一致（中文表头）：
    标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述

    复用 import_jobs_from_excel 的逐行容错与 job_key 去重口径 ——
    同一岗位从 CSV / 爬虫 / 管理端三个入口进入都不会重复入库。
    """
    import csv
    import io

    if not file_content:
        raise ValueError("文件内容为空")
    if len(file_content) > IMPORT_MAX_BYTES:
        raise ValueError(f"文件过大，上限 {IMPORT_MAX_BYTES // 1024 // 1024}MB")

    try:
        text = file_content.decode("utf-8-sig")  # 兼容 UTF-8 BOM（Excel 导出常见）
        reader = csv.DictReader(io.StringIO(text))
        result = await _import_rows(db, reader, label="CSV")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise ValueError(f"CSV 导入失败: {e}")

    return result


async def import_jobs_from_excel(db: AsyncSession, file_content: bytes) -> dict:
    """从 xlsx 导入岗位，返回 {"success","skip","fail","skipped_empty","total"} 统计。

    - job_key 相同的记录计入 skip（幂等：同一份文件重复导入不会产生重复数据，
      也不会因唯一约束冲突而整体回滚）；
    - 单行脏数据在预检阶段剔除，写库失败回滚后重放其余行，都不影响整批；
    - 空行（Excel 常见的尾部空行）计入 skipped_empty。
    """
    if not file_content:
        raise ValueError("文件内容为空")
    if len(file_content) > IMPORT_MAX_BYTES:
        raise ValueError(f"文件过大，上限 {IMPORT_MAX_BYTES // 1024 // 1024}MB")

    try:
        wb = load_workbook(filename=BytesIO(file_content), read_only=True)
        ws = wb.active

        rows = ws.iter_rows(values_only=True)
        raw_headers = next(rows, None)
        if raw_headers is None:
            raise ValueError("文件没有表头")
        headers = [str(h) if h is not None else "" for h in raw_headers]

        # 逐行 dict 化后再交给共用的逐行逻辑（与 CSV 完全同一条路径）
        dict_rows = (dict(zip(headers, values)) for values in rows)
        result = await _import_rows(db, dict_rows, label="Excel")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise ValueError(f"导入失败: {e}")

    return result
