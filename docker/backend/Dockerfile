# syntax=docker/dockerfile:1
# 后端镜像：FastAPI + Playwright Chromium（爬虫依赖放本镜像内，保证开箱即用）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1

WORKDIR /app

# Playwright 系统依赖（Chromium 运行所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation libglib2.0-0 libx11-6 libxcb1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Playwright 浏览器下载源。默认走官方 CDN；**国内网络务必覆盖**：
# 官方 cdn.playwright.dev 实测约 100 KB/s（Chromium 187 MB 需 1.5 小时以上），
# 换 npmmirror 实测约 10 MB/s（约 18 秒），相差约 100 倍。
#   PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright docker compose up -d --build
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.playwright.dev

# 先装依赖（利用 Docker 层缓存，代码变更不需要重装系统依赖）
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}" playwright install chromium --with-deps \
    || PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}" playwright install chromium

# 业务代码
COPY app ./app
COPY scripts ./scripts
# 注意：**不要** COPY reports/ —— 它是运行时产物目录（日报 Excel 落盘在
# reports/daily/，由 report_service._write_excel_file 自动 mkdir），
# 且已列入 .dockerignore。
# 原写法 `COPY reports ./reports 2>/dev/null || true` 是无效的 Dockerfile 语法
# （Dockerfile 不支持 shell 重定向与 `||`），Linux 下源目录不存在时会直接构建失败。

# 非 root 运行（安全实践）
# ⚠️ 运行时数据目录 /app/data 必须在这一步就建好并归属 appuser：
#    compose 的 `appdata` 具名卷挂载到该路径，而 Docker 初始化**空的具名卷**时，
#    只会继承「镜像内该路径已存在的目录」的内容与属主。
#    镜像里没有这个目录 → 卷被建成 root:root → 非 root 的 appuser 写 SQLite
#    时报 (sqlite3.OperationalError) unable to open database file，容器起不来。
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "python scripts/init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8080"]
