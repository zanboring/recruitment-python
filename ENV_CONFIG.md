# 环境变量配置

## .env 文件模板

```env
# ========== 数据库 ==========
APP_ENV=development

DB_HOST=localhost
DB_PORT=3306
DB_NAME=recruitment_db
DB_USERNAME=root
DB_PASSWORD=your_mysql_password

# ========== JWT ==========
# 至少32字符的随机密钥, Base64编码
# 生成方式: python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET=your_jwt_secret_base64
JWT_EXPIRATION=86400000

# ========== AI 对话模型（OpenAI 兼容协议）==========
# 主服务商：deepseek（默认）| zhipu。另一个自动作为备选，实现多供应商容灾。
AI_PROVIDER=deepseek

# DeepSeek（默认主模型）
# 申请地址: https://platform.deepseek.com/api_keys
# 密钥留空时自动降级到本地 Ollama，链路不会中断
DEEPSEEK_API_KEY=
DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
DEEPSEEK_MODEL=deepseek-flash
# 思考模式强度：none 关闭（默认）/ low / high / max
# 招聘问答用 none 即可，开启会显著增加延迟与 token 消耗
DEEPSEEK_REASONING_EFFORT=none

# 智谱 GLM（备选服务商，可留空）
ZHIPUAI_API_KEY=
ZHIPUAI_API_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
ZHIPUAI_MODEL=glm-4-flash

# ========== Ollama 本地大模型 ==========
# 本地不是「一个备胎」，而是按能力分工的两个模型：
#   OLLAMA_MODEL       语言类 —— 对话生成、分析报告（语言表达与事实准确性更好）
#   OLLAMA_CODE_MODEL  代码类 —— 工具识别、JSON 结构化抽取（准确率相同但快一倍）
#   OLLAMA_TOOL_MODEL  显式指定工具识别模型；留空则自动用 CODE_MODEL，再回退 MODEL
# 分工依据来自实测，可执行 `python scripts/bench_local_models.py` 在本机复现。
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_CODE_MODEL=qwen2.5-coder:7b
OLLAMA_TOOL_MODEL=

# ========== Function Calling 工具识别前置过滤 ==========
# 朴素实现对每条消息都发起一次「是否需要查库」的 LLM 调用（约 2.9s/次）。
# 规则保守：仅在「消息很短且无领域信号」时跳过。发现漏判可整体关闭回退。
AI_TOOL_PREFILTER_ENABLED=true
AI_TOOL_PREFILTER_MAX_CHARS=24

# ========== 知识库自动入库 ==========
# 每次问答都会把回答沉淀进知识库，形成「AI 内容被当作知识召回」的闭环，
# 答错时错误会被固化。需要「只信人工内容」时关闭此开关。
AI_AUTO_LEARN_ENABLED=true

# ========== 输出长度上限（成本与延迟的硬边界）==========
# 不设上限时：云端按 token 计费的输出不可控，本地模型可能让用户等上几分钟
AI_MAX_OUTPUT_TOKENS=1024
AI_ANALYSIS_MAX_OUTPUT_TOKENS=2048
AI_TOOL_DETECT_MAX_TOKENS=128

# ========== AI 用量与成本统计 ==========
# 记录每次 LLM / 向量化调用的 token、耗时与降级层级，落 ai_usage 表
AI_USAGE_LOG_ENABLED=true
# 单价按「元 / 百万 token」计，默认值仅供估算，请按实际采购价调整
USAGE_PRICE_DEEPSEEK_INPUT=2.0
USAGE_PRICE_DEEPSEEK_OUTPUT=8.0
USAGE_PRICE_ZHIPU_INPUT=0.1
USAGE_PRICE_ZHIPU_OUTPUT=0.1
USAGE_PRICE_LOCAL=0.0

# ========== 知识库语义检索（向量 RAG）==========
# 复用上面的 ZHIPUAI_API_KEY；不填 Key 时自动降级为关键词检索
EMBEDDING_ENABLED=true
EMBEDDING_MODEL=embedding-3
EMBEDDING_API_URL=https://open.bigmodel.cn/api/paas/v4/embeddings
EMBEDDING_DIMENSIONS=1024

# 向量化后端：
#   zhipu   云端智谱 embedding-3（需 ZHIPUAI_API_KEY）
#   ollama  本地 Ollama（离线、零成本、数据不出内网）
#           使用前需执行：ollama pull nomic-embed-text
EMBEDDING_BACKEND=zhipu
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# 知识库检索策略：
#   auto      语义优先，无命中或异常时降级关键词（默认）
#   semantic  仅语义向量检索
#   keyword   仅关键词检索（无需密钥，可离线）
#   hybrid    向量 + 关键词的 RRF 融合
# 各策略的检索质量对比：python scripts/eval_rag.py
RAG_RETRIEVAL_STRATEGY=auto

# ========== 爬虫 ==========
CRAWL_MAX_JOBS=200
CRAWL_RETRY_TIMES=4
CRAWL_DELAY_MIN=12
CRAWL_DELAY_MAX=18
CRAWL_TIMEOUT=45

# ========== 接口限流（滑动窗口，单进程内存）==========
# 多副本部署时请关闭并改用网关 / Redis 分布式限流
RATE_LIMIT_ENABLED=true
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_DEFAULT_PER_MINUTE=120
RATE_LIMIT_LOGIN_PER_MINUTE=10
RATE_LIMIT_REGISTER_PER_MINUTE=5
RATE_LIMIT_AI_PER_MINUTE=20
# 是否信任 X-Forwarded-For 作为客户端 IP（默认 false，安全优先）
# XFF 由客户端可随意伪造，若用作限流维度，等于「换个头换一份配额」，
# 会直接绕过登录防爆破。仅当应用确实部署在可信反向代理之后时置为 true。
RATE_LIMIT_TRUST_FORWARDED_FOR=false

# ========== 服务 ==========
SERVER_PORT=8080

# ========== CORS ==========
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

## application.yml 等效配置 (原 Java 项目, 仅供参考)

```yaml
server:
  port: 8080
  encoding: UTF-8

spring:
  datasource:
    url: jdbc:mysql://localhost:3306/recruitment_db?useUnicode=true&characterEncoding=utf8mb4&serverTimezone=Asia/Shanghai&useSSL=true
    username: ${DB_USERNAME}
    password: ${DB_PASSWORD}
    hikari:
      maximumPoolSize: 20
      minimumIdle: 5
      connectionTimeout: 30000
      idleTimeout: 600000
      maxLifetime: 1800000
  jackson:
    date-format: yyyy-MM-dd HH:mm:ss
    time-zone: Asia/Shanghai
    default-property-inclusion: non_null
  servlet:
    multipart:
      max-file-size: 50MB
      max-request-size: 50MB

mybatis:
  type-aliases-package: com.example.recruitment.entity
  mapper-locations: classpath*:mapper/*.xml
  configuration:
    map-underscore-to-camel-case: true
    cache-enabled: true

pagehelper:
  helperDialect: mysql

jwt:
  secret: ${JWT_SECRET}
  expiration: ${JWT_EXPIRATION:86400000}

zhipuai:
  api:
    key: ${ZHIPUAI_API_KEY:}

ollama:
  enabled: ${OLLAMA_ENABLED:true}
  base-url: ${OLLAMA_BASE_URL:http://localhost:11434}
  # 语言类：对话生成
  model: ${OLLAMA_MODEL:qwen2.5:14b}
  # 代码类：工具识别 / 结构化抽取
  code-model: ${OLLAMA_CODE_MODEL:qwen2.5-coder:7b}
  # 单独指定工具识别模型（留空则用 code-model，再回退 model）
  tool-model: ${OLLAMA_TOOL_MODEL:}

crawl:
  enable-backup-data: true
  enable-scheduled: true

cors:
  allowed-origins: http://localhost:5173,http://localhost:5174,...

logging:
  file: logs/recruitment-system.log
  max-size: 10MB
  max-history: 30
```

## 数据库初始化

```bash
# 1. 创建数据库
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS recruitment_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 2. 运行初始化脚本（自动建表 + 插入默认管理员 admin/admin123）
python scripts/init_db.py
```

## Playwright 浏览器安装

```bash
playwright install chromium
```
