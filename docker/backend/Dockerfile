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

# 先装依赖（利用 Docker 层缓存，代码变更不需要重装系统依赖）
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install chromium --with-deps || playwright install chromium

# 业务代码
COPY app ./app
COPY scripts ./scripts
# 注意：**不要** COPY reports/ —— 它是运行时产物目录（日报 Excel 落盘在
# reports/daily/，由 report_service._write_excel_file 自动 mkdir），
# 且已列入 .dockerignore。
# 原写法 `COPY reports ./reports 2>/dev/null || true` 是无效的 Dockerfile 语法
# （Dockerfile 不支持 shell 重定向与 `||`），Linux 下源目录不存在时会直接构建失败。

# 非 root 运行（安全实践）
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "python scripts/init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8080"]
