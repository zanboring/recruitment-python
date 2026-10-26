"""Schema 与模型定义的一致性检查与自动补齐。

**背景（这是一个真实缺陷的修复）**

``Base.metadata.create_all`` 只创建**不存在**的表，**不会**给已存在的表加列 ——
这是 SQLAlchemy 的既定语义，不是 bug。于是「给模型加一列」这个动作在
**已有数据库**上不会生效，而后果极其隐蔽：

- 启动日志显示一切正常（``create_all`` 无报错、默认管理员创建成功）
- 但任何查询该表的语句都会带上新列 → ``Unknown column`` / ``no such column``
  → 接口一律 HTTP 500
- 错误信息是数据库方言原文，对使用者没有任何指导性

而且**影响面是"该表的全部查询"而非"用到新列的那一个"** —— 因为 SQLAlchemy 的
``SELECT`` 会列出模型里的所有列。所以给 ``job`` 加一列，会让岗位列表、统计图表、
AI 工具查询**同时**挂掉。

此前这个风险靠 ``README`` 的「表结构变更（升级须知）」手工补列清单兜底，但
**该清单已经漏项**：``job.last_checked_at`` 是后加的列，对应的手工补列语句
从未被写进 README。这证明「靠人记得同步文档」这条路径不可靠 ——
文档是给人读的，而 schema 漂移会在**没人读文档时**爆发。

本模块把它变成自动的：启动时比对「模型定义的列」与「数据库实际的列」，
对缺失的列执行 ``ALTER TABLE ... ADD COLUMN``。

**安全边界（刻意保守）**：

- **只增列**，绝不删除列、绝不修改类型或约束 —— ``ADD COLUMN`` 对已有数据安全
- 主键 / 唯一约束 / ``NOT NULL`` 且无服务端默认值的列**不自动处理**，
  改为明确报告（这些改动不可能是"无感升级"，需要人判断）
- 表本身不存在时不处理 —— 那是 ``create_all`` 的职责
- 全过程记录执行过的 DDL，便于审计
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

from app.database import Base

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """一次 schema 同步的结果。

    ``added`` 用于确认"确实补了列"；``skipped`` 记录**无法安全自动处理**的列，
    调用方应把它作为需要人工介入的信号呈现出来（而不是只记日志）。
    """

    added: List[Tuple[str, str]] = field(default_factory=list)
    skipped: List[Tuple[str, str, str]] = field(default_factory=list)

    @property
    def has_added(self) -> bool:
        return bool(self.added)

    def summary(self) -> str:
        parts = []
        if self.added:
            cols = "、".join(f"{t}.{c}" for t, c in self.added)
            parts.append(f"已自动补齐 {len(self.added)} 个缺失列：{cols}")
        if self.skipped:
            detail = "；".join(f"{t}.{c}（{why}）" for t, c, why in self.skipped)
            parts.append(f"{len(self.skipped)} 个列需人工处理：{detail}")
        return "；".join(parts) if parts else "schema 与模型一致，无需变更"


def _can_auto_add(column) -> Tuple[bool, str]:
    """判断某列能否安全地通过 ``ADD COLUMN`` 补齐。

    返回 ``(能否, 不能的原因)``。保守原则：只要可能让已有行违反约束，就不自动加。
    """
    if column.primary_key:
        return False, "主键列"
    if column.unique:
        return False, "带唯一约束（已有行可能重复）"
    if not column.nullable and column.server_default is None:
        # 已有行填不出值 → DDL 会失败，或（更糟）在某些方言下被静默容忍
        return False, "NOT NULL 且无服务端默认值，已有行无法满足"
    return True, ""


def _plan_missing_columns(sync_conn):
    """同步侧回调：算出「表已存在但缺列」的清单。

    在 ``run_sync`` 里执行，因此可以用同步的 ``inspect``。
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    plan = []
    for table_name, table in Base.metadata.tables.items():
        # 表不存在 → 交给 create_all，不在这里处理
        if table_name not in existing_tables:
            continue
        actual_columns = {c["name"] for c in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual_columns:
                plan.append((table_name, column))
    return plan


async def sync_missing_columns(conn) -> SyncResult:
    """把「模型有、数据库没有」的列补上。只增列，不会删除或修改任何东西。

    传入一个已打开的 ``AsyncConnection``（与 create_all 同一个连接即可）。
    """
    result = SyncResult()

    plan = await conn.run_sync(_plan_missing_columns)
    if not plan:
        return result

    preparer = conn.dialect.identifier_preparer
    for table_name, column in plan:
        allowed, reason = _can_auto_add(column)
        if not allowed:
            result.skipped.append((table_name, column.name, reason))
            logger.error(
                "数据库表 %s 缺少列 %s，但无法自动补齐（%s）。"
                "该表上的查询会持续报「未知列」并返回 500，请手工处理。",
                table_name, column.name, reason,
            )
            continue

        # CreateColumn 生成的是「列定义」片段（含类型、NULL 约束、默认值），
        # 拼进 ALTER TABLE 就是标准的加列语句，且类型按当前方言编译。
        column_ddl = str(CreateColumn(column).compile(dialect=conn.dialect))
        statement = (
            f"ALTER TABLE {preparer.quote(table_name)} ADD COLUMN {column_ddl}"
        )
        try:
            await conn.execute(text(statement))
        except Exception as exc:  # noqa: BLE001  一条失败不该拖垮其余列
            result.skipped.append((table_name, column.name, f"执行失败：{exc}"))
            logger.error(
                "自动补齐列 %s.%s 失败：%s（语句：%s）",
                table_name, column.name, exc, statement,
            )
            continue

        result.added.append((table_name, column.name))
        logger.warning(
            "检测到数据库缺列并已自动补齐：%s.%s（语句：%s）。"
            "旧库结构落后于代码，补齐后该表查询恢复正常。",
            table_name, column.name, statement,
        )

    return result
