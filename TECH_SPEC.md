# Recruitment 系统 — Python 重构技术规格

> 本文档供 Trae 重构用，包含原 Java 项目的完整数据库设计、API接口、业务逻辑、算法公式。

---

## 0. 项目概况

| 项目 | 原值 |
|------|------|
| 语言/框架 | Java 21 + Spring Boot 3.2.5 |
| ORM | MyBatis 3.0.4 + PageHelper 2.1.0 |
| 安全 | Spring Security 6 + jjwt 0.12.6 |
| 数据库 | MySQL 8.0 |
| 爬虫 | WebMagic 0.8.0 + Jsoup 1.17.2 + Playwright 1.49.0 |
| Excel | EasyExcel 3.3.4 |
| 文档 | springdoc-openapi 2.5.0 |
| 总代码量 | 约 70 个 Java 文件 |

---

## 1. 数据库表结构

### 1.1 数据库
- 数据库名: `recruitment_db`
- 字符集: `utf8mb4`
- 时区: `Asia/Shanghai`

### 1.2 user 表

```sql
CREATE TABLE `user` (
    `id`               BIGINT AUTO_INCREMENT PRIMARY KEY,
    `username`         VARCHAR(50)  NOT NULL UNIQUE,
    `password`         VARCHAR(255) NOT NULL,
    `email`            VARCHAR(100),
    `role`             VARCHAR(20)  NOT NULL DEFAULT 'USER',
    `skills`           VARCHAR(500),
    `education`        VARCHAR(20),
    `experience_years` INT,
    `login_fail_count` INT DEFAULT 0,
    `locked_until`     DATETIME,
    `created_at`       DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

默认管理员: username=admin, password=admin123 (BCrypt: `$2a$10$...ZDoWXd+YGTGxeMkoxzJp2YevU/WOkv4dqxI+V7mkpQ8=`)

### 1.3 job 表 (核心)

```sql
CREATE TABLE `job` (
    `id`           BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_id`   BIGINT,
    `title`        VARCHAR(200) NOT NULL,
    `company_name` VARCHAR(200),
    `source_site`  VARCHAR(50)  NOT NULL,
    `job_key`      VARCHAR(100) NOT NULL UNIQUE,
    `job_status`   VARCHAR(20)  DEFAULT 'ACTIVE',
    `city`         VARCHAR(50),
    `experience`   VARCHAR(50),
    `education`    VARCHAR(50),
    `min_salary`   DECIMAL(15,2),
    `max_salary`   DECIMAL(15,2),
    `salary_unit`  VARCHAR(10)  DEFAULT '元',
    `skills`       VARCHAR(500),
    `job_desc`     TEXT,
    `url`          VARCHAR(500),
    `detail_html`  TEXT,
    `publish_time` DATETIME,
    `last_seen_at` DATETIME,
    `created_at`   DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_source_site (`source_site`),
    INDEX idx_city (`city`),
    INDEX idx_job_status (`job_status`),
    INDEX idx_created_at (`created_at`)
);
```

### 1.4 company 表

```sql
CREATE TABLE `company` (
    `id`       BIGINT AUTO_INCREMENT PRIMARY KEY,
    `name`     VARCHAR(200) NOT NULL,
    `industry` VARCHAR(100),
    `city`     VARCHAR(50),
    `address`  VARCHAR(500),
    `size`     VARCHAR(50),
    `website`  VARCHAR(200),
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 1.5 crawl_task 表

```sql
CREATE TABLE `crawl_task` (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `source_site` VARCHAR(50)  NOT NULL,
    `keyword`     VARCHAR(100) NOT NULL,
    `city`        VARCHAR(50),
    `status`      VARCHAR(20)  DEFAULT 'PENDING',
    `job_count`   INT DEFAULT 0,
    `message`     TEXT,
    `created_at`  DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 1.6 knowledge_base 表

```sql
CREATE TABLE `knowledge_base` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `question`      VARCHAR(500) NOT NULL,
    `answer`        TEXT NOT NULL,
    `tags`          VARCHAR(300),
    `source`        VARCHAR(20) DEFAULT 'manual',
    `usage_count`   INT DEFAULT 0,
    `status`        TINYINT DEFAULT 1,
    `quality_score` INT DEFAULT 0,
    `created_at`    DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at`    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 1.7 sys_log 表

```sql
CREATE TABLE `sys_log` (
    `id`        BIGINT AUTO_INCREMENT PRIMARY KEY,
    `username`  VARCHAR(50),
    `action`    VARCHAR(255),
    `method`    VARCHAR(255),
    `uri`       VARCHAR(255),
    `ip`        VARCHAR(50),
    `params`    TEXT,
    `success`   TINYINT DEFAULT 1,
    `error_msg` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 2. 认证系统

### 2.1 JWT
- 算法: HS256
- Claims: sub(userId), username, role, iat, exp
- 过期: 默认 86400000ms (24小时)
- 密钥: 环境变量 `JWT_SECRET` (Base64)
- 传递: `Authorization: Bearer {token}`

### 2.2 密码
- 算法: BCrypt

### 2.3 权限角色

| 角色 | 标识 | 权限 |
|------|------|------|
| USER | ROLE_USER | 基本查询、个人中心 |
| ADMIN | ROLE_ADMIN | 所有 + job:write/delete + crawl:manage + data:cleanup |

### 2.4 公开端点 (无需认证)
- `/api/auth/login`, `/api/auth/register`, `/api/auth/auto-login`
- `/api/ai/status`, `/api/model/status`, `/api/model/health`
- `/api/knowledge/**` (全部公开)
- `/api/job/list`, `/api/job/detail/{id}`, `/api/job/statistics`, `/api/job/search`

### 2.5 登录锁定
- 连续失败 5 次 → 锁定 30 分钟
- 成功登录 → 重置失败计数

---

## 3. REST API 完整清单

### 3.1 统一响应格式

```json
{"code": 0, "msg": "success", "data": {}}
```
code: 0=成功, 1=失败, 401=未认证, 403=无权限, 422=参数校验失败, 500=服务器错误

### 3.2 认证 `/api/auth`

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| POST | /login | {username, password} | 返回 {token, user} |
| POST | /register | {username, password, email} | 默认角色 USER |
| POST | /change-password | {oldPassword, newPassword} | 需认证 |
| GET | /user-info | - | 返回当前用户信息 |

### 3.3 AI `/api/ai`

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| GET | /status | - | Ollama和智谱API状态 |
| POST | /chat | {message, useLocalModel} | 同步AI回复 |
| POST | /stream | {message, useLocalModel} | SSE流式回复 |
| POST | /cancel | - | 取消流式回复 |

useLocalModel 路由策略:
- null (自动): Ollama优先 → 智谱GLM-4回退
- true: 强制Ollama
- false: 强制智谱GLM-4

### 3.4 岗位 `/api/jobs`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | / | 新增岗位 |
| PUT | / | 更新岗位 |
| DELETE | /{id} | 删除 |
| DELETE | /batch | 批量删除 |
| GET | /{id} | 详情 |
| POST | /page | 分页查询(RequestBody: JobQueryDTO) |
| GET | /stat/city | 城市统计 |
| GET | /stat/company | 企业统计 |
| GET | /stat/skill | 技能统计 |
| GET | /stat/salary-range | 薪资区间统计 |
| GET | /stat/education | 学历统计 |
| GET | /stat/experience | 经验统计 |
| GET | /stat/status | 状态统计 |
| GET | /predict-salary | 薪资预测(?city=&education=&experience=&skills=) |
| GET | /recommend | 岗位推荐(?skills=&education=&experience=&city=) |
| POST | /recommend/intelligent | 智能推荐({skills, education, experienceYears, city, limit}) |
| GET | /analysis/summary | 分析摘要 |
| POST | /ai-analysis | AI分析(RequestBody: JobQueryDTO) |

### 3.5 爬虫 `/api/crawl`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /task | 创建任务({sourceSite, keyword, city}) |
| POST | /task/{id}/start | 启动任务 |
| GET | /tasks | 任务列表 |
| DELETE | /task/{id} | 删除 |

### 3.6 数据 `/api/data`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /import | Excel导入(MultipartFile) |
| GET | /export | Excel导出(?limit=2000) |
| POST | /cleanup | 清空数据 |

### 3.7 知识库 `/api/knowledge`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /list | 分页列表 |
| GET | /all | 全部启用条目 |
| GET | /search?keyword= | 搜索 |
| GET | /{id} | 详情 |
| POST | / | 新增 |
| PUT | /{id} | 更新 |
| DELETE | /{id} | 删除 |
| PUT | /{id}/status?status= | 启用/禁用 |
| PUT | /{id}/score?score= | 质量评分(0-3) |
| POST | /learn | 从AI学习({question, answer}) |

### 3.8 用户管理 `/api/users` (ADMIN)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | / | 分页列表 |
| GET | /{id} | 详情 |
| PUT | /{id} | 更新 |
| DELETE | /{id} | 删除 |
| PATCH | /{id}/status | 启用/禁用 |

---

## 4. 智能推荐算法

### 4.1 多因子综合评分

```
综合分 = 技能匹配 × 0.7 + 学历匹配 × 0.2 + 经验匹配 × 0.1
```

- **技能匹配**: Jaccard相似度 (0-1)
- **学历匹配**: 用户学历 >= 岗位要求 → 1.0; 否则 → 用户等级/岗位等级
  - 等级: 博士=5, 硕士=4, 本科=3, 大专=2, 高中=1, 不限=0
- **经验匹配**: 用户年限 >= 岗位要求 → 1.0; 否则 → 用户年限/岗位年限
  - 年限映射: 10年以上=10, 5-10年=7, 3-5年=4, 1-3年=2, 1年以下=1, 应届=0

### 4.2 薪资预测

```
预测薪资 = 行业基准薪资 × (城市系数×0.4 + 经验系数×0.35 + 学历系数×0.25) × (1 + 技能溢价)
```

**城市系数**: 北京1.80, 上海1.75, 深圳1.65, 杭州1.45, 广州1.40, 成都1.30, 南京1.35, 武汉1.25, 长沙1.05, 其他1.00
**经验系数**: 10年+1.80, 5-10年1.50, 3-5年1.20, 1-3年0.90, 应届0.70
**学历系数**: 博士1.70, 硕士1.35, 本科1.00, 大专0.80
**高薪技能溢价**: 算法/大数据/人工智能/机器学习/深度学习/Go/架构师, 每项+5%, 上限15%
**行业基准薪资**: 无数据时默认12000
**输出范围**: 限制 5000-100000

---

## 5. AI 模块

### 5.1 智谱 GLM-4
- API: `POST https://open.bigmodel.cn/api/paas/v4/chat/completions`
- 模型: `glm-4`
- 认证: `Authorization: Bearer {ZHIPUAI_API_KEY}`
- 同步超时: 连接30s, 读取120s, 重试2次间隔2s
- 流式超时: 连接30s, 读取180s
- 流式格式: SSE, 解析 `data: {"choices":[{"delta":{"content":"...","finish_reason":"stop"}}]}`

### 5.2 Ollama
- API: `POST {OLLAMA_BASE_URL}/api/chat`
- 默认: `http://localhost:11434`
- 模型: `qwen2:7b`
- 健康检查: `GET /api/tags`, 缓存30s
- 流式格式: NDJSON, 每行一个JSON, 解析 `{"message":{"content":"..."},"done":false}`

### 5.3 AI聊天增强
- 知识库上下文: 精确匹配 → 模糊搜索TOP3 → 构建系统提示词
- 数据统计注入: 5分钟缓存的统计信息(热门城市/技能/薪资分布)
- 自动学习: 智谱回答后自动保存到知识库

### 5.4 本地小模型
- 基于规则引擎, 无需外部服务
- 8个FAQ分类, 每分类3条预设回答
- 薪资/技能/经验 QA 各3分类3条
- 关键词检测 → 数据库查询 → FAQ匹配 → 语义相似度 → 默认回答
- 数据分析: 薪资分布/热门岗位TOP5/技能需求TOP10/城市统计

---

## 6. 爬虫模块

### 6.1 支持的平台

| 平台 | 代码 | 解析器选择器 |
|------|------|-------------|
| BOSS直聘 | boss | li.job-card-box, .position-item (需Playwright渲染) |
| 智联招聘 | zhaopin | .joblist-box |
| 前程无忧 | job51 | .j_joblist |
| 猎聘 | liepin | .job-card |

### 6.2 爬取策略

| 参数 | 值 |
|------|----|
| 单次上限 | 200条/平台 |
| 重试 | 最多4次, 指数退避 |
| 请求间隔 | 随机 12-18 秒 |
| 超时 | 45秒 |
| 并发 | 2线程 |
| 去重 | SHA-256(平台+岗位名+公司名) Base64前43字符 |
| 定时 | 每天凌晨2点自动爬取 |

### 6.3 备用数据生成
- 爬取无结果时生成 20-35 条模拟岗位
- 公司名前缀"长沙", 薪资控制在3万以内

### 6.4 过滤规则
- 过滤高级岗位(含"高级/资深/主管/经理/总监/架构师"或5年以上经验)
- 过滤无效岗位(含"培训/外包/中介/刷单")
- 过滤非本科岗位
- 过滤月薪超3万

### 6.5 技能提取
- 6大类预定义词库: 后端/前端/数据AI/运维/测试/通用
- 长词优先匹配, 最多8个

### 6.6 薪资解析
- 正则: `(\d+(?:\.\d+)?)\s*([万千kK])?\s*[-~到]\s*(\d+(?:\.\d+)?)\s*([万千kK])?`
- 格式: 统一为元

### 6.7 User-Agent池 (12个)
```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.1 Safari/605.1.15
Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0
Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/120.0.0.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.0.0
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0
Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/117.0.0.0
```

---

## 7. 数据清洗规则

### 7.1 城市标准化
- 去"市"后缀, 15城市白名单: 北京/上海/深圳/杭州/广州/成都/南京/武汉/西安/长沙/苏州/天津/重庆/合肥/厦门
- 其他 → "未知"

### 7.2 学历标准化
博士/硕士/本科/大专/高中/不限

### 7.3 经验标准化
应届/1年以下/1-3年/3-5年/5-10年/10年以上

### 7.4 薪资格式标准化
- "15-25K" → min=15000, max=25000, unit=K
- "1-1.5万/月" → min=10000, max=15000, unit=万
- "面议" → null

### 7.5 智能排序规则 (GET /api/job/list)
```sql
ORDER BY 
    CASE WHEN min_salary IS NOT NULL THEN 0 ELSE 1 END,
    CASE WHEN skills IS NOT NULL AND skills != '' THEN 0 ELSE 1 END,
    CASE WHEN city IN ('北京','上海','深圳','杭州','广州') THEN 0 ELSE 1 END,
    created_at DESC
```

---

## 8. 操作日志 (AOP)

- 拦截所有标注 `@Log` 的方法
- 记录: 操作描述, 方法名, URI, IP, 用户名, 请求参数, 成功/失败, 错误信息
- IP获取优先顺序: X-Forwarded-For → Proxy-Client-IP → WL-Proxy-Client-IP → X-Real-IP → getRemoteAddr

---

## 9. 目录结构速查 (原 Java 项目)

```
com.example.recruitment
├── RecruitmentSystemApplication.java
├── annotation/ Log.java
├── aspect/ LogAspect.java
├── common/ Result.java, ResultCode.java
├── config/ SecurityConfig, JwtAuthFilter, DataInitializer
├── controller/ AuthController, UserController, JobController,
│              AIController, ModelController, CrawlController,
│              DataController, KnowledgeBaseController, SysLogController
├── crawl/ JobParser, ParserFactory, BossParser, ZhaopinParser,
│         Job51Parser, LiepinParser, PlaywrightBrowser
├── dto/ (6个DTO类)
├── entity/ User, Job, Company, CrawlTask, KnowledgeBase, SysLog
├── exception/ BusinessException, GlobalExceptionHandler
├── mapper/ (6个Mapper接口 + XML)
├── service/
│   └── impl/ UserServiceImpl, JobServiceImpl, AIService,
│             OllamaServiceImpl, LocalModelServiceImpl,
│             KnowledgeBaseServiceImpl, CrawlServiceImpl,
│             DataServiceImpl, SysLogServiceImpl
├── util/ JwtUtil, PasswordUtil, HashUtil, SalaryUtil
└── vo/ UserVO, JobStatVO, JobTrendVO, JobRecommendVO, AIFeedbackVO
```
