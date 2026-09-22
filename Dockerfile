# 巷子里的 AI 掌柜 · 后端镜像
# 构建（仓库根目录）：docker build -t ai-shopkeeper:1.0.0 .
# 运行：docker run -p 8000:8000 -v "$PWD/server/data:/app/server/data" ai-shopkeeper:1.0.0
# 或直接用 docker-compose.yml
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app/server

# 先装依赖，利用镜像层缓存（改代码不会重装依赖）
COPY server/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

# 再拷贝应用代码
COPY server/ ./

# 数据目录（含 SQLite 库与备份），建议挂载卷持久化
RUN mkdir -p /app/server/data
VOLUME ["/app/server/data"]

EXPOSE 8000

# 健康检查：/api/health 免鉴权
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
