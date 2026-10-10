# collab/ —— 协作信道（WorkBuddy ↔ Trae）

> ## ✅ 当前状态：双方均在运行（2026-09-17 11:35 更新）
>
> > 历史提示：本文件 04:30 曾写过「Trae 已收工」—— **那条已作废**。
> > Trae 的工作区与进程仍在，11:31 还在更新 `COLLAB.md` 与 `README.md`。
> > 协作机制照常生效，继续按本文件的边界与频率约定执行。
>
> | 事项 | 现状 |
> |---|---|
> | `collab/inbox-workbuddy.md` | Trae 写、我读 —— **有效** |
> | `collab/inbox-trae.md` | 我写、Trae 读 —— **有效** |
> | `collab/inbox-codebuddy.md` | WorkBuddy 写、CodeBuddy 读 —— **有效**（2026-10-10 新增） |
> | `COLLAB.md` §2 状态表 | Trae 维护 |
> | 模块边界 | 按下表执行（采集域归 WorkBuddy，数据导入/稳定链路归 Trae） |
> | 通信频率 | 里程碑/发现 bug/动对方文件/阻塞 才写消息，日常小改不写 |
> | push | 攒到里程碑一起推 |
>
> 下方「单文件双写」的历史记录保留 —— 它是本项目踩过的真实坑，
> 也是「文件所有权」这个结论的由来。**结论本身依然生效。**

## 一、为什么不再往 `COLLAB.md` 里写消息

`COLLAB.md` 是**双方共写一个文件**，于是：两边各自提交 → 合并时**必然冲突**。

这不是假设，已经真实发生过一次：双方都把「WorkBuddy 首条消息」各自提交了一遍
（我在 `dev-workbuddy` 提交 `9a493d9`，你在 `dev-trae` 提交了同样内容），
`COLLAB.md` 直接撞车。

**根因不是「谁写错了」，而是「一个文件两个写者」这个结构本身有缺陷。**
git 无法自动合并同一区域的两份不同改动 —— 只要共用文件，就一定会撞，
再怎么"小心"也没用。

## 二、新结构：每人只写自己的文件

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `collab/inbox-trae.md` | **只 WorkBuddy 写** | Trae |
| `collab/inbox-workbuddy.md` | **只 Trae 写** | WorkBuddy |
| `collab/inbox-codebuddy.md` | **只 WorkBuddy 写** | CodeBuddy |
| `collab/inbox-codebuddy-reply.md` | **只 CodeBuddy 写** | WorkBuddy |
| `COLLAB.md` §2 任务状态表 | **只 Trae 写**（状态表由 Trae 维护） | WorkBuddy |
| `collab/direct-codebuddy-to-trae.md` | **只 CodeBuddy 写**（2026-10-10 新增） | Trae |
| `collab/direct-trae-to-codebuddy.md` | **只 Trae 写**（2026-10-10 新增） | CodeBuddy |

一个文件只有一个写者 → **结构上不可能冲突**。这不是靠纪律，是靠文件所有权把冲突消灭掉。

## 三、一条命令看清全部协作状态

双击 **`collab-check.cmd`**（任一工作区都可运行），输出四项：

1. 三条分支各自在哪
2. 对方有哪些提交我还没有（→ 我是不是在过时的基础上改）
3. **合并试算：会不会冲突、冲突在哪些文件** —— 用 `git merge-tree` 做权威判定，
   与实际合并的结论完全一致（比"双方都动过哪些文件"准确得多，因为改不同区域时 git 能自动合并）
4. 收件箱有没有新消息（按内容哈希自动判断，**不需要手工标记已读**）

## 四、消息格式约定

在收件箱末尾**追加**，每条以行首 `## MSG` 开头：

```markdown
## MSG 2026-09-17 03:20 采集护栏已完成

- 新增 app/crawlers/throttle.py / robots.py
- 需要你知道：conftest 里我把这些护栏默认关了，原因见下
```

**只追加、不修改历史行**是刻意的：改历史行会造成冲突，追加不会。
（`check.py` 就是靠统计 `## MSG` 行数来判断消息条数的。）

## 五、分工建议：按「模块」切，**不要**按「总版 / 兼容版」切

### 兼容版不是独立代码库，是同一套代码的另一个打包目标

证据（都可验证）：

- `recsys.spec` 里是 `Analysis(['run_entry.py'])`，而 `run_entry.py` 只有一句
  `from app.main import create_app` —— **打进 exe 的就是总版的同一套 `app/` 代码**
- `build-exe.bat` 在同一个工作目录里构建（先 `npm run build`，再 `pyinstaller`）
- 所谓"兼容版专属"的功能（设置栏填 API Key、链接导入 HTTP 降级）改的是
  `app/config.py`、`app/routers/settings.py`、`app/services/url_import_service.py`
  —— 这些改动在总版里同样生效，**而且本来就该生效**

### 如果按版本拆成两套代码，代价是

1. **每个 bug 要修两遍** —— 今天修的 6 个缺陷，每个都要 ×2；
2. **两套必然漂移** —— 总版修了、兼容版忘了，或反过来，且没人能一眼看出差异；
3. **冲突从一次变成几十次** —— 最后合并时两个代码库整体对撞，而不是几个文件；
4. **面试时无法解释"为什么同一个功能有两份实现"** —— 这是明确的减分项。

### 正确的切法：一套代码 + 两个交付目标，按模块分人

> **这是当前生效的分工（用户要求「先各自分好工」）。有变更请改本表，不要靠口头约定。**

| 归属 | 域 | 独占的文件 / 目录 |
|---|---|---|
| **WorkBuddy** | 采集域 | `app/crawlers/**`、`app/services/crawler_service.py`、`app/routers/crawler.py` |
| **WorkBuddy** | 浏览器采集通道 | `userscript/**`、`crawler_service.ingest_browser_jobs()`、`routers/crawler.py` 的 `/ingest` |
| **WorkBuddy** | 日报域 | `app/services/report_service.py`、`webhook_service.py`、`app/routers/report.py`、`frontend/.../Report.vue` |
| **WorkBuddy** | 版本与发布机制 | `app/version.py`、`app/services/update_service.py`、`app/routers/system.py`、`CHANGELOG.md` |
| **WorkBuddy** | 协作信道 | `collab/**`（`inbox-workbuddy.md` 除外）、`collab-check.cmd` |
| **Trae** | 数据导入域（视觉 / 链接 / CSV） | `app/services/url_import_service.py`、`vision_service.py`、`app/routers/jobs.py` |
| **Trae** | 稳定性链路 | `app/services/llm_client.py`、`ai_service.py`、`job_checker.py` |
| **Trae** | 打包与 Release | `recsys.spec`、`build-exe.bat`、`run_entry.py`、`start*.bat`、`git-sync.cmd` |
| **Trae** | 设置中心 | `app/routers/settings.py`、`frontend/.../Settings.vue`、`frontend/src/api/settings.ts` |
| **Trae** | 协作状态表 | `COLLAB.md` §2、`collab/inbox-workbuddy.md` |

**共用文件（双方都会改，按下面规则来）**：

| 文件 | 规则 |
|---|---|
| `app/config.py` | 新增配置项**追加到 `Settings` 类末尾自己的区块**，不要插进别人区块中间 |
| `app/main.py` | 导入行与 `include_router` 都追加到末尾；冲突时通常「两边都保留」 |
| `README.md` | 只改自己负责的章节 |
| `tests/**` | 各自新增自己的测试文件；**不要改对方的测试文件**（要改先发消息） |

**两个交付目标**（不是两套代码）：

| 目标 | 怎么产出 | 从哪打包 |
|---|---|---|
| 面试总版 | 源码 + `start.bat` | `main` |
| 兼容版（绿色软件） | `build-exe.bat` → `dist/RecSys/` | **`main`**（不是 `dev-trae`） |

> ⚠️ 兼容版必须从 `main` 打包。如果从 `dev-trae` 打包，它会缺掉 WorkBuddy 已合入的功能，
> 于是"兼容版"变成"功能少一截的版本"—— 那正好是拆代码库会掉的坑。

---

## 五之二、通信频率约定（用户要求「交流频率少一些」）

之前的做法是一条小改动就写一条消息，结果收件箱堆了 8 条、还各自触发一轮合并 ——
**通信成本比改代码本身还高**。改为：

| 场景 | 做法 |
|---|---|
| 日常改动、小修复 | **不写消息**。各自提交，靠 `collab-check.cmd` 自查（它本来就会显示对方新提交与冲突预警） |
| 一个里程碑完成 | **写一条**汇总消息（含：做了什么、需要对方注意什么、下一步） |
| 发现对方代码有 bug | **写一条**，但要带可复现步骤（不要只说"有问题"） |
| 边界要变（动了对方的文件） | **写一条**，先问再动 |
| 阻塞、需要对方配合 | **写一条**，并在标题写「⚠️ 待处理」 |

**不用写信的情况**：改了只属于自己的文件、加了只属于自己的测试、文档同步 ——
这些 `collab-check.cmd` 的「对方新提交」列表已经能说明。

**push 频率同理**：本地提交随便攒，**等一个里程碑再一起 push**（网络不稳定，少推几次）。
用户只在这时候需要动手：`git push origin dev-workbuddy dev-trae`。

---

## 六、工作区布局（2026-10-10 起：**已统一为单目录**）

> ⚠️ **本节原内容（三目录 worktree 方案）已作废。** 保留下方历史说明仅供追溯。

**用户 2026-10-10 14:01 明确要求：「以后统一一下，工作文件夹在 D 盘那个」。**

```
D:\Dev\recruitment-python        ← 唯一工作副本（所有分支都在这个仓库里）
   ├── dev-workbuddy   WorkBuddy
   ├── dev-codebuddy   CodeBuddy
   └── main            用户拍板后合并，当前停在 4cae06d
```

- **只有一个目录**，不再用 `git worktree` 开多目录 → 也就不会再有"两个目录各自持有未提交改动"的麻烦
- 换分支用普通 `git checkout <branch>`；两个 AI **不要同时**改同一批文件（按 §5 分工表的文件所有权来）
- 互读对方分支：`git show dev-codebuddy:文件` / `git log dev-codebuddy`
- 只有 `git push` 到 GitHub 需要用户执行（AI 沙箱网络被拦，见 `COLLAB.md` §5）

### ⛔ 禁止使用的目录（都是过期副本）

| 路径 | 状态 |
|---|---|
| `C:\Users\Administrator\Desktop\recruitment-python` | ❌ **过期快照**（2026-10-10 用户复制，**带独立 `.git` 与同一 `origin`**） |
| `H:\Backup\Dev\recruitment-python` | ❌ 旧备份（09-28） |
| `H:\Backup\recruitment-python-workarea-20260928` | ❌ 旧备份 |
| `H:\Backup\C_Users_20260924\Desktop\recruitment-python` | ❌ 旧备份（09-23） |

**判定活副本**：有 `collab/MISSION-redis-docker.md`，且分支为 `dev-workbuddy` / `dev-codebuddy`。
**动手前先 `cd` 到 `D:\Dev\recruitment-python` 再操作。**

### 历史说明（已作废，仅供追溯）

原先的布局是三个目录共享同一个 `.git`（`git worktree`），
配套 `C:\Users\zanboring\recruitment-python-wb` / `-trae` 两个 worktree。
该方案在 2026-10-10 换盘后废弃：worktree 目录已不存在，且它带来的
「一个仓库多份工作树」复杂度在当前双 AI 串行协作下没有收益。
