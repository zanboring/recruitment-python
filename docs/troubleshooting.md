# 故障排查手册（troubleshooting）

> 适用对象：第一次跑这个项目的人，或者三个月后的作者本人。
> 下文所有命令都在**项目根目录**（含 `app/`、`tests/` 的那一层）下执行。
> 解释器统一使用项目内虚拟环境：`.venv\Scripts\python.exe`。

---

## 0. 先建立两个基本事实

| 事实 | 值 |
|---|---|
| 服务端口 | `8080`（`start.bat` 启动命令里写死） |
| 数据库文件 | `data\recsys.db`（SQLite，由 `config.env` 的 `DB_NAME=data/recsys.db` 指定） |

启动方式（二选一）：

- 双击 `start.bat`（窗口关了就什么都看不到，见第 1 节）；
- 或命令行手动启动（**推荐排查时用这个**，错误不会丢）：

```bat
cd /d <项目根目录>
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

启动成功的标志：访问 `http://localhost:8080/` 能打开前端页面，且启动日志里按顺序出现
「数据库结构自检」「数据库连接就绪」之类的 INFO 行（ lifespan 里建表 → 补列 → 切 journal_mode → 起调度器 → 起 admin）。

---

## 1. 双击启动脚本闪退 / 起不来

### 问题

`start.bat` 在「依赖安装失败 / 建库失败」这些分支里有 `pause`，窗口会停住；
但 uvicorn 启动后异常退出时窗口直接关闭，黑框一闪，看不到任何报错。

### 怎么看到真实报错

不要双击。开一个 cmd 窗口，手动逐段执行：

```bat
cd /d <项目根目录>

:: 第一步：确认虚拟环境存在且 Python 能起
.venv\Scripts\python.exe --version

:: 第二步：手动跑初始化，建表与建管理员的错误会完整打在这里
.venv\Scripts\python.exe scripts\init_db.py

:: 第三步：手动起服务，启动期异常（导入失败、端口冲突、数据库打不开）会直接打在这个窗口里
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 按报错对照

| 报错关键词 | 出处 | 处理 |
|---|---|---|
| `未检测到 Python` | `start.bat` 第 19-24 行 | 装 Python 3.11+ 并加入 PATH |
| `No module named 'app'` | 你不在项目根目录跑 | 先 `cd /d <项目根目录>` |
| `unable to open database file` | `app\database.py` 第 60-67 行 | 检查 `data\` 目录是否存在、是否只读；代码会自动 `mkdir`，但磁盘满 / 权限拒绝会失败 |
| `JWT_SECRET 仍是仓库中公开的默认值` | `app\config.py` 第 575-581 行（仅 `APP_ENV=production`） | 换一个 ≥32 字符的新密钥 |
| `address already in use` / WinError 10013 | 端口被占用 | 见第 2 节 |

> 注：`init_db.py` 跑通后会输出「数据表创建完成：…」。它和服务启动时 lifespan 自动建表
> （`app\main.py` 第 42-43 行）做的是同一件事，幂等；手动跑它是为了在不起 uvicorn 的情况下
> 先暴露数据库连接问题。

---

## 2. 端口被占用

### 确认是谁占用了 8080

```bat
netstat -ano | findstr :8080
```

最后一列是占用进程的 PID。查它是谁：

```bat
tasklist | findstr <PID>
```

常见占用者：上一次没退干净的 uvicorn / python.exe，或者本机其它服务。

### 处理方式

**方式 A：杀掉占用进程**

```bat
taskkill /PID <PID> /F
```

**方式 B：换个端口启动**

注意 `start.bat` 第 61 行的端口是**写死**的（`--port 8080`），改 `config.env` 不会改变 `start.bat` 的行为。
换端口只能手动指定：

```bat
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

或者走打包入口 `run_entry.py`，它读环境变量：

```bat
set SERVER_PORT=8081
.venv\Scripts\python.exe run_entry.py
```

`SERVER_HOST` 默认 `127.0.0.1`；需要局域网访问才显式设 `SERVER_HOST=0.0.0.0`。

---

## 3. 页面能打开但数据全空

页面能打开 ≠ 库里有数据。按顺序查：

### 第一步：库里有几张表、job 表几行

```bat
cd /d <项目根目录>
.venv\Scripts\python.exe scripts\source_stats.py
```

这个脚本会按 `job.source_site` 分组输出行数，并给出：

- `total`：总条数
- `demo`：来源为 `seed` 的演示数据条数
- `real`：真实采集条数
- `unknown`：来源为空的条数（异常）

**判读：**

- 如果输出 `job` 表 0 行 → 还没有任何数据写进来过，这是正常初始状态（见 `data-provenance.md`），不是 bug。
- 如果报错 `no such table: job` → 表没建起来，服务启动时 lifespan 会自动 `create_all`；手动补一次：
  ```bat
  .venv\Scripts\python.exe scripts\init_db.py
  ```

### 第二步：确认连的是哪个库

```bat
.venv\Scripts\python.exe -c "from app.database import DATABASE_URL; print(DATABASE_URL)"
```

源码模式下 `config.env` 里 `DB_TYPE=sqlite`、`DB_NAME=data/recsys.db`，上面这条命令应输出
`sqlite+aiosqlite:///./data/recsys.db`。如果你以为自己连的是 MySQL 或另一个库，先看 `config.env`。

### 第三步：直接查 SQLite

```bat
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/recsys.db'); print(c.execute('select source_site, count(*) from job group by source_site').fetchall())"
```

预期的表清单（由 `app\models\` 下各模型注册，本机实测共 8 张）：
`user`、`job`、`company`、`crawl_task`、`knowledge_base`、`sys_log`、`ai_usage`、`daily_report`。

---

## 4. 登录不了 / 401

### 初始口令存在哪

| 环境 | 初始口令来源 | 出处 |
|---|---|---|
| 开发模式（默认） | `admin` / `admin123` | `app\init_data.py` 第 35 行 |
| 生产模式（`APP_ENV=production`） | 随机生成，写入 `data\admin-initial-password.txt` | `app\init_data.py` 第 39、56-66 行 |
| 任意模式 | 环境变量 `ADMIN_INITIAL_PASSWORD` 一旦设置，以它为准 | `app\init_data.py` 第 52-54 行 |

### 登录失败的几种实际表现

| 现象 | 原因 | 出处 |
|---|---|---|
| 接口 401 `请先登录` | 请求没带 `Authorization: Bearer <token>` 头，或 token 已过期 | `app\dependencies.py` 第 19-25 行 |
| 接口 401 `用户不存在` | 库里 `user` 表没有该用户 | `app\dependencies.py` 第 27-29 行 |
| 接口 401 `账号已被禁用` | 用户 `enabled=False` | `app\dependencies.py` 第 32-33 行 |
| 登录页提示 `用户名或密码错误` | 用户名或密码不对（两种错误文案故意合并，不泄露用户名是否存在） | `app\services\auth_service.py` 第 22、45 行 |
| 连续输错后提示 `账号已锁定，请N分钟后再试` | 连续失败 5 次锁 30 分钟 | `app\services\auth_service.py` 第 42-43 行 |
| 登录成功后操作仍 401 | JWT 默认 24 小时过期（`JWT_EXPIRATION=86400000` 毫秒），重新登录即可 | `app\config.py` 第 220 行；`app\utils\security.py` 第 40 行 |

### 忘了密码怎么重置

第一步：在项目根目录生成新密码的哈希（用项目自己的 `hash_password`，保证格式一致）：

```bat
.venv\Scripts\python.exe -c "from app.utils.security import hash_password; print(hash_password('你的新密码'))"
```

输出形如 `sha256$xxxxxx`。第二步：把这个哈希写回 `user` 表（把「粘贴上一步输出的哈希」替换成第一步打印的那串 `sha256$...`）：

```bat
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/recsys.db'); c.execute('update user set password=?, login_fail_count=0, locked_until=NULL where username=?', ('粘贴上一步输出的哈希','admin')); c.commit(); print('done')"
```

> 注意：直接改库绕过了应用，建议仅在完全无法登录时使用；改完重启服务再登录。

### 免密登录（auto-login）的状态

`POST /api/auth/auto-login` 是开发演示用的免密拿 token 接口，**已标记 deprecated**，且有两道门：

1. `APP_ENV=production` 时直接 403；
2. 请求来源必须是本机回环地址（127.0.0.1 / localhost），否则 403。

出处：`app\routers\compat.py` 第 486-506 行。生产环境遇到这个接口 403 是预期行为。

---

## 5. AI 回答一直降级到规则兜底

### 三级链路是什么

按顺序，任一环节失败自动落到下一级：

1. **云端 LLM**：DeepSeek（主）→ 智谱 GLM（备）；只有配了 API Key 的服务商才参与
2. **本地 Ollama**：需要 `OLLAMA_ENABLED=true` 且本机 Ollama 在线
3. **规则引擎兜底**：纯本地规则，不消耗 token，回答质量明显下降

出处：`app\services\ai_service.py` 第 463 行。

### 怎么确认当前用的是哪一级

每次 AI 调用都会落库到 `ai_usage` 表，查最近 10 条：

```bat
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/recsys.db'); [print(r) for r in c.execute('select provider, model, tier, success, error_msg from ai_usage order by created_at desc limit 10').fetchall()]"
```

`ai_usage.tier` 的取值：

| tier | 含义 | 出处 |
|---|---|---|
| `cloud` | 云端（DeepSeek / 智谱） | `app\services\ai_service.py` 第 508、542 行 |
| `local` | 本地 Ollama | `app\services\ai_service.py` 第 593、622 行 |
| `fallback` | 规则引擎兜底 | `app\services\ai_service.py` 第 655 行 |

如果 `tier=fallback` 的行越来越多，按下面的顺序排查：

### 排查顺序

**① 云端：先看 `ai_usage` 里失败记录的 `error_msg`**

```bat
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/recsys.db'); [print(r) for r in c.execute('select provider, model, error_msg from ai_usage where success=0 order by created_at desc limit 5').fetchall()]"
```

（本机实测 `ai_usage` 里历史记录：`cloud` 5 条、`local` 58 条，`tier` 字段确有真实数据。）

常见原因：API Key 为空或失效、余额耗尽、网络不通。配置位置：`.env` / `config.env` 里的
`DEEPSEEK_API_KEY`、`ZHIPUAI_API_KEY`（在「设置」页填的会写入 `config.env`）。

> 补充：云端连续失败 3 次后熔断器会打开 30 秒（快速失败），这段时间也会直接落到本地/规则。
> 出处：`app\services\llm_client.py` 第 36-41 行。

**② 本地 Ollama：确认 Ollama 在线**

```bat
curl http://localhost:11434/api/tags
```

- 连不上 → 启动 Ollama，或把 `OLLAMA_ENABLED=false` 关掉这条路径。
- 连得上但模型没装 → 对比 `ai_usage` 里 `model` 列，去 Ollama 里 `ollama pull <那个模型名>`。

**③ 规则兜底**：如果 ①② 都不可用，最终一定会落到规则引擎——这是设计行为，不是 bug。

---

## 6. 视觉识别导入失败

接口：`POST /api/jobs/vision-import`（单图）、`POST /api/jobs/vision-batch-import`（批量），需要管理员登录。

### 前置条件（缺一不可）

| 条件 | 检查方式 | 出处 |
|---|---|---|
| 智谱 API Key 已配置 | 看 `.env` / `config.env` 里 `ZHIPUAI_API_KEY` 非空 | `app\services\vision_service.py` 第 138-142 行 |
| 图片 ≤ 5 MB | 超出会直接 400 | `app\routers\jobs.py` 第 267 行 |
| 是管理员账号 | 普通用户调用会 403 | `app\routers\jobs.py` 第 252 行 |

### 报错怎么读

| 报错原文 | 含义 | 处理 |
|---|---|---|
| `未配置 ZHIPUAI_API_KEY：视觉识别依赖智谱 GLM-4V-Free 模型（免费）…` | 没配 key | 在 `.env` 或 `config.env` 填入 `ZHIPUAI_API_KEY` |
| `智谱 API Key 无效或已过期，请检查 ZHIPUAI_API_KEY` | 智谱返回 401 | 换一个有效 key |
| `未能在图片中识别到岗位信息，请换更清晰的截图重试` | 图里没有可识别的岗位文字 | 换更清晰的截图 |
| `图片超过 5MB 限制` | 文件过大 | 压缩后再传 |
| 智谱返回 HTTP 400 `code 1210` | 识别接口的 `max_tokens` 硬编码为 1024（`vision_service.py` 第 158 行），超出会被拒 | 这是已知约束，不要试图改大；单张图识别信息过多时拆图 |

> 识别模型固定为 `glm-4v-flash`（`app\services\vision_service.py` 第 24 行），走智谱免费额度。
> 识别结果入库后 `source_site='vision'`。

---

## 7. Redis 连接报错

### 结论先行：不是必须修

缓存层在 `app\cache.py` 里做了自动降级：

- `REDIS_URL` 留空 → 直接走进程内内存缓存（`app\cache.py` 第 50-52 行）；
- 配置了 `REDIS_URL` 但连不上 → 记一条 `WARNING` 日志后**永久降级内存**，业务不中断（`app\cache.py` 第 62-64 行）。

启动日志里两种典型行：

- `Redis 缓存已启用：redis://...` → 当前在用 Redis（`app\cache.py` 第 61 行）；
- `Redis 不可用，降级为内存缓存：...` → 当前已降级内存。

### 你需要关心的边界

降级后**单进程完全正常**；只有部署多副本时才会出现「各副本缓存/会话不一致」。
本项目单机运行场景下，Redis 没起不影响任何功能，可以忽略。

### 确认当前配置在哪一层

```bat
.venv\Scripts\python.exe -c "from app.config import settings; print('redis_url=', repr(settings.redis_url)); print('rate_limit_backend=', settings.rate_limit_backend); print('session_backend=', settings.session_backend)"
```

如果 `redis_url` 非空且你确实想用上 Redis，启动一个本地 Redis（示例连接串 `redis://localhost:6379/0`，见 `app\config.py` 第 408 行注释）即可；
不想用就把 `config.env` 里 `REDIS_URL` 清空，同时把 `*_BACKEND` 几项改回 `memory`，日志就干净了。

---

## 8. 测试跑不起来

### 必须用的命令（项目根目录）

```bat
.venv\Scripts\python.exe -m pytest -o addopts= -q --tb=line -p no:cacheprovider
```

为什么必须带这些参数：

1. **`-o addopts=`**：`pytest.ini` 第 11 行写死了 `addopts = -q`。不带 `-o addopts=` 会叠加成 `-qq`，
   把汇总行（`X passed, Y failed`）静默掉，看起来像「测试没跑完」。这就是任务书里说的
   「为什么汇总行看不到」。
2. **前置环境变量**：本机 `config.env` 里把限流 / 熔断器 / 配额 / 会话后端都配成了 redis。
   跑测试时必须显式覆盖回内存后端，否则测试会连本机 Redis：

```bat
set RATE_LIMIT_BACKEND=memory
set CIRCUIT_BREAKER_BACKEND=memory
set QUOTA_BACKEND=memory
set SESSION_BACKEND=memory
set OLLAMA_ENABLED=false
set JWT_SECRET=ci_only_secret_at_least_32_characters_long
.venv\Scripts\python.exe -m pytest -o addopts= -q --tb=line -p no:cacheprovider
```

### 预期判读

- 测试库是 SQLite 内存库（`tests\conftest.py` 第 39 行），**不会**动 `data\recsys.db`。
- 本机 pytest 退出码不可靠：safe-delete 在 `atexit` 抛 `SystemExit(1)`，全绿也会带非零退出码。
  判读依据是「`passed` 数 == 期望 且 `failed` 数 == 0」，不是退出码。

---

## 附：我核对过的符号

| 符号 | 出处 |
|---|---|
| 启动命令 `uvicorn app.main:app --host 0.0.0.0 --port 8080` | `start.bat:61` |
| `cd /d "%~dp0"`（切到项目根） | `start.bat:11` |
| `SERVER_PORT` / `SERVER_HOST` 环境变量 | `run_entry.py:24,34` |
| `server_port: int = 8080` | `app\config.py:405` |
| `sqlite_target` 相对路径 → `./data/recsys.db` | `app\database.py:33-44` |
| `SQLITE_BUSY_TIMEOUT_MS = 30000` | `app\database.py:115` |
| `ensure_delete_journal_mode` | `app\database.py:157` |
| lifespan 建表 / 补列 / 切 journal_mode | `app\main.py:42-59` |
| `init_db.py` 手动建表入口 | `scripts\init_db.py:39-49` |
| `_DEV_DEFAULT_PASSWORD = "admin123"` | `app\init_data.py:35` |
| `data/admin-initial-password.txt` | `app\init_data.py:39` |
| `ADMIN_INITIAL_PASSWORD` 环境变量 | `app\init_data.py:54` |
| 401 三种触发点 | `app\dependencies.py:20,25,29` |
| 失败 5 次锁 30 分钟 | `app\services\auth_service.py:42-43` |
| `jwt_expiration = 86400000` | `app\config.py:220` |
| `hash_password` / `sha256$` 前缀 | `app\utils\security.py:62-69` |
| auto-login 两道门 | `app\routers\compat.py:486-506` |
| 降级链顺序 | `app\services\ai_service.py:463` |
| tier 三级 `cloud/local/fallback` | `app\services\ai_service.py:508,622,655` |
| `ai_usage` 表 | `app\models\ai_usage.py:20` |
| 熔断器 3 次失败 / 30 秒冷却 | `app\services\llm_client.py:36-41` |
| `OLLAMA_ENABLED` 默认 true | `app\config.py:284` |
| `vision-import` 接口与管理员鉴权 | `app\routers\jobs.py:246,252` |
| 5MB 图片限制 | `app\routers\jobs.py:267` |
| `VISION_MODEL = "glm-4v-flash"` | `app\services\vision_service.py:24` |
| `max_tokens = 1024` | `app\services\vision_service.py:158` |
| 401 → key 失效报错文案 | `app\services\vision_service.py:170-171` |
| Redis 空配置直接降级内存 | `app\cache.py:50-52` |
| Redis 失败降级内存 + warning | `app\cache.py:62-64` |
| `pytest.ini` `addopts = -q` | `pytest.ini:11` |
| 测试 SQLite 内存库 | `tests\conftest.py:39` |
