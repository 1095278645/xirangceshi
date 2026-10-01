# 巷子里的 AI 掌柜 · 后端镜像
# 构建（仓库根目录）：docker build -t ai-shopkeeper:1.0.0 .
# 运行：docker run -p 8000:8000 -v "$PWD/server/data:/app/server/data" ai-shopkeeper:1.0.0
# 或直接用 docker-compose.yml
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app/server

# 时区数据：slim 镜像不含 tzdata，缺它时 TZ 不生效，容器会按 UTC 算「今天」——
# 北京时间 00:00~08:00 之间记账/复盘/报税日历会落到前一天（本地 Windows 跑不出来）。
# 部署后可用 `docker exec ai-shopkeeper date +%z` 复核，应为 +0800。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
    && printf '%s\n' "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用镜像层缓存（改代码不会重装依赖）
# 依赖源可覆盖（默认官方 PyPI；国内服务器构建时传
#   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 可显著加速）
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY server/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.lock

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
