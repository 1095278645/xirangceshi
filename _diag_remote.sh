#!/bin/bash
# 比对线上容器里的 ai.py 与本机版本的关键修复是否一致
echo "=== 容器内 ai.py 关键标记 ==="
sudo -n docker exec ai-shopkeeper sh -c '
  echo -n "  含 deepseek-v4.1-flash 白名单: "; grep -c "deepseek-v4.1-flash" /app/server/ai.py || true
  echo -n "  含 _DEFAULT_REASONING_EFFORT : "; grep -c "_DEFAULT_REASONING_EFFORT" /app/server/ai.py || true
  echo -n "  含 thinking 参数构造           : "; grep -c "thinking" /app/server/ai.py || true
  echo -n "  含 递增预算重试               : "; grep -c "_CHAT_TOKEN_CAP" /app/server/ai.py || true
'
echo
echo "=== 容器内 ai.py sha256 ==="
sudo -n docker exec ai-shopkeeper sha256sum /app/server/ai.py
echo "=== 容器内 copy 相关文件 sha256 ==="
sudo -n docker exec ai-shopkeeper sh -c 'sha256sum /app/server/static/js/pages/copy.js /app/server/routers/insights.py 2>/dev/null || ls /app/server/routers/'
echo "=== 容器创建时间 ==="
sudo -n docker inspect ai-shopkeeper --format '{{.Created}}'
echo
echo "=== 线上配置：模型 / 编排模式 / 能力档 ==="
grep -E '^(DEEPSEEK_MODEL|DEEPSEEK_BASE_URL|AI_PIPELINE|SHOP_API_PROFILE)=' ~/xirang/.env
