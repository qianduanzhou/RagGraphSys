# -*- coding: utf-8 -*-
# 后端镜像：FastAPI + LangGraph。
# 注意：requirements.txt 跨平台干净（无 pywin32 等 Windows 专用包），可直接在 Linux 构建。
FROM python:3.11-slim

# 运行期环境：不缓冲（日志实时）、不写 .pyc、固定时区
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖：curl 供 HEALTHCHECK 使用，tzdata 固定时区
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（独立一层，命中 docker 缓存，源码改动不重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码（.dockerignore 已排除 .venv / 测试 / 临时文件 / 本地 .env）
COPY . .

EXPOSE 8000

# 健康检查：调 /api/health，它在 DB 缺失时返回 degraded 但仍 200，故用 status 字段判定更稳。
# 这里用 HTTP 状态码（degraded 也是 200，算作存活即可）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# 生产启动：uvicorn 直跑（main.py 的 __main__ 仅用于本地）。
# --workers 按并发需要调整；graph.invoke 是同步阻塞，多 worker 才能并发处理 /chat。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
