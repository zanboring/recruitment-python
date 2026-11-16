# -*- coding: utf-8 -*-
"""CSV 导入测试：表头解析 / 幂等去重 / 容错。"""
import pytest

from app.services import export_service


CSV_SAMPLE = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,SpringBoot,offline,test,负责后端开发\n"
    "Python工程师,测试公司2,深圳,18000,30000,3-5年,本科,Python,FastAPI,offline,test,负责AI服务\n"
)


@pytest.mark.asyncio
async def test_csv_import_success(db_session):
    result = await export_service.import_jobs_from_csv(
        db_session, CSV_SAMPLE.encode("utf-8")
    )
    assert result["success"] == 2
    assert result["fail"] == 0


@pytest.mark.asyncio
async def test_csv_import_idempotent(db_session):
    data = CSV_SAMPLE.encode("utf-8-sig")  # 带 BOM 也应能解析
    first = await export_service.import_jobs_from_csv(db_session, data)
    second = await export_service.import_jobs_from_csv(db_session, data)
    assert first["success"] == 2
    # 二次导入全部去重
    assert second["success"] == 0
    assert second["skip"] == 2


@pytest.mark.asyncio
async def test_csv_import_empty(db_session):
    with pytest.raises(ValueError):
        await export_service.import_jobs_from_csv(db_session, b"")


# ---------------------------------------------------------------------------
# 状态语义：导入不是「观察」，不能改变已存在记录的状态
#
# 业务决策（2026-11-08）：状态只能由「**观察到它还在**」驱动。
# 爬虫重爬到 = 它现在确实挂着；扫到一次 = 它还活着。
# 而「我手上有一份旧导出表」只说明曾经有过，**不构成它还在的证据**。
#
# 这条原则与行业无关 —— 换成商品、房源、设备台账都一样：
# 一份历史快照不该让已消失的实体重新出现。
# ---------------------------------------------------------------------------

CSV_ONE_JOB = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,2026-01-01,负责后端开发\n"
)


def _job_key_of_csv_row() -> str:
    """按导入器同一套指纹规则算出 job_key（不要手写字符串）。"""
    from app.crawlers.cleaner import generate_job_key

    return generate_job_key("offline", "Java开发工程师", "测试公司", "长沙")


@pytest.mark.asyncio
async def test_csv导入不复活已下架岗位(db_session):
    """已下架的岗位，被旧导出表再导一次，状态必须仍是 CLOSED。

    为什么这条现在就要锁：当前导入器遇到同 job_key 直接 skip，**碰巧**是对的。
    但 qoder 复审提出的方案是把导入器汇入 `save_job`，而 `save_job` 命中存量时
    无条件写 `job_status = "ACTIVE"` —— 一旦照那个方案改，用户导一份旧表
    就会把整批已下架岗位复活，而且没有任何测试会红。
    """
    from app.models.job import Job

    existing = Job(
        title="Java开发工程师",
        company_name="测试公司",
        city="长沙",
        source_site="offline",
        job_key=_job_key_of_csv_row(),
        job_status="CLOSED",
        min_salary=15000,
        max_salary=25000,
    )
    db_session.add(existing)
    await db_session.commit()
    job_id = existing.id

    result = await export_service.import_jobs_from_csv(
        db_session, CSV_ONE_JOB.encode("utf-8")
    )

    assert result["success"] == 0, "同一岗位不该被重复插入"
    assert result["skip"] == 1

    fresh = await db_session.get(Job, job_id)
    assert fresh is not None
    assert fresh.job_status == "CLOSED", (
        "导入把已下架岗位复活了：导入只说明「曾经有过」，不说明「它还在」"
    )


@pytest.mark.asyncio
async def test_csv导入不覆盖存量字段(db_session):
    """旧导出表不能覆盖库里更新的值（薪资 / 标题等）。

    与上一条同源：导入是「补充新记录」，不是「用旧快照回写」。
    """
    from app.models.job import Job

    existing = Job(
        title="Java开发工程师",
        company_name="测试公司",
        city="长沙",
        source_site="offline",
        job_key=_job_key_of_csv_row(),
        job_status="ACTIVE",
        min_salary=30000,      # 库里是更新后的值
        max_salary=50000,
    )
    db_session.add(existing)
    await db_session.commit()
    job_id = existing.id

    await export_service.import_jobs_from_csv(db_session, CSV_ONE_JOB.encode("utf-8"))

    fresh = await db_session.get(Job, job_id)
    assert float(fresh.min_salary) == 30000, "旧导出表覆盖了库里更新的薪资"
    assert float(fresh.max_salary) == 50000


# ==========================================================================
# A1｜批内去重不能依赖 Session.autoflush 的默认值（2026-11-13）
#
# 原实现：循环里先 SELECT 再 add，第二次 SELECT 能看到第一条还没提交的行 ——
# 所以批内重复**碰巧**被计入 skip。这个"碰巧"完全来自 autoflush=True
# （app/database.py 的 async_sessionmaker 没传这个参数），代码里没有一个字
# 提到它，也没有一条用例覆盖它。
#
# autoflush=False 是常见的批量导入提性能手段，一旦有人这么改：
# 两行都进 add，唯一约束要等到最后的 commit() 才抛，函数抛 ValueError，
# **已经算好的 success 整体丢弃**。
# ==========================================================================

CSV_DUP_ROW = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,2026-01-01,负责后端开发\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,2026-01-01,负责后端开发\n"
)


@pytest.mark.asyncio
async def test_csv批内重复只入一行(db_session):
    """锁当前行为：同一文件里两行完全相同 → 1 条入库、1 条 skip。"""
    from sqlalchemy import func, select

    from app.models.job import Job

    result = await export_service.import_jobs_from_csv(
        db_session, CSV_DUP_ROW.encode("utf-8")
    )

    assert (result["success"], result["skip"], result["fail"]) == (1, 1, 0)
    assert result["total"] == 2
    stored = await db_session.execute(select(func.count(Job.id)))
    assert stored.scalar_one() == 1, "批内重复被写进了两条"


@pytest.mark.asyncio
async def test_csv关闭autoflush后批内去重仍然正确(engine):
    """**重点用例**：把 autoflush 关掉，结果必须一模一样。

    这条不能用 ``db_session`` 夹具：它建的是默认会话（autoflush=True），
    测不出"是否依赖默认值"。这里自己造一个 autoflush=False 的会话。
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.job import Job

    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        assert session.autoflush is False, "这条用例的前提就是 autoflush 已关闭"
        result = await export_service.import_jobs_from_csv(
            session, CSV_DUP_ROW.encode("utf-8")
        )

    assert (result["success"], result["skip"], result["fail"]) == (1, 1, 0), (
        "关闭 autoflush 后批内去重失效 —— 说明去重仍依赖 flush 时机"
    )
    assert result["total"] == 2

    # 另开会话查库：不要用上面那个会话（它的身份映射里还留着对象）
    async with maker() as fresh:
        stored = await fresh.execute(select(func.count(Job.id)))
        assert stored.scalar_one() == 1


# ==========================================================================
# A2｜三个计数必须与实际落库一致
# ==========================================================================

CSV_WITH_EMPTY_TITLE = (
    "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,2026-01-01,负责后端开发\n"
    ",空标题公司,长沙,10000,20000,1-3年,本科,,offline,,标题是空的\n"
    "Python工程师,测试公司2,深圳,18000,30000,3-5年,本科,Python,offline,,负责AI服务\n"
)


def _assert_counts_add_up(result, expected_rows):
    assert result["total"] == expected_rows, f"总行数不对：{result}"
    assert (
        result["success"] + result["skip"] + result["fail"] + result["skipped_empty"]
        == result["total"]
    ), f"四个计数加不出总行数：{result}"


@pytest.mark.asyncio
async def test_csv空标题行单独计数且总数可对账(db_session):
    """A2-1：空标题行以前三个计数都不加，调用方看不出少处理了行。"""
    from sqlalchemy import func, select

    from app.models.job import Job

    result = await export_service.import_jobs_from_csv(
        db_session, CSV_WITH_EMPTY_TITLE.encode("utf-8")
    )

    assert result["success"] == 2
    assert result["skipped_empty"] == 1
    assert result["skip"] == 0 and result["fail"] == 0
    _assert_counts_add_up(result, 3)

    stored = await db_session.execute(select(func.count(Job.id)))
    assert stored.scalar_one() == result["success"], "success 与库里实际行数不一致"


class _InsertFailingSession:
    """真实 Session 的薄代理：遇到指定 ``job_key`` 的 INSERT 时注入 IntegrityError。

    为什么要按 key 而不是按「第 N 次 flush」：2026-11-18 起写库改成 Core
    executemany（一次 INSERT 写完整批），flush 计数不再与数据行对应。
    按 key 注入无论走批量路径还是重放路径都能准确命中同一行。

    注入的是真实的 ``sqlalchemy.exc.IntegrityError`` 类型 —— 与唯一约束冲突
    抛出的类型完全相同。
    """

    def __init__(self, real, fail_key: str):
        self._real = real
        self._fail_key = fail_key

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def execute(self, statement, params=None, *args, **kwargs):
        if str(statement).strip().upper().startswith("INSERT"):
            items = params if isinstance(params, (list, tuple)) else [params]
            if any(
                isinstance(p, dict) and p.get("job_key") == self._fail_key for p in items
            ):
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError(
                    "INSERT INTO job", params or {},
                    Exception("UNIQUE constraint failed: job.job_key"),
                )
        return await self._real.execute(statement, params, *args, **kwargs)


@pytest.mark.asyncio
async def test_csv单行写库失败不影响其余行且计数完整(db_session):
    """单行写库失败不影响其余行，四个计数仍然完整。

    2026-11-18 后的实现：批量 executemany 失败 → 回滚整个事务 → 逐行重放并
    剔除那一行 → 其余行照常写入；返回体是真实计数，不是 500 + 半提交。
    """
    from sqlalchemy import func, select

    from app.models.job import Job

    three_rows = (
        "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
        "Java开发工程师,测试公司,长沙,15000,25000,1-3年,本科,Java,offline,,负责后端\n"
        "Python工程师,测试公司2,深圳,18000,30000,3-5年,本科,Python,offline,,负责AI\n"
        "Go工程师,测试公司3,北京,20000,35000,3-5年,本科,Go,offline,,负责网关\n"
    )

    from app.utils.job_key import generate_job_key

    bad_key = generate_job_key("offline", "Python工程师", "测试公司2", "深圳")
    proxy = _InsertFailingSession(db_session, fail_key=bad_key)   # 第 2 行写库失败
    result = await export_service.import_jobs_from_csv(proxy, three_rows.encode("utf-8"))

    assert result == {
        "success": 2,
        "skip": 0,
        "fail": 1,
        "skipped_empty": 0,
        "total": 3,
    }, f"计数与实际不一致（写库失败把后面的行也带崩了）：{result}"

    stored = await db_session.execute(select(func.count(Job.id)))
    assert stored.scalar_one() == 2, "第 1、3 行应该都在库里"

    titles = (await db_session.execute(select(Job.title))).scalars().all()
    assert "Python工程师" not in titles, "失败的那一行不该留下半截数据"
    assert set(titles) == {"Java开发工程师", "Go工程师"}

    # 会话必须仍然可用（回滚后不该进入 PendingRollback 状态）
    await db_session.execute(select(func.count(Job.id)))


@pytest.mark.asyncio
async def test_写库异常确实会让会话进入必须回滚状态(db_session):
    """先证明 A2-3 的机制真实存在，再证明 SAVEPOINT 是对的解法。

    这条与导入器无关：直接在 SQLite 上制造一次唯一约束冲突，然后**不做
    rollback** 继续执行 —— 看 SQLAlchemy 是否抛 PendingRollbackError。

    作用是把 A2-3 从"理论风险"变成"可观测事实"：没有 SAVEPOINT 时，
    导入循环里第一次写库失败之后的所有行都会以这个异常计入 fail。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError, PendingRollbackError

    from app.models.job import Job

    db_session.add(Job(title="A", source_site="import", job_key="dup_test_key"))
    await db_session.flush()

    db_session.add(Job(title="B", source_site="import", job_key="dup_test_key"))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    # 关键：不 rollback，继续用这个会话
    with pytest.raises(PendingRollbackError):
        await db_session.execute(select(Job.id))


@pytest.mark.asyncio
async def test_excel批内重复与空行按同一口径计数(db_session):
    """CSV 与 Excel 共用同一份逐行逻辑，行为必须一致（原来两份同构循环会分叉）。"""
    from io import BytesIO

    from openpyxl import Workbook
    from sqlalchemy import func, select

    from app.models.job import Job

    wb = Workbook()
    ws = wb.active
    ws.append(export_service.EXPORT_HEADERS)
    row = ["Java开发工程师", "测试公司", "长沙", 15000, 25000, "1-3年", "本科",
           "Java", "offline", "2026-01-01", "负责后端开发"]
    ws.append(row)
    ws.append(list(row))            # 批内重复 → skip
    ws.append([None] * len(row))    # 空行 → skipped_empty
    buffer = BytesIO()
    wb.save(buffer)

    result = await export_service.import_jobs_from_excel(db_session, buffer.getvalue())

    assert (result["success"], result["skip"], result["skipped_empty"], result["fail"]) == (
        1, 1, 1, 0
    ), f"Excel 路径口径与 CSV 不一致：{result}"
    _assert_counts_add_up(result, 3)

    stored = await db_session.execute(select(func.count(Job.id)))
    assert stored.scalar_one() == 1


# ==========================================================================
# C｜nginx 上传体积上限（经 8080 入口上传 >1MB 会 413）
#
# nginx 默认 client_max_body_size = 1MB，而对外唯一端口是 8080:80，
# 所有流量都过 docker/nginx.conf。应用侧上限是 IMPORT_MAX_BYTES = 10MB，
# 缺这条配置时它只在直连后端容器端口时才成立。
# ==========================================================================


def _nginx_api_block() -> str:
    """取出 ``location /api/ { ... }`` 块（按花括号配对，不靠行号）。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docker" / "nginx.conf").read_text(
        encoding="utf-8"
    )
    start = text.find("location /api/")
    assert start != -1, "nginx.conf 里找不到 location /api/ 块"
    open_at = text.find("{", start)
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at : index + 1]
    raise AssertionError("location /api/ 块的花括号不配对")


def test_nginx的反代块配了请求体上限():
    block = _nginx_api_block()
    assert "client_max_body_size" in block, (
        "location /api/ 没有 client_max_body_size：nginx 默认 1MB 生效，"
        "经 8080 入口上传 >1MB 的 CSV/xlsx 会 413，根本到不了应用"
    )


def test_nginx请求体上限要略大于应用上限而不是恰好相等():
    """取值必须 > 10MB：恰好 10m 时 multipart 开销会让 nginx 先于应用拒绝。

    那样用户看到的是 nginx 的 413 页面，而不是应用返回的「文件过大，上限 10MB」。
    """
    import re

    from app.services.export_service import IMPORT_MAX_BYTES

    block = _nginx_api_block()
    match = re.search(r"client_max_body_size\s+(\d+)([kKmMgG])\s*;", block)
    assert match, "client_max_body_size 写法无法识别"
    number, unit = int(match.group(1)), match.group(2).lower()
    limit = number * {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit]

    assert limit > IMPORT_MAX_BYTES, (
        f"nginx 上限 {limit} 字节不大于应用上限 {IMPORT_MAX_BYTES} 字节："
        f"multipart 报文开销会让合法文件先被 nginx 413 挡掉"
    )
    assert limit <= IMPORT_MAX_BYTES * 2, (
        f"nginx 上限 {limit} 字节留了过多余量：应用上限之外再放行一倍以上，"
        f"等于让 nginx 不再起兜底作用"
    )


# ==========================================================================
# F1/F2 回归（2026-11-18）：半提交 + 92.5 倍吞吐退化
#
# 上一轮用 `begin_nested()` 逐行 SAVEPOINT 做到"单行失败不拖垮整批"，
# 代价是在 SQLite 的 legacy 事务模式下每条 RELEASE 都等于 COMMIT：
#   * 外层异常时出错点之前的行已经落盘，接口却只吐 500、计数全丢；
#   * 1 万行实测 32.37s（qoder 复审），nginx 12m 入口能装几十万行 → 300s 504。
# 现在改成 Core executemany 一次写入，失败才逐行重放剔除坏行。
# ==========================================================================

def _make_large_csv(n: int) -> bytes:
    header = "标题,公司,城市,薪资(min),薪资(max),经验,学历,技能,来源平台,发布时间,描述\n"
    lines = [
        f"岗位{i},公司{i},长沙,10000,20000,1-3年,本科,Python,import,,描述{i}"
        for i in range(n)
    ]
    return (header + "\n".join(lines) + "\n").encode("utf-8")


async def test_一万行导入耗时不得退化到逐行提交(tmp_path):
    """**吞吐回归**：10 万字节级文件要能在 5 秒内导完。

    阈值放宽到 5s 是为了不把 CI 抖动做成红测；实测正常在 0.2s 量级，
    旧的逐行 SAVEPOINT 实现是 32s —— 这个阈值仍能抓住任何数量级退化。
    """
    import time

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import Base as ModelBase
    from app.models.job import Job

    engine = await _make_file_engine(tmp_path / "throughput.db", ModelBase)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            started = time.perf_counter()
            result = await export_service.import_jobs_from_csv(db, _make_large_csv(10000))
            elapsed = time.perf_counter() - started

        assert result["success"] == 10000, result
        assert elapsed < 5.0, (
            f"1 万行耗时 {elapsed:.2f}s —— 逐行提交退化回来了（旧实现 32.37s）"
        )

        async with maker() as fresh:
            stored = (await fresh.execute(select(func.count(Job.id)))).scalar_one()
        assert stored == 10000, f"返回说写进 10000 条，库里只有 {stored} 条"
    finally:
        await engine.dispose()


async def test_外层解析异常时不留半批已提交行(tmp_path):
    """生成器在第 4 行抛错（CSV 字段超长等价物）：库里必须是 0 行。

    旧实现下前 3 行已经被逐行 RELEASE 提交，重开连接仍看得到 —— 接口 500
    加半批数据，用户重试时这 3 行又变成 skip，谁都不知道发生过什么。
    """
    import sqlite3

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import Base as ModelBase
    from app.models.job import Job

    path = tmp_path / "half_commit.db"
    engine = await _make_file_engine(path, ModelBase)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    def broken_rows():
        for i in range(3):
            yield {"标题": f"半提交{i}", "公司": "某公司", "城市": "长沙"}
        raise ValueError("field larger than field limit (131072)")

    try:
        async with maker() as db:
            with pytest.raises(ValueError, match="field larger"):
                await export_service._import_rows(db, broken_rows(), label="CSV")
            await db.rollback()          # 调用方（import_jobs_from_csv）的异常分支会做这一步

        # 用**新连接**读：排除"当前 session 还没 flush"造成的假阴性
        conn = sqlite3.connect(str(path))
        stored = conn.execute("select count(*) from job").fetchone()[0]
        conn.close()
        assert stored == 0, f"外层异常留下了 {stored} 行半提交数据"

        async with maker() as fresh:
            assert (await fresh.execute(select(Job.id))).all() == []
    finally:
        await engine.dispose()


async def _make_file_engine(path, model_base):
    """给上面两条用例建一个独立的 SQLite **文件**库（用真表结构）。

    不用测试夹具的内存库：内存库是 StaticPool 单连接，"半提交"这种跨连接
    可见性验证在那里测不出来。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(model_base.metadata.create_all)
    return engine