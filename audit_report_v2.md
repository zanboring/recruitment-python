# Recruitment Python 项目审计报告 V2

**审计时间**：2026-07-06  
**项目路径**：`C:/Users/zanboring/OneDrive - Ormesby Primary/Desktop/recruitment-python/`  
**审计范围**：37 个 `.py` 文件 + `.env` + `requirements.txt`  
**语法检查**：✅ 全部通过

---

## 项目概览

| 指标 | 数值 |
|------|------|
| Python 源文件 | 37 个 |
| 数据库表模型 | 6 张（User / Job / Company / CrawlTask / KnowledgeBase / SysLog） |
| REST 路由 | 4 组（auth / jobs / ai / crawler） |
| 对比上次审计 | ✅ 上次 10 个阻塞问题已修复 9 个 |
| 总体评估 | 🟡 可以跑，有 5 个阻塞问题 + 7 个警告 |

---

## 对比上次审计的修复情况

| 上次问题 | 状态 |
|----------|------|
| Pydantic v2 `.from_orm()` → `model_validate` | ✅ `JobResponse` 已修复，`model_config = {"from_attributes": True}` |
| `time.sleep()` 阻塞事件循环 | ✅ 已全部替换为 `asyncio.sleep()` |
| JWT_SECRET 占位符 | ✅ `.env` 中已是真实密钥 |
| 文件上传 `file: bytes` 类型错误 | ✅ 已改为 `file: UploadFile` + `await file.read()` |
| CORS_ORIGINS JSON 字符串 | ✅ 现有逻辑兼容 `json.loads` 和逗号分隔两种格式 |
| `scheduler.py` `except: pass` 吞异常 | ✅ 已加 `logger.error(..., exc_info=True)` |
| `__import__('sqlalchemy')` 动态导入 | ✅ 已改为正常 `from sqlalchemy import select` |
| `dependencies.py` 仍用 HTTPException | ✅ 已全部改用 `AppException` |
| `datetime.now()` 无 timezone | ✅ 已全部改用 `datetime.now(timezone.utc)` |
| SQL LIKE 通配符注入 | ✅ 已添加 `_escape_like()` 转义 `%` 和 `_` |

---

## 🔴 阻塞级问题（5 个）

### 1. `schemas/auth.py:29-30` 和 `schemas/user.py:22-23` — Pydantic v1 语法残留

```python
# schemas/auth.py - UserInfoResponse
class Config:           # ← Pydantic v1
    from_attributes = True

# schemas/user.py - UserResponse
class Config:           # ← Pydantic v1
    from_attributes = True
```

**影响**：Pydantic v2 仍兼容 `class Config`，但 deprecated，未来版本移除。JobResponse 已正确使用 `model_config = {"from_attributes": True}`，这两处漏修了。

**修复**：
```python
model_config = {"from_attributes": True}
```

---

### 2. `services/job_service.py:19` — 无效的 `db.get()` 调用

```python
existing = await db.get(Job, job_key)      # job_key 是字符串，get() 用主键(id: int)
stmt = select(Job).where(Job.job_key == job_key)  # 正确写法
existing = result.scalar_one_or_none()
```

**影响**：`db.get()` 按主键 `id` 查询，传入字符串 `job_key` 会静默返回 None。虽然下一行正确查了，但第一行是死代码，占用了不必要的数据库往返。

**修复**：删除第 19 行。

---

### 3. `services/export_service.py:13-14` — 导出只导 20 条

```python
async def export_jobs_to_excel(db: AsyncSession, query_dto: JobQueryDTO):
    jobs = await JobService.query_jobs(db, query_dto)
```

`JobQueryDTO` 默认 `page_size=20`，导出时没有覆盖这个值，所以**永远只导出 20 条**。

**修复**：
```python
query_dto.page_size = 999999  # 导出全部
jobs = await JobService.query_jobs(db, query_dto)
```

---

### 4. `scheduler.py` — 多 Worker 下定时任务重复执行

如果用 `uvicorn --workers 4` 启动，每个 worker 进程都会执行 `start_scheduler()`，导致**凌晨 2 点同时跑 4 份一模一样的爬虫任务**。数据库会被塞入大量重复数据。

**修复方案**（3 选 1）：
- **推荐**：用 `uvicorn main:app --workers 1` 单 worker 启动 scheduler
- 或用 `apscheduler` 的 `MongoJobStore`/`SQLAlchemyJobStore` 防止重复
- 或用 `redis` 分布式锁

---

### 5. 无用户管理路由

`schemas/user.py` 定义了 `UserUpdateRequest` / `UserResponse`，但 `main.py` 没有注册任何用户管理路由。用户登录后无法更新自己的技能、学历、经验——而推荐算法完全依赖这些数据。

**修复**：新建 `app/routers/user.py`：
```python
@router.put("/me")
async def update_profile(request: UserUpdateRequest, db=Depends(get_db), user=Depends(get_current_user)):
    ...
```
并注册到 `main.py`。

---

## 🟡 警告（6 个）

### 6. BOSS 爬虫用 httpx 而非 Playwright

BOSS 直聘是 JS 动态渲染的，`httpx` 只能拿到静态 HTML。`requirements.txt` 装了 `playwright==1.49.0` 但 `boss.py` 完全没用。当前代码大概率爬不到数据。

**修复**：在 `boss.py` 中集成 Playwright 无头浏览器。

---

### 7. AI conversation_history 全局变量不安全

```python
# services/ai_service.py:10
conversation_history: dict[str, list] = {}
```

- 重启丢失所有会话
- 多 worker 之间不共享
- 无 `asyncio.Lock` 保护，并发写入可能丢数据
- `_cleanup_oldest_session()` 用 `next(iter(...))` 删除字典第一个 key——Python 3.7+ 字典有序，但这里删的是最早插入的，逻辑正确但不够明显

**修复**：短期先用 `asyncio.Lock` + `maxsize` LRU 缓存，长期换 Redis。

---

### 8. 登录接口无频率限制

`/api/auth/login` 没有 rate limiting，可被暴力破解密码。虽然已有 `login_fail_count` 锁定机制（5次失败锁 30 分钟），但攻击者可以换用户名继续试。

**修复**：加 `slowapi` 或中间件限制 IP 频率。

---

### 9. `export_service.py:89` — `job_key` 用 `hash()` 不稳定

```python
job_key=f"import_{row}_{hash(str(job_data))}"
```

Python 的 `hash()` 在不同进程/重启后值可能不同（PYTHONHASHSEED）。如果重复导入同一个文件，`job_key` 每次都不一样，无法去重。

**修复**：用 `hashlib.md5` 或 `hashlib.sha256`（参考 `JobService._generate_job_key`）。

---

### 10. `analyzer.py:80-94` — `experience_years`→`exp_str` 转换重复

```python
# recommend_jobs() 内部
if experience_years >= 10:    exp_str = "10年以上"
elif experience_years >= 5:   exp_str = "5-10年"
...
```

和 `salary_predictor.py` 里的 `get_experience_coefficient()` 逻辑高度重叠。应该抽到 `analyzer.py` 作为公共函数 `years_to_experience_string(years: int) -> str`。

---

### 11. `scheduler.py:75` — `task` 变量在异常处理中潜在未定义

```python
try:
    task = CrawlTask(...)  # line 29
    ...
except Exception as e:
    task.status = "FAILED"  # ← 如果 CrawlTask 构造失败，这里 NameError
```

虽然极端情况下 `CrawlTask(...)` 不太可能失败，但防御性编程应处理。

---

## 🟢 建议（6 个）

### 12. `import_jobs_from_excel` 无批量 flush

当前每行 `db.add(job)` 后只在最后 `await db.commit()`。10 万行数据会占用大量内存。建议每 500 行 `await db.flush()`。

### 13. scheduler 中 3 次 `await db.commit()` 冗余

`scheduler.py:69` / `:73` 两次紧挨着的 `commit()`。应该只保留最后一次。

### 14. 缺少 `.gitignore`

项目里没有 `.gitignore`，`__pycache__` 和 `.env` 可能被提交到 Git。

### 15. stats 查询无缓存

`stat_by_skill` 每次全表扫描 `Job.skills`，数据量大时很慢。建议加短期缓存（Redis 或 `functools.lru_cache`）。

### 16. `boss.py` 爬虫异常全吞

```python
except Exception as e:
    break  # ← 不记录日志，静默退出
```

应该至少 log 一下异常原因。

### 17. `cleaner.py:74` — `extract_skills` 可优化

当前逐技能扫描全文本，O(n*m) 复杂度。可以用 `set` 交集或正则一次匹配。

---

## 总结

| 类别 | 数量 | 上次审计 |
|------|------|---------|
| 🔴 阻塞 | 5 | 10 |
| 🟡 警告 | 6 | 11 |
| 🟢 建议 | 6 | - |
| 语法错误 | 0 | 0 |
| 上次问题已修复 | 9/10 | - |

整体项目质量比上次大幅提升。**优先修前 5 个阻塞问题**（特别是 #4 scheduler 多 worker 和 #6 BOSS 爬虫不能用 Playwright），修完就可以跑起来演示了。
