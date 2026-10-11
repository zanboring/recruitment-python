# DUAL-MACHINE.md · 双机开发协议

> 起因：用户 2026-10-11 决定采用双机开发 —— **笔记本负责生成代码，台式机负责跑 AI 和项目**。
> 本文件定义两台机器之间的协作规则。**它优先于任何口头约定**；与本文件冲突的做法一律视为违规。

---

## 一、角色与唯一真源

| 机器 | 角色 | 允许做的事 |
|---|---|---|
| **笔记本** | **开发方** | 写/生成业务代码；跑 CodeBuddy / Trae；提交到 `dev-*` 分支；合并到 `main`；`git push` |
| **台式机**（本机） | **运行方 + 文档方** | `git pull` 后运行（Docker / 源码模式）；提交 `collab/**` 与容器配置；做验收 |

> **唯一真源 = GitHub 上的 `origin/main`。**
> 两台机器上任何一份工作副本都只是**缓存**，不是真源。

### 台式机允许提交的范围（白名单）

- `collab/**` —— 任务书、信道消息、验收记录
- `docker/**`、`docker-compose.yml`、`.dockerignore` —— 容器配置
- 根目录的交接/说明类 Markdown

**业务代码（`app/**`、`frontend/**`、`tests/**`）一律在笔记本上改。**

---

## 二、数据流（单向，不允许反向）

```
笔记本（写代码）
   │  git push  →  dev-codebuddy / dev-trae / dev-workbuddy
   ▼
GitHub  origin
   │  git pull --ff-only  →  main
   ▼
台式机（运行）
```

- **代码方向：笔记本 → GitHub → 台式机**。
- 台式机**不向 `dev-*` 分支推送业务代码**。
- 台式机若因验收发现了业务代码缺陷，**只写进 `collab/inbox-*.md` 派工**，由笔记本侧修改。

---

## 三、硬规则（每条都对应一次真实事故或已识别的风险）

1. **开工前先 `git pull --ff-only`；收工后立刻 `git push`。**
   不允许"攒着之后再推" —— 台式机当前就有 4 个提交积压未推，这会直接导致两台机器分叉。
2. **禁止用拷贝/U盘/网盘直接搬工作目录**（绕过 git）。
   本项目历史上已因"两份带 `.git` 的活副本指向同一 origin"翻过车。
3. **分支纪律**：`dev-codebuddy` / `dev-trae` / `dev-workbuddy`，各人只能用自己那条。
   commit 前 `git rev-parse --abbrev-ref HEAD`，commit 后 `git log --oneline -1` 复核。
4. **禁止 `git add -A`** —— 工作树里常有他人未提交内容。
5. **台式机只拉 `main`**，不拉 `dev-*`。
   理由：未验收的代码不应进入运行环境。`main` 是"已验证"的边界。
6. **任何超过 1 分钟的操作（全量测试、构建）之前与之后，都重新确认分支与 `git status`。**

---

## 四、识别到的最大风险：写者从 2 个变成 4 个

原来的结构里"单目录共享 HEAD + 两个 AI 并发"已经造成过**两次分支错位事故**。
引入第二台机器后，写者变成 **2 台机器 × 各自的 AI = 最多 4 个**，且**工作树不再共享**——
这意味着**"分支错位"会被"跨机分叉"取代**，后者更难发现也更容易丢工作。

**缓解手段（必须执行）：**

- 一台机器一个角色（见 §一），不做"两边都开发"
- 串行：同一时间只允许一台机器在提交
- 每天开始与结束时各 sync 一次（`pull` / `push`）
- 定期用 `git log --oneline --all --graph` 核对是否出现意外分叉

---

## 五、网络注意（实测，2026-10-11）

| 目标 | 台式机可达性 | 说明 |
|---|---|---|
| `github.com` / `codeload.github.com` | ✅ 200 / 301 | **`git push` / `git pull` 可正常走这条路** |
| `api.github.com` | ✅ 200 | |
| `objects.githubusercontent.com` | ❌ 000 | **只有 release 二进制资产被墙** |

- **只要下载 GitHub 上的二进制文件**（Docker 镜像、Playwright、WSL 包等），才需要加加速前缀 `https://gh-proxy.com/`。
- **`git` 操作不要加加速前缀**，直连即可。
- ⚠️ AI 沙箱内 `git push` 会被重置（`Recv failure: Connection was reset`），
  **推送必须由用户在真实终端执行**，不要在沙箱里反复重试。

---

## 六、环境参数（台式机）

| 项 | 值 |
|---|---|
| 局域网 IP | `192.168.31.74`（网关 `192.168.31.1`） |
| Docker 镜像/容器数据 | `G:\Work\docker`（已迁离 C 盘） |
| Ollama | 模型在 `D:\Apps\Data\Ollama\models`（3.7 GB：qwen3.5 + qwen3-embedding） |
| **Ollama 监听范围** | **仅 `127.0.0.1`（不开放给局域网）** —— 用户 2026-10-11 拍板 |
| 项目运行端口 | `http://localhost:8080` |

### ⚠️ 一个待决策点：容器内的项目目前**用不上**本机 Ollama

- `docker-compose.yml` 里写的是 **`OLLAMA_ENABLED: "false"`**，且未注入 `OLLAMA_BASE_URL`
- 原因：容器内的 `localhost` 指向**容器自身**，指不到宿主机的 Ollama
- 所以「台式机跑 AI」目前**只在源码模式（`start.bat`）下成立**；
  容器模式下项目的 AI 走 DeepSeek / 智谱云端，兜底直接落到规则引擎

**若要打通**（容器也能用本机 Ollama），需同时满足三条：

1. 宿主机 Ollama 改成 `OLLAMA_HOST=0.0.0.0`（否则容器连不上）
2. compose 里 `OLLAMA_ENABLED: "true"` + `OLLAMA_BASE_URL: "http://host.docker.internal:11434"`
3. **加防火墙规则，只放行 Docker/WSL 网段访问 11434，仍对局域网关闭**
   —— 这样才同时满足"容器能用"与"笔记本不能用"

> 第 3 条是必须的：只做前两条会把 11434 暴露到整个局域网，违背 §六 的决策。
