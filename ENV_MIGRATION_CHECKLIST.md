# 开发环境迁移清单（换硬盘前 / 换硬盘后）

> 场景：新购入 SN850X，计划让 SN850X 做系统盘（C），现有 C、D 盘改盘符为 F。
> 本文档给出「换盘前要不要装环境」的判断依据、必须备份的清单、以及换盘后的重建步骤。
> 生成时间：2026-09-23

---

## 0. 结论先行

| 你的情况 | 建议 |
|---|---|
| 这段等待期**需要运行/演示**本项目 | 装一套**最小可跑环境**，接受换盘后重建（重建约 20 分钟） |
| 这段等待期**不需要运行**项目 | **不要装完整环境**。只做「备份源码与密钥 + 下载安装包 + 记录版本清单」 |

理由：换盘后**所有绝对路径都会失效**，现在装的每一样东西大概率都要重来一遍（详见第 2 节）。

---

## 1. 本机现状体检（2026-09-23）

| 项目 | 状态 | 说明 |
|---|---|---|
| `.venv/` | ❌ **已失效** | `pyvenv.cfg` 指向 `C:\Users\zanboring\AppData\Local\Microsoft\WindowsApps\...Python.3.11...`，本机无 `zanboring` 用户 |
| `.venv` 创建路径 | ❌ 路径不存在 | `C:\Users\zanboring\OneDrive - Ormesby Primary\Desktop\recruitment-python\.venv` |
| `.env` 中的 `DB_URL` | ❌ **指向无效路径** | `sqlite+aiosqlite:///C:/Users/zanboring/AppData/Local/Temp/recruitment.db` |
| `config.env` | ⚠️ 0 字节 | 空占位文件；**真正的配置在 `.env`**（3556 字节） |
| `recruitment.db` | ⚠️ 0 字节 | 空文件，无数据 |
| Docker / Docker Compose | ❌ 未安装 | `docker: command not found` |
| 系统 Python | 3.13.14 | workbuddy 托管版，**不是本项目用的 3.11** |
| Node / npm | v22.22.2 / 10.9.7 | 版本符合 `frontend/package.json` 要求 |
| 磁盘 | C: + D:（732G，剩 333G） | SN850X 尚未接入 |
| Git 仓库 | ⚠️ `.git` 存在但 `git status` 报 not a repository | 建议在本机终端手动确认：`git -C . status`（可能是权限/沙箱问题） |

**关键结论：这份项目在本机目前是「跑不起来」的状态**——虚拟环境是别人机器的、数据库路径也是别人机器的。
所以「要不要装环境」这个问题，实质是**「要不要在本机从零搭一套」**。

---

## 2. 换盘后会自动失效的东西（原理）

盘符一变（C→F、以及新 C 的路径全变），下面这些**都会坏**：

| 对象 | 为什么失效 | 重建成本 |
|---|---|---|
| Python `.venv/`（138M） | `pyvenv.cfg` 写死解释器绝对路径，`Scripts/*.exe` 内嵌路径 | 低：删掉重跑 `python -m venv .venv` + `pip install -r requirements.txt` |
| `frontend/node_modules/`（237M） | 含路径相关产物/软链接 | 低：`npm install` 重装 |
| `build/`（44M）PyInstaller 产物 | PyInstaller 把路径编进 exe 和 `.toc` | 中：重跑 `build-exe.bat` |
| `dist/`（78M） | 同上，是打包输出 | 中：重跑打包脚本 |
| MySQL 服务（如已装） | **Windows 服务注册表项里的盘符不会自动改**，服务起不来 | 高：重装或改注册表 + 数据目录 |
| Docker Desktop（如已装） | 镜像/容器在 `%LOCALAPPDATA%\Docker\wsl`，对盘符与 WSL 发行版绑定极敏感 | 高：基本等于重装 + 重拉镜像 |
| IDE（VS Code / PyCharm） | 插件、解释器路径、`.idea/*.xml` 全是绝对路径 | 中：重配解释器 |
| 环境变量 `PATH` / `JAVA_HOME` 等 | 注册表值不会自动改 | 低~中：重配 |
| **项目源码（`app/`、`frontend/src`、脚本、文档）** | ✅ **纯文本 + 相对路径，完全不受影响** | 无需重建 |

**唯一需要认真保护的就是源码和密钥。**

---

## 3. 现在必须做的 4 件事（约 30 分钟）

### 3.1 备份项目（复制到 D 盘或移动硬盘）

D 盘现有 333G 可用空间，整目录复制约 550MB，直接拷就行：

```bash
# 目标路径按需修改
xcopy "C:\Users\Administrator\Desktop\recruitment-python" "D:\backup\recruitment-python" /E /H /I /Y
```

### 3.2 备份优先级清单

| 优先级 | 文件/目录 | 原因 | 可重建？ |
|---|---|---|---|
| 🔴 P0 | `.env` | **含 DEEPSEEK_API_KEY、ZHIPUAI_API_KEY、JWT_SECRET、DB 账号密码、BROWSER_COLLECT_TOKEN** | ❌ 不可重建 |
| 🔴 P0 | `config.template.env`、`config.env` | 配置模板 | ⚠️ |
| 🔴 P0 | `app/`、`frontend/src`、`scripts/`、`tests/` | 全部源码 | ❌ |
| 🟠 P1 | `reports/` | 已生成的日报 Excel 与评估报告（`reports/daily/`、`rag_evaluation.md`、`local_model_benchmark.md`） | ❌ |
| 🟠 P1 | `README.md`、`TECH_SPEC.md`、`CHANGELOG.md`、`INTERVIEW.md`、`COLLAB*.md`、`_archive/` | 文档资产 | ❌ |
| 🟠 P1 | `userscript/`、`docker/`、`recsys.spec`、各类 `.bat`/`.cmd` | 部署与工具链 | ❌ |
| 🟡 P2 | `recruitment.db` | 当前 0 字节，无数据 | ✅ |
| 🟡 P2 | `.venv/`、`frontend/node_modules/`、`build/`、`dist/`、`__pycache__/`、`.pytest_cache/` | 全部可重建，可不备份 | ✅ |

> 💡 `.env` 里的 API Key 建议**另外抄一份到手机/密码管理器**。只放在硬盘上，一旦硬盘出问题就找不回来了。

### 3.3 下载安装包（存到 D 盘或 U 盘，别放 C 盘）

新系统装好后断网也能装：

- [ ] Python 3.11.x（Windows installer）— 与本项目一致的版本，避免依赖编译差异
- [ ] Node.js 22.x LTS
- [ ] MySQL 8.x（或直接继续用 SQLite，本项目的 `DB_URL` 就是 sqlite）
- [ ] VS Code / PyCharm
- [ ] Docker Desktop（可选，仅当要跑 `docker-compose.yml` 时才需要）
- [ ] Redis for Windows 或 WSL（可选，不装会自动降级内存缓存）
- [ ] Git for Windows

### 3.4 记录版本清单（本机当前实测）

| 组件 | 当前版本 |
|---|---|
| Python（项目要求） | 3.11.9（原 `.venv` 所用版本） |
| 系统 Python | 3.13.14 |
| Node.js | v22.22.2 |
| npm | 10.9.7 |
| 数据库 | SQLite（`DB_URL=sqlite+aiosqlite:///...`） |
| 后端依赖 | 见 `requirements.txt`（FastAPI 0.115 / SQLAlchemy 2.0.35 / Playwright 1.49 等） |
| 前端依赖 | 见 `frontend/package.json`（Vue 3.4 / Vite 5.2 / Element Plus 2.7 等） |

---

## 4. 换盘实施建议

### 方案 A：全新装系统到 SN850X（**推荐**）

1. 换盘前先把第 3 节全部做完
2. 安装 Windows 到 SN850X → 老盘整盘留作数据盘，盘符改为 F
3. 好处：新系统干净，无历史包袱；老系统还能作为应急启动盘
4. 代价：需要重装所有软件（按第 3.3 节清单 + 第 5 节步骤）

### 方案 B：整盘克隆/系统迁移到 SN850X

- 优点：省掉重装软件的时间
- 缺点：**盘符一变，第 2 节表格里的所有东西照样要修**，工作量约等于重装，还会把旧系统的垃圾一起带过去
- 若选此方案：克隆完成后**必须先改盘符再逐个修复**，顺序错了会导致启动失败

---

## 5. 换盘后重建步骤（方案 A）

```bat
cd /d F:\backup\recruitment-python

rem 1) 后端虚拟环境
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

rem 2) 前端依赖
cd frontend
npm install
cd ..

rem 3) 修配置（见第 6 节）后启动
start.bat
```

依赖清单也可直接用现成的脚本：`build-exe.bat` 前半段就包含「建 venv + 装 requirements + 装 pyinstaller」，可参考其写法。

---

## 6. 重建后必须修的两处（否则起不来）

### 6.1 `.env` 里的 `DB_URL`（必修）

```
# 现在（无效，指向别人机器的临时目录）：
DB_URL=sqlite+aiosqlite:///C:/Users/zanboring/AppData/Local/Temp/recruitment.db

# 改成（示例）：
DB_URL=sqlite+aiosqlite:///F:/backup/recruitment-python/data/recruitment.db
```

### 6.2 `.env` 里的 `DB_HOST` / `DB_PORT` 等模板占位

`.env` 中多处仍是模板语法（如 `DB_HOST=${DB_HOST:localhost}`），说明该文件是从模板生成的、未完全替换。
若切 MySQL，需把这些 `${...}` 占位符替换为真实值；继续用 SQLite 则只需保证上一条 `DB_URL` 正确。

### 6.3 其他需确认的项

- `OLLAMA_ENABLED=true`：本机未装 Ollama，建议先改为 `false`（否则会等本地模型连接超时后降级）
- `BROWSER_COLLECT_TOKEN`：浏览器采集用的令牌，换机后需重新获取
- `JWT_SECRET`：换机后建议重新生成（旧 secret 已随代码流转，存在泄露风险）
- `CORS_ORIGINS`：若访问地址变化需同步调整

---

## 7. 重建后验证清单

- [ ] `GET /api/system/version` 返回 `source` 形态
- [ ] 登录接口可用（`POST /api/auth/login`）
- [ ] `pytest` 全绿（`pytest.ini` 已配 asyncio 模式，跑在 SQLite 内存库上）
- [ ] 前端 `npm run dev` 能打开页面
- [ ] 爬虫能跑通（Playwright Chromium 已安装）
- [ ] AI 接口有响应（密钥有效时走云端，否则降级规则引擎）
- [ ] 日报能生成 Excel 到 `reports/daily/`

---

## 8. 需要重新生成的产物

| 产物 | 命令 |
|---|---|
| 前端静态资源 `frontend/dist/` | `build-frontend.bat` |
| 可移植 exe `dist/recsys/` | `build-exe.bat` |
| 缓存与临时目录 | 删掉 `.pytest_cache/`、`__pycache__/`、`app/**/__pycache__/` 即可，会自动重建 |
