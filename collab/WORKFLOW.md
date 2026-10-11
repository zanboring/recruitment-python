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

## 六、两台机器各自的一次性准备

| | 台式机 | 笔记本 |
|---|---|---|
| Docker Desktop | ✅ 已装（WSL2 后端，镜像数据在 `G:\Work\docker`） | 待装（**必须选 WSL2 后端**） |
| registry-mirrors | ✅ 已配（Docker Hub 直连不通） | **待配**（同三个源） |
| Ollama | ✅ 已装（`D:\Apps\Data\Ollama\models`） | 待装（**模型从 U 盘 `I:\Ollama\models` 拷**） |
| Python 解释器 | 3.14.7（**仅跑单测**） | 待装（版本不限，测试以容器为准） |

> **模型名必须一致**：`qwen3.5:4b` + `qwen3-embedding:0.6b`。
> 换了名字项目会**静默降级**到规则引擎（不报错，只是 AI 功能失效）。
