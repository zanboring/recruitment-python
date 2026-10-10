# 油猴采集通道 · 卡住问题诊断与修复

> 诊断时间：2026-10-10 12:31　｜　对象：`userscript/recsys-collector.user.js` v1.1.1

---

## 一、诊断结论：油猴脚本**没有启动**，而且不可能启动

**根因：篡改猴（Tampermonkey）压根没安装。** 脚本没有宿主，当然不会运行。

### 证据链

| # | 检查项 | 结果 |
|---|---|---|
| 1 | Edge 已装扩展（逐个读 manifest 确认） | ChatGPT、Cookie-Editor、Edge relevant text changes、若干 `__MSG_*__` 内置组件 —— **无任何脚本管理器** |
| 2 | Chrome 已装扩展 | 仅 ChatGPT 等 2 个，**无 Tampermonkey** |
| 3 | Firefox 扩展 | 只有 `formautofill` / `newtab` / `pictureinpicture` 等**系统内置**扩展 |
| 4 | 全盘搜 `*tampermonkey*` / `*篡改猴*`（AppData / D:\Apps / D:\Dev） | **空** |
| 5 | 其他脚本管理器（暴力猴 `jinjacgbpgkfneahmojbmmjakhjcjfma`、脚本猫） | **均不存在** |
| 6 | `dhdgffkkebhmkfjojejmpbldmpobfkfo`（Tampermonkey 扩展 ID） | Chrome / Edge 全部配置文件中**均未找到** |
| 7 | 浏览器进程 | 只有 `msedge.exe` 在跑（14 个进程）；Chrome / Firefox 进程数为 0 |

> ⚠️ `userscript/README.md` 第 38 行写的「1. 你已装好篡改猴（Tampermonkey）✅」是**错误假设**，请修正该文档。

### 同时存在的第二个阻塞点

**后端服务没在跑。** `netstat` 查 8080 端口无任何监听，请求报 `os error 10061`（目标计算机积极拒绝）。
即使脚本跑起来了，采到的数据也无处回传。

### 第三点：工作目录指错了

真项目在 `D:\Dev\recruitment-python\`，而工具当前工作区指向 `C:\Users\Administrator\Desktop\recruitment-python`（**空目录**）。
这就是「读不到本地数据」的原因——工具打开的是空壳，不是你的项目。

### 好消息

`BROWSER_COLLECT_TOKEN` 在 `.env` 和 `config.env` 中**都已配置为非空**，所以不会踩到「没配令牌 → 接口 403」那个坑。

---

## 二、修复步骤（按顺序执行）

### Step 0 · 把工作区切到正确目录

把工具的工作目录改成 **`D:\Dev\recruitment-python`**（或把项目挪回桌面）。否则后续任何 AI 协作都读不到项目文件。

### Step 1 · 先启动后端（否则采了也没用）

```bat
cd /d D:\Dev\recruitment-python
start.bat
```

验证（新开一个终端）：

```bash
curl http://127.0.0.1:8080/api/system/version
```

应返回一段 JSON，含 `version` 与 `distribution: "source"`。返回不了就别往下走。

### Step 2 · 在 Edge 里装篡改猴

1. 地址栏打开 `edge://extensions/`，先确认左下角「**开发人员模式**」是否需要开（从商店装不用开）。
2. 打开 Edge 加载项商店，搜索 **Tampermonkey** → 点「获取」→ 确认安装。
   - 商店打不开时：去 `https://www.tampermonkey.net/` 下载，或换用 Chrome 应用商店。
3. 装完回到 `edge://extensions/`，确认 **Tampermonkey 的开关是打开状态**（灰色 = 已停用）。
4. 点浏览器右上角拼图图标 → 把 Tampermonkey **固定到工具栏**（方便看状态）。

### Step 3 · 装脚本

**方式 A（推荐）**：在 Edge 地址栏直接打开
```
file:///D:/Dev/recruitment-python/userscript/recsys-collector.user.js
```
篡改猴会自动弹出安装页 → 点「**安装**」。

**方式 B**：点篡改猴图标 → 「管理面板」→ 「+」添加新脚本 → 把文件内容整段粘贴替换 → `Ctrl+S` 保存。

装完检查：篡改猴管理面板的「已安装脚本」里应能看到 **RecSys 招聘数据采集器 v1.1.1**，状态为**已启用**。

### Step 4 · 验证脚本真的跑起来了

打开任意 BOSS 直聘岗位页（如 `https://www.zhipin.com/...`）→ 按 `F12` → 切到 **Console**。

应当看到这一行：
```
[RecSys采集器] 脚本已注入，域名：www.zhipin.com，URL：https://www.zhipin.com/...
```

同时页面右下角出现 **RecSys 采集器** 面板。

**看不到这行 = 脚本没运行**，三种可能：篡改猴被停用 / 脚本被停用 / `@match` 不匹配当前域名。

### Step 5 · 填令牌

面板点「**设置**」：
- 服务地址：`http://localhost:8080`
- 令牌：填 `.env` 里 `BROWSER_COLLECT_TOKEN=` 后面那串值

### Step 6 · 先点「预览」，确认选择器没失效

**不要直接点采集。** `README.md` 已明确写明：`SITES` 里的选择器**未在真实站点验证过**，是按公开结构写的「有依据的猜测」。
「预览」只识别不发送，会告诉你**识别到几条 + 用的是哪个定位方式（选择器名 or 兜底逻辑）**，一眼就能判断是否失效。

预览正常后再点「采集当前页」。

---

## 三、验证清单（全部打勾才算通）

- [ ] 工作区指向 `D:\Dev\recruitment-python`
- [ ] `curl http://127.0.0.1:8080/api/system/version` 返回 JSON
- [ ] `edge://extensions/` 里 Tampermonkey 已启用
- [ ] 篡改猴面板里能看到「RecSys 招聘数据采集器 v1.1.1」且已启用
- [ ] 招聘页面 F12 Console 出现 `[RecSys采集器] 脚本已注入`
- [ ] 页面右下角出现采集器面板
- [ ] 面板「设置」里服务地址与令牌已填
- [ ] 点「预览」能识别到岗位条数 > 0
- [ ] 点「采集当前页」返回「新增 / 重复 / 被过滤 / 无效」四项计数

---

## 四、排错速查表

| 现象 | 原因 | 处理 |
|---|---|---|
| Console 没有注入日志 | 脚本没运行 | 依次查：篡改猴开关 → 脚本启用状态 → `@match` 是否覆盖当前域名 |
| 有注入日志但没有面板 | 面板注入失败或样式冲突 | 看 Console 是否有报错；刷新页面重试 |
| 预览识别到 0 条 | 站点改版、选择器失效 | 按 README 改脚本 `SITES` 数组（脚本自带「兜底定位」，通常只改这一处） |
| 回传返回 403 | 面板里的令牌与服务端不一致 | 面板「设置」重新填 `.env` 中的令牌 |
| 回传报网络错误 / 超时 | 后端没起来 | 回 Step 1 起服务 |
| 「自动翻页」停在第 1 页 | 「下一页」按钮没被识别 | 改用手动翻页；脚本找不到 `a[href*="page="]`、`.next` 或「下一页」文本时会停 |
| 提示缺少 `@connect` 权限 | 篡改猴拦截了跨域请求 | 确认脚本头部 `@connect localhost` / `@connect 127.0.0.1` 存在 |

---

## 五、给 README 的修正建议

`userscript/README.md` 第 38 行的安装步骤应改为**从零开始**，不要假设篡改猴已安装：

```diff
-1. 你已装好 **篡改猴（Tampermonkey）** ✅
+1. 安装 **篡改猴（Tampermonkey）**：
+   - Edge/Chrome：打开扩展商店搜索 Tampermonkey → 获取 → 安装
+   - 安装后在 edge://extensions/（或 chrome://extensions/）确认开关已打开
+   - 验证：打开招聘页面按 F12，Console 应出现「[RecSys采集器] 脚本已注入」
```
