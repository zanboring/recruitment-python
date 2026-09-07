# 环境变量配置

## .env 文件模板

```env
# ========== 数据库 ==========
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

# ========== 智谱AI (GLM-4) ==========
# 注册地址: https://open.bigmodel.cn/
ZHIPUAI_API_KEY=your_zhipu_api_key

# ========== Ollama 本地大模型 ==========
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2:7b

# ========== 爬虫 ==========
CRAWL_MAX_JOBS=200
CRAWL_RETRY_TIMES=4
CRAWL_DELAY_MIN=12
CRAWL_DELAY_MAX=18
CRAWL_TIMEOUT=45

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
  model: ${OLLAMA_MODEL:qwen2:7b}

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

# 2. 运行建表脚本 (表结构见 TECH_SPEC.md 第1节)
# 或使用 SQLAlchemy 的 create_all()
```

## Playwright 浏览器安装

```bash
playwright install chromium
```
