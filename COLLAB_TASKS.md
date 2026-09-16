# 任务分工方案（WorkBuddy ↔ Trae 并行开发）

> 状态：**待用户确认**。确认后各自只在自己的分支上开发，完成后由用户验收并合并回 `main`。

---

## 0. 分支约定（已核验的实际状态）

| 分支 | 归属 | 当前起点 | 说明 |
|---|---|---|---|
| `main` | 用户 | `9298319` | 稳定基线，只接受验收通过的合并 |
| `dev-workbuddy` | **WorkBuddy** | `9298319` + `a5619bc` | 我（WorkBuddy）的开发分支 |
| `dev-trae` | **Trae** | `9298319` | Trae 的开发分支 |

> ⚠️ `COLLAB_HANDOFF.md` 第 1 节提到的 `dev-collab` **从来不存在**，该名称已废弃，不要再建。
> 实际流程以仓库根目录的 `git-trae-branch.cmd` / `git-merge-main.cmd` 为准。

---

## 1. 协作纪律（含已实际踩过的坑）

1. **同时只能有一个助手操作 git。**
   本目录只有**一个工作区**，三个分支共用。一方执行 `git checkout` / `git reset --hard` 时，
   另一方未提交的改动会被**静默丢弃**——本会话已真实发生一次：WorkBuddy 对
   `tests/conftest.py` 的修复被并发执行的 `reset --hard` 冲掉，只能重做。
   → 约定：**改完立刻 commit**；切分支前先 `git status` 确认工作区干净。
2. **禁止 `git add -A` 盲提。**
   `.gitignore` 已正确排除 `dist/`、`build/`、`frontend/node_modules/`、`config.env`、
   `reports/daily/`，但这依赖分支上的 `.gitignore` 生效。切分支时工作区文件不跟随，
   盲提会把 16764 个 `node_modules` 文件和 12.7MB 的 exe 一起提交上去。
3. **`app/config.py` 是共用的高频冲突文件。**
   新增配置项一律**追加在 `Settings` 类末尾**，不要插在中间，降低 merge 冲突。
   两个分支改到同一区间时，合并方手工解一次即可。
4. **改完必须验证**（项目既有约定）：
   - 后端：`.venv\Scripts\python.exe -m pytest -q`（当前基线 **466 项全绿**）
   - 前端：`cd frontend && npm run build`
   - 打包相关：重跑 `build-exe.bat`

---

## 2. 待办清单（来源：AI_APPLICATION_HANDOFF.md §7，已逐条核验）

| # | 事项 | 优先级 | 状态 |
|---|---|---|---|
| A | 默认管理员口令 `admin/admin123` 硬编码在 `app/init_data.py` | P0 | 未做 |
| B | `TOOLS` 只有 `query_jobs` 一个，缺多工具编排能力 | P1 | 未做 |
| C | 限流是单进程内存实现，多副本部署即失效 | P2 | 未做 |
| D | 向量只存内存缓存，进程重启整库重新向量化（云端要花钱） | P2 | 未做 |
| E | 容器化（docker-compose + Dockerfile） | P0 | ✅ 已完成 |
| F | 测试不 hermetic：结果随开发者本机 `.env` 漂移 | — | ✅ 已修 `a5619bc` |

---

## 3. 分工提案（按文件边界切，尽量零重叠）

### WorkBuddy 负责 —— AI 能力层（B + D）

| 任务 | 主要文件 |
|---|---|
| B. 多工具编排：在 `TOOLS` 注册表上横向扩展（薪资对比 / 公司画像 / 技能缺口），让 ReAct Agent 能多步编排 | `app/services/tool_service.py`<br>`app/services/ai_service.py`<br>`app/services/react_agent.py`<br>`tests/test_tool_service.py`<br>`tests/test_tool_prefilter.py`<br>`tests/test_react_agent.py` |
| D. 向量持久化：新建向量表，启动时复用已算好的向量，避免重启整库重算 | `app/services/embedding_service.py`<br>`app/models/`（新增模型文件）<br>`tests/test_embedding_service.py` |

### Trae 负责 —— 安全与基础设施（A + C）

| 任务 | 主要文件 |
|---|---|
| A. 默认口令安全化：首次启动随机生成密码 / 强制改密；生产环境拒绝默认值 | `app/init_data.py`<br>`app/config.py`<br>`scripts/init_db.py`<br>`.env.example`<br>`config.template.env` |
| C. 分布式限流：把单进程内存计数器换成 Redis 计数器，无 Redis 时降级回内存 | `app/middleware/rate_limit.py`<br>`app/cache.py`<br>`tests/test_rate_limit.py` |

### 边界说明

- `app/config.py`：**双方都可能新增配置项**。按第 1 节第 3 条约定追加在类末尾。
- `app/database.py`：D 需要注册新表 → 归 **WorkBuddy** 改动。
- `README.md`：谁的功能谁补自己那一段，合并时由用户处理一次冲突。
- `app/services/ai_service.py`：**归 WorkBuddy**，Trae 如需要请提需求。

---

## 4. 验收方式

1. 每个任务完成后，在**自己的分支**上跑通全量测试（当前基线 466 项，只增不减）。
2. 向用户汇报：改动文件清单 + 验证命令 + 实测结果。
3. 用户验收通过后，由用户执行合并（`git merge`）回 `main`。
4. 合并顺序建议：先合一条，跑一次全量测试，再合第二条——避免两处改动互相掩盖问题。

---

## 5. 附：本机核验过的基线事实

- `main` = `9298319`，214 个文件；`app/` 80、`frontend/` 60、`tests/` 39
- 未提交任何构建产物或密钥（`dist/`、`build/`、`node_modules/`、`config.env` 均未入库）
- 测试：**466 项全绿**（与 `README.md` 记录一致；
  废弃脚本 `git-collab.cmd` 的提交信息里写的「624 项」是错的，不要引用那个数字）
- 打包产物：`dist/RecSys/RecSys.exe`，12.7 MB
- 本机 venv：`.venv\Scripts\python.exe`（Python 3.11.9）
