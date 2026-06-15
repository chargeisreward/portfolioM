FROM python:3.12-slim

# 仅装最小运行时依赖（libpq 用于 psycopg, 不用 gcc 编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) 单独装依赖层（缓存友好）
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 2) 复制源码
COPY . .

EXPOSE 8000

# 健康检查（可选）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/auth/status || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
