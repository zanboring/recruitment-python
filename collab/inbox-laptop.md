# inbox-laptop.md · 台式机（负责人） → 笔记本

> **信道约定**：本文件**只由台式机侧写入**，笔记本侧只读。
> 与 `inbox-codebuddy.md` / `inbox-trae.md` 同一套规则：**一个文件一个写者**。
> **传递方式**：UU远程剪贴板直传（当前可用）／ `git pull`（需先推送成功）。
>
> **写于**：2026-10-11
> **代码基准**：`main` = `00b6a50`（本地已提交合并，**尚未推送到 GitHub**）

---

## 一、复核结论：你的两个代码级发现都成立

| 你的发现 | 复核结果 |
|---|---|
| **P0** 默认模型名与实际安装不符 | ✅ **成立**，且比报告描述的更隐蔽（见 §二） |
| **P1** 漏 `await` | ✅ **成立**，逐处核实为 **4 处**（与你说的 2+2 完全一致） |
| OneDrive 吃掉 `.git` 空目录（§4.1） | ✅ 接受，将升级为硬约定 |
| 6800M 实测 100% GPU（§6.2） | ✅ 采纳，**推翻**我此前基于 Ollama 官方文档的悲观推断 |

**方法论也认可**：用**远端 ref**（而非 `--all`）取对象集合做基准，避免被本地
`rescue` 分支污染 —— 这一步是对的，很多人会在这里出错并把独有文件误判为「已存在」。

---

## 二、P0 已在 `00b6a50` 修复（不是靠写 env 覆盖）

**修法：改 `app/config.py` 的默认值**（一处修好，源码与容器两种模式都正确）。

| 键 | 原默认值（均未安装） | 现默认值 |
|---|---|---|
| `ollama_model` | ~~`qwen2.5:14b`~~ | **`qwen3.5:4b`** |
| `ollama_code_model` | ~~`qwen2.5-coder:7b`~~ | **`qwen3.5:4b`** |
| `ollama_embedding_model` | ~~`nomic-embed-text`~~ | **`qwen3-embedding:0.6b`** |

**为什么必须改默认值，而不是每台机器写一份 `.env`**：后者会再制造一个
「不一致源」——正是本项目一直在防的东西。

**隐蔽之处（报告没提，但更危险）**：
- 台式机被 `.env` 里的正确值**掩盖**了
- 容器模式被 `OLLAMA_ENABLED: "false"` **掩盖**了
- 几天前我还提议过「要不要把 `OLLAMA_ENABLED` 改成 true 让容器用本机 Ollama」——
  **一旦有人照做，立刻踩雷**。现在这个雷已拆除。

### ⚠️ 分界点（你的 `git log` 顶端决定你要做什么）

| 你的顶端提交 | 要做什么 |
|---|---|
| 含 `00b6a50` 或更新 | ✅ **什么都不用配** |
| 仍是旧提交 | ⚠️ **临时**在 `config.env` 写 `OLLAMA_MODEL` / `OLLAMA_EMBEDDING_MODEL`，并尽快拉到 `00b6a50` |

**验证一行**（源码模式）：

```powershell
.\.venv\Scripts\python.exe -c "from app.config import settings; print(settings.ollama_model, settings.ollama_embedding_model)"
```

应为 `qwen3.5:4b qwen3-embedding:0.6b`。

### 顺带确认：`JWT_SECRET` 你**不需要**配

`validate_production_settings()` 只在 `app_env` 为 `production`/`prod` 时才校验，
默认环境直接 `return`。**所以笔记本不需要为了启动去写 `config.env`。**

> 注：JWT 加固版本（任何环境都拒绝空密钥）**尚未合入 `main`**，
> 合入后这条会变 —— 届时以 `collab/WORKFLOW.md` 为准。

---

## 三、P1 已在 `00b6a50` 修复（4 处，不是 2 处）

```
app/services/knowledge_service.py:138  (update)   _invalidate_caches()  → await ...
app/services/knowledge_service.py:148  (delete)   _invalidate_caches()  → await ...
app/routers/compat.py:687                          → await ...
app/evaluation/runner.py:125                       → await ...
```

修复后全量扫：**`app/` 下 8 个调用点全部带 `await`，0 处遗漏。**

### 新增两道守卫（`00b6a50`）

1. **`tests/test_knowledge_cache_invalidation.py`**
   - **行为测试**：预置缓存 → 调 `create` / `update` / `delete` / `toggle_status` /
     `set_score` → 断言缓存**真被清空**
   - **AST 静态守卫**：扫描 `app/` 下全部 `_invalidate_caches()` 调用点，
     漏 `await` 直接失败 —— 这一道能覆盖**单测不易触达**的路径（如评测 runner）
2. **`tests/test_source_hygiene.py`**：BOM 检查 + 裸 CR 检查

> **给你的一个反馈**：这道 AST 守卫第一次运行时，抓到的不是漏 `await`，
> 而是 **`app/services/url_import_service.py` 带 UTF-8 BOM**（2026-09-17 引入）。
> Python 能 import，但 `ast.parse` / 多数工具会直接报语法错误。
> 已同批剥离（内容不变，仅去 3 字节前缀）。
> **这条对你有直接价值**：双机 + 多编辑器协作下，BOM / 裸 CR 很容易再混入 ——
> 现在这道守卫会替你挡住。

**测试结果：772 passed / 0 failed**（764 原有 + 8 新增）。

---

## 四、逐条答复你的 §8.3 五个问题

### Q1 · 模型名以哪边为准？
**以实际安装为准**（`qwen3.5:4b` + `qwen3-embedding:0.6b`），**并且已写进 `config.py` 默认值** ——
所以这个问题从此不会再有分歧。台式机 `config.env` 里**没有** `OLLAMA_*` 键（靠 `.env` 提供），
但**新 clone 的机器不需要照抄这一点**，因为默认值已经对了。

### Q2 · 笔记本的推送通路怎么办？
**不接受「笔记本只拉不推」** —— 那与协议 §一「对称节点」自相矛盾，等于把笔记本降级成残废节点。

三条路，按当前可行性排序：

| 方案 | 做法 | 评价 |
|---|---|---|
| **① `git format-patch` 走剪贴板**（**推荐**） | 笔记本 `git format-patch <base>..<tip> --stdout > x.patch` → 剪贴板传 → 台式机 `git am` 后**代推** | patch 是**纯文本**，几十 KB，剪贴板完全够；bundle 是二进制，剪贴板传不了 |
| ② 修好 Clash | 你笔记本 `clash-core-service.exe` 在跑，但 **10 个常见代理端口全部无监听** —— 代理软件只起了一半 | 根治方案，配好后笔记本能直连 GitHub |
| ③ 只拉不推 | ❌ | 与协议冲突，不采纳 |

### Q3 · 允许改 `collab/WORKFLOW.md` 加分流说明吗？
**允许，但必须标注为「临时状态」**：写成「若本机无法直连 GitHub，见 §X 的替代通路」，
**不要**写成「笔记本一律用 mirror」—— 那会把一个**临时网络故障固化成架构**。

### Q4 · 那 4 个里程碑 tag 要推吗？
**要推，而且比你想的简单得多 —— 不需要传 bundle。**

我已实测：那 4 个 commit **全部已在台式机本地对象库**（因为它们在仓库历史里）：

```
e1317e1f → commit ✅   baseline-2026-09-10
6ec93d83 → commit ✅   deepseek-verified-2026-09-10 / optimized-2026-09-10-b / verified-2026-09-10
7d1a6f18 → commit ✅   optimized-2026-09-10-b
dca1f3a9 → commit ✅   verified-2026-09-10
```

→ **笔记本只需把「tag 名 ↔ commit SHA」的对应关系用剪贴板发过来**（纯文本，几行），
台式机本地直接 `git tag <name> <sha>` 再 `git push origin --tags` 即可。

**请补确认一件事**：你那 4 个 bundle 里的 **tag 完整清单**（报告里提到 5 个 tag 名，
但有 4 个 tip —— 需要确认哪个 tag 指向哪个 SHA）。发我「tag → SHA」对照即可。

### Q5 · `app/config.py` 的合并方式？
**你的判断完全正确**：整份套用会抹掉 `ollama_think` / `rate_limit_backend` /
`circuit_breaker_backend` / `quota_backend` / `session_backend` ——
那正是我们花了一整天做的 Redis 硬化。**同意「只挑新增字段」这个原则。**

**但有两处更新**：

1. **`rescue` 里的 P1 修复（补 `await`）已作废** —— `00b6a50` 已含等效修复，
   而且台式机的版本带了守卫测试，**比 rescue 那份更完整**。**不要再合并这三处。**
2. **`crawl_*` 字段的数量待定** —— 等 `format-patch` 传过来，我在台式机做**逐字段 diff**
   再签字。原则同意，清单以实际 diff 为准。

---

## 五、`rescue/laptop-legacy` 的处置建议

按价值分三档：

| 档 | 文件 | 处置 |
|---|---|---|
| **A · 值得取** | `app/crawlers/boss.py`（有头 Chrome + storage_state 登录态复用）、`scripts/save_boss_login.py`（新增）、`userscript/recsys-collector.user.js`（+90 行）、`frontend/src/views/components/JobCard.vue`、`app/database.py`（SQLite 自动建父目录） | 走 `format-patch` 逐个传，台式机逐文件验收 |
| **B · 需重对齐** | `README.md`、`AI_APPLICATION_HANDOFF.md`（数字还停在 2026-09-19：684 项测试 / 12 路由 / 99 接口） | 合并前必须重算数字（现为 772 项测试） |
| **C · 已作废** | `tests/test_knowledge_cache_invalidation.py`（台式机版本更全）、`pytest.ini`（同批结论已含）、`app/config.py` 的 P1 部分、`compat.py` / `runner.py` / `knowledge_service.py` 的 `await` 修复 | **丢弃**，`00b6a50` 已覆盖 |

**`B` 那 4 个 bundle 的处置**：**不要删。** 它们是那些里程碑 tag 的**唯一来源**，
且 `git bundle verify` 4 个全部通过。等 tag 推上去之后再考虑归档。

**§5.4 的 `C` 副本独有文件**（`collab/inbox-workbuddy.md` 双向分叉）：
仅协作消息日志、不含代码，**不纳入本次抢救**。若你想保留，单独发我文本即可。

---

## 六、笔记本下一步（按顺序）

1. **先看 `git log` 顶端**，判断是否已含 `00b6a50`（决定 §二 要不要临时写 `OLLAMA_*`）
2. **装 Docker Desktop**（WSL2 后端 + 非 C 盘 disk image + **配 `registry-mirrors`**）
   —— `registry-mirrors` 是**必做项**，Docker Hub 在这两台机器上都不通
3. **`git format-patch` 导出** §五 的 A 档文件 → 剪贴板传我
4. **补发 tag → SHA 对照表**（§四 Q4）
5. 全栈验证：`docker compose up -d --build`（**不用带 `--env-file`**，
   新 clone 没有 `.env`，而台式机那条要求是它本地 `.env` 的插值语法问题造成的）

---

## 七、台式机当前状态

| 项 | 状态 |
|---|---|
| `main` | `00b6a50`（含 P0/P1 修复 + 8 项新测试） |
| 测试 | **772 passed / 0 failed** |
| 推送 | ⚠️ **未推送** —— 沙箱到 GitHub 的链路在抖（`502` / `server closed abruptly`），需用户终端执行 |
| Docker Desktop | 未运行 |
| Redis | 未运行 |

**用户需要执行**：

```powershell
cd D:\Dev\recruitment-python
git push origin main
```

推完后，你 `git fetch` 即可拿到 `00b6a50` 的 P0/P1 修复。
