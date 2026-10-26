# 贡献指南

感谢关注这个项目。本仓库是个人毕业设计的 Python 重构版（FastAPI + SQLAlchemy 2.0 异步 + 本地/云端双 AI 后端），
规模约 99 个 REST 接口 / 21 个业务服务 / 8 张数据表 / 772 项自动化测试。

---

## 一、环境准备

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.11** | 以容器为准（见 `docker/backend/Dockerfile`）。宿主机用 3.13/3.14 也能跑单测，但**语法闸门必须以 3.11 为准** |
| Node.js | 20 | 仅前端需要 |
| Docker Desktop | 最新 | **必须选 WSL2 后端** |

> 国内网络提醒：`docker compose` 首次构建时 Playwright 的 Chromium 从官方 CDN 拉取约 100 KB/s（187 MB 要 1.5 小时以上）。
> 先设环境变量换成镜像源，约 14 秒：
>
> ```bash
> # Linux / macOS
> export PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
> # Windows PowerShell
> $env:PLAYWRIGHT_DOWNLOAD_HOST='https://cdn.npmmirror.com/binaries/playwright'
> ```
>
> 另外 Docker Hub 在部分网络下不可达，需在 Docker Desktop → Settings → Docker Engine 配置
> `registry-mirrors`（详见 `docs/configuration.md`）。

## 二、本地起栈

```bash
# 1) 配置：模板全是占位符，按需填 API Key；不填则 AI 自动降级本地 Ollama / 规则引擎
cp .env.example .env

# 2) 起全栈（后端 + 前端 + Redis）
docker compose up -d --build
```

本地模型（可选）：`ollama pull qwen3.5:4b` 与 `ollama pull qwen3-embedding:0.6b`。
**两个模型名必须与 `app/config.py` 的默认值一致** —— 模型名不一致时 Ollama 返回 404，
而调用方会**静默降级到规则引擎**（不报错，只是 AI 功能失效）。

## 三、跑测试

```bash
# Windows
.venv/Scripts/python.exe -m pytest

# Linux / macOS
.venv/bin/python -m pytest
```

- **当前基线：772 passed / 0 failed。** 提交前请确保仍为全绿。
- 测试不依赖真实 Redis / Ollama / 浏览器：这三类后端在用例里都注入了替身（fixture），
  CI 环境（无外部服务）同样能跑通。
- `tests/test_source_hygiene.py` 会检查 BOM 与裸 CR；`tests/test_knowledge_cache_invalidation.py`
  含一条 **AST 静态守卫**，扫描 `app/` 下全部 `_invalidate_caches()` 调用点，**漏 `await` 直接失败**。

## 四、代码风格

- **异步优先**：FastAPI + SQLAlchemy 2.0 async。新增 I/O 一律 `async`。
- ⚠️ **注意别漏 `await`**：本项目栽过一次 —— `async def _invalidate_caches()` 被同步调用，
  协程从未执行、缓存从未清空，且**不报错**。现在有 AST 守卫兜底，但写的时候请自己留意。
  这类「静默失效」在本项目是重点防御对象（同类还有：模型名不匹配、后端配置缺项）。
- 配置项默认值必须「开箱可用」：新增 `Settings` 字段时，默认值要指向本机真实存在的东西，
  否则会静默降级。默认值不得改变单机行为（例如 `*_backend` 一律默认 `memory`）。
- 注释写「为什么」，不写「是什么」。

## 五、提交规范

提交信息格式：

```
<type>(<scope>): <描述>

[可选正文：为什么这么改、有什么副作用]
```

`type` 取值：`feat` / `fix` / `docs` / `chore` / `test` / `refactor` / `perf` / `build`

示例：`fix(db): 修复旧库缺列导致整表接口 500 —— 启动时自动补齐缺失列`

## 六、分支策略

### 6.1 长期分支

- `main` —— 稳定分支，只接受已通过测试与评审的合并
- `feat/*`、`fix/*`、`docs/*` —— 功能 / 修复 / 文档子分支

**保护 `main`：不直接提交、不强制推送。**

### 6.2 功能子分支（可选的良好实践）：一任务一短分支，合并后即删

```bash
git switch dev-codebuddy            # 站到自己的长期分支
git switch -c feat/redis-quota      # 需要时再开功能子分支（或 fix/cors-origins）
#  ...改代码...
git add <明确写文件名>               # 不要用 git add -A
git commit -m "feat(quota): 日配额走 Redis"
```

> 本项目有过两次「分支错位」事故。给功能子分支起描述性名字，可以避免
> 「这条分支到底该不该合」的悬案 —— 合并后即删，不留残留。

> 📌 **本节曾写作「不要开长期分支」**，那是**单人单机时代**的结论，**现已不适用**：
> 当前是**双机 + 双 AI**，必须用长期分支区分「谁的工位」（`dev-codebuddy` / `dev-trae`）。
> 正确表述是：**长期分支固定存在；临时性的功能子分支用完即删。** 两者是层级关系，不是二选一。

## 七、禁止提交的内容

**以下内容一律不进仓库**（`.gitignore` 已挡，但请自己确认）：

- `.env`、`config.env`、`env/config.env` —— 含 API Key / JWT 密钥
- 数据库文件（`*.db` / `*.sqlite3`）
- 系统审计报告（`reports/*.html`，含本机磁盘布局与已装软件清单）
- 内部协作文档与机器信息（内网 IP、本机绝对路径）

> 提交前可用 `git status --short` 复核；`git add` 请显式写文件名，不要 `git add -A` / `git add .`。

## 八、CI

`.github/workflows/ci.yml` 在每次 push / PR 到 `main` 时跑测试（Python 3.11，memory 后端，无外部服务依赖）。

**改动测试或依赖后，请先本地跑一遍与 CI 相同的命令**，确认全绿再提交 ——
一个红的 CI 比没有 CI 更糟。
