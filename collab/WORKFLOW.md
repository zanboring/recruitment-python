# WORKFLOW.md · 日常操作流程

> **日常只看这一页。**
> 背景、理由、实测证据全在 `DUAL-MACHINE.md`（11 节，很长，日常不用看）。
> 本页只写每天真正要敲的命令。

---

## 一、三条铁律（唯一不能省的东西）

1. **开工先 `git pull`，收工先 `git push`** —— 不攒提交
2. **同一时间只在一台机器上干活** —— 一个人本来就做不到同时
3. **代码走 Git，环境走 Docker** —— 不手工拷目录、不传镜像

> 除了这三条，其余都是可以商量的细节。

---

## 二、日常循环（一屏看完）

### ① 在任意一台机器上开工

```bash
git pull
```

### ② 一个任务一条**短**分支（不要再开长期分支）

```bash
git switch -c feat/redis-quota        # 或 fix/cors-origins
#   ...改代码...
git add <明确写文件名>                 # 不用 git add -A
git commit -m "feat(quota): 日配额走 Redis"
git push -u origin feat/redis-quota
```

### ③ 台式机验收（它是唯一有「已验收 Docker 环境」的那台）

```bash
git fetch origin
git switch feat/redis-quota
```

**按改动风险挑验收深度：**

| 改了什么 | 跑什么 | 耗时 |
|---|---|---|
| 只改 `app/**` 的 Python | **3.11 语法闸门**：<br>`docker run --rm --entrypoint python recruitment-python-backend -m compileall -q app scripts` | 秒级 |
| 改了逻辑 | 上面 + **全量测试**：<br>`.\.venv\Scripts\python.exe -m pytest -q` | 约 2 分 40 秒 |
| 改了 `Dockerfile` / 依赖 / 前端 | 上面 + **整套重建并验 A8**：<br>`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright docker compose --env-file config.env up -d --build` | 3–10 分钟 |

**过了就合并：**

```bash
git switch main
git merge --ff-only feat/redis-quota
git push origin main
git push origin --delete feat/redis-quota
```

**没过** → 分支保留，回函说明复现步骤，由作者修。

> ⚠️ **待亲测确认**：上面「3.11 语法闸门」那条命令写于 Docker Desktop 尚未启动之时，
> **首次使用前先在台式机上跑一遍**（预期：无输出、退出码 0）。若镜像名不同，先 `docker images` 查实际名称。
> 全量测试那条用的是宿主机 `.venv`（3.14），它**不能替代**语法闸门 ——
> 因为 3.14 通过不代表 3.11 通过（这是 §8.1 记录的真实风险）。

### ④ 另一台机器跟上

```bash
git fetch --prune
git pull
git branch -D feat/redis-quota        # 已合并的本地分支清掉
```

---

## 三、什么时候**不需要**走 ②③

| 情况 | 做法 |
|---|---|
| 只改 `collab/**` 文档 | 直接提 `main`（不涉及代码，不用验收） |
| 紧急改一行 | 直接提 `main`，补跑一次全量测试 |
| 涉及依赖 / 构建 / Dockerfile | **必须**走 ②③（环境变更风险最高） |

---

## 四、冲突（很少见）

一个人 + 串行提交 → 冲突基本只在"两台机器改了同一个文件"时出现。
`git pull --rebase` 会当场告诉你，解决后 `git rebase --continue` 即可。

---

## 五、旧的长分支策略怎么处置

- `dev-codebuddy` / `dev-trae` / `dev-workbuddy`：**不再新开**。
  已有分支上的未完成工作照常收尾，合并后即可删除。
- **为什么改**：长期分支是这个项目**两次「分支错位」事故**的温床。
  改成按任务命名的短分支后，分支名直接说明"在干什么"，
  不会再有"这条分支到底该不该合"的悬案。
- 顺带不必再在 commit 里手工标机器名 —— 真要追溯来源用：
  `git log --format='%h %an %ad %s' --date=short`

---

## 六、笔记本一次性开通清单（每条都经实测，别跳步）

> ⚠️ **`config.env` 与 `.env` 都被 `.gitignore` 挡住** —— 新 clone 的仓库里**没有这两个文件**。
> 下面所有命令都据此设计。

### 0.1 先处理旧副本（最高优先级）

笔记本之前开发过本项目 → 很可能有一份带 `.git` 的旧副本。
**"两份带 `.git` 的活副本指向同一 origin"在本项目历史上已经造成过事故。**

```powershell
cd <旧的 recruitment-python 目录>
git log --oneline -12      # 有未推送的本地提交吗？
git status --short         # 有未提交改动吗？
git stash list             # 有 stash 吗？
git remote -v              # 指向哪个 origin？
```

- **有未推送内容** → `git push origin HEAD:rescue/laptop-legacy` 保住，再删目录
- **干净且与 origin 一致** → 直接删目录
- ❌ **任何情况都不要在旧副本上继续开发**

### 0.2 装 Docker Desktop

- **必须选 WSL2 后端**
- 装完把 **Disk image location** 指到非 C 盘（台式机用的是 `G:\Work\docker`）
- ⚠️ **必须配 `registry-mirrors`** —— Docker Hub 在你这台机器**实测完全不通**，
  不配的话第一次 `docker compose up --build` 会全部拉取失败（台式机踩过这个坑）

  Settings → Docker Engine 里加：
  ```json
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://docker.1panel.live"
  ]
  ```
  点 Apply & Restart，`docker info` 里出现 Registry Mirrors 即生效。

### 0.3 从 GitHub 重新 clone

```powershell
git clone https://github.com/zanboring/recruitment-python.git
cd recruitment-python
```

### 0.4 模型：走 U 盘（**不要走局域网，也不要各自 `ollama pull`**）

> ❌ **局域网 SMB 在这台台式机上不可用**：`Administrator` 是**空密码**，
> 而 Windows 默认 `LimitBlankPasswordUse=1` 禁止空密码账户经网络认证
> → `\\192.168.31.74\D$` **必然报 1326**。别在这条路上浪费时间。

模型已备好，**直接插 U 盘**：

```
I:\Ollama\models                 13 个文件 / 3.69 GB（8/8 blob 自校验通过）
I:\Ollama\安装说明-给笔记本.md    ← 含二次校验命令与注意事项
```

拷到笔记本的 `D:\Apps\Data\Ollama\models`，设环境变量 **`OLLAMA_MODELS`** 指向它，重启 Ollama。

**为什么必须用同一份、而不是各自 `ollama pull`**：嵌入模型的向量空间是模型特有的。
两台只要不一致，任何一次把 `data/recruitment.db` 带到另一台，
就会**静默**检索出垃圾结果（不报错，只是答案变差）。

### 0.5 确认显卡有没有真的在用

```powershell
ollama ps
```

看 `PROCESSOR` 列：显示 GPU → 6800M 用上了；显示 `100% CPU` → 没用上
（Ollama 官方 Windows ROCm 清单**不含移动版 6800M**）。
**不影响项目** —— 4B + 0.6B 太小，CPU 也够用。

### 0.6 起全栈验证

**省事版**（用 compose 内置的默认密钥，本地开发可接受）：

```powershell
$env:PLAYWRIGHT_DOWNLOAD_HOST='https://cdn.npmmirror.com/binaries/playwright'
docker compose up -d --build
```

**推荐版**（有正经的 JWT 密钥）：

```powershell
copy config.template.env config.env
#   编辑 config.env：填 JWT_SECRET（可选再加 DEEPSEEK_API_KEY / ZHIPUAI_API_KEY）
$env:PLAYWRIGHT_DOWNLOAD_HOST='https://cdn.npmmirror.com/binaries/playwright'
docker compose --env-file config.env up -d --build
```

> **两点说明：**
> 1. **`PLAYWRIGHT_DOWNLOAD_HOST` 不能省** —— 官方 CDN 实测约 100 KB/s，
>    187 MB 的 Chromium 要 1.5 小时以上；换 npmmirror 约 14 秒。
> 2. 笔记本上**不需要** `--env-file .env`。台式机那条"必须带 `--env-file`"的要求，
>    是它**本地 `.env` 的插值语法问题（F6）**造成的；
>    **新 clone 没有 `.env`，不存在这个问题** —— 已实测：模拟无 `.env` 无 `config.env` 的目录，
>    `docker compose config` **退出码 0**。

---

## 七、两台机器的就绪状态对照

| 项 | 台式机 | 笔记本 |
|---|---|---|
| 代码仓库 | ✅ `D:\Dev\recruitment-python`，`main` 与远端同步、工作树干净 | 待 0.3 clone |
| Docker Desktop | ✅ 已装（WSL2，镜像数据在 `G:\Work\docker`） | 待 0.2 |
| registry-mirrors | ✅ 已配 | 待 0.2 |
| Ollama + 模型 | ✅ `D:\Apps\Data\Ollama\models` | 待 0.4（**U 盘**） |
| Python 解释器 | 3.14.7（**仅跑单测**，不等于 3.11 通过） | 版本不限，测试以容器为准 |

> **模型名必须一致**：`qwen3.5:4b` + `qwen3-embedding:0.6b`。
> 换了名字项目会**静默降级**到规则引擎（不报错，只是 AI 功能失效）。
