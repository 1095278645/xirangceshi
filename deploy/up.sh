#!/usr/bin/env bash
# 巷子里的 AI 掌柜 · 服务器一键部署（腾讯云轻量 / 任意 Linux）
#
# 用法（**在仓库根目录**执行）：
#   bash deploy/up.sh
#   SITE_ADDRESS=shop.example.com bash deploy/up.sh    # 指定已备案域名，Caddy 自动 HTTPS
#
# 幂等：重复执行 = 重新构建并拉起，账本在 server/data，不会丢。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="deploy/docker-compose.public.yml"
ENV_FILE=".env"
APP_CONTAINER="ai-shopkeeper"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f Dockerfile ] || die "请在仓库根目录执行：bash deploy/up.sh"
[ -f "$COMPOSE_FILE" ] || die "缺少 $COMPOSE_FILE（deploy/ 目录要跟仓库一起上传）"
[ -f deploy/.env.example ] || die "缺少 deploy/.env.example"
command -v curl >/dev/null 2>&1 || die "缺少 curl：apt install -y curl 或 yum install -y curl 后重试"

# ---------- 1. 权限 ----------
if [ "$(id -u)" -eq 0 ]; then
	SUDO=""
else
	command -v sudo >/dev/null 2>&1 || die "当前不是 root 且没有 sudo，请用 root 执行"
	SUDO="sudo"
fi

# ---------- 2. 安装 Docker（缺什么装什么）----------
if ! command -v docker >/dev/null 2>&1; then
	log "未检测到 Docker，正在安装（Aliyun 镜像源，约 1~3 分钟）"
	curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
	$SUDO sh /tmp/get-docker.sh --mirror Aliyun
	$SUDO systemctl enable --now docker >/dev/null 2>&1 || true
fi
if ! docker compose version >/dev/null 2>&1 && ! $SUDO docker compose version >/dev/null 2>&1; then
	die "缺少 docker compose 插件：apt install -y docker-compose-plugin（或 yum install -y docker-compose-plugin）后重试"
fi

# 当前用户不在 docker 组时，用 sudo 调 docker
if docker info >/dev/null 2>&1; then
	DOCKER="docker"
else
	DOCKER="$SUDO docker"
fi

# ---------- 3. 镜像加速（只在没有既有配置时写入，不覆盖你的设置）----------
if [ ! -f /etc/docker/daemon.json ]; then
	log "写入 /etc/docker/daemon.json（腾讯云内网镜像加速）"
	$SUDO mkdir -p /etc/docker
	printf '%s\n' '{' '  "registry-mirrors": ["https://mirror.ccs.tencentyun.com"]' '}' \
		| $SUDO tee /etc/docker/daemon.json >/dev/null
	$SUDO systemctl restart docker >/dev/null 2>&1 || true
	DOCKER="$SUDO docker"
fi

# ---------- 4. 生成 .env（含随机访问令牌）----------
if [ ! -f "$ENV_FILE" ]; then
	log "生成 $ENV_FILE（自动创建高强度访问令牌）"
	TOKEN="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
	sed "s|^SHOP_ACCESS_TOKEN=.*|SHOP_ACCESS_TOKEN=${TOKEN}|" deploy/.env.example > "$ENV_FILE"
fi
if [ -n "${SITE_ADDRESS:-}" ]; then
	sed -i "s|^SITE_ADDRESS=.*|SITE_ADDRESS=${SITE_ADDRESS}|" "$ENV_FILE"
fi
if grep -q '^SITE_ADDRESS=$' "$ENV_FILE"; then
	log "未指定域名，按「仅 HTTP」模式部署（SITE_ADDRESS=:80）"
	sed -i 's|^SITE_ADDRESS=.*|SITE_ADDRESS=:80|' "$ENV_FILE"
	PUBIP="$(curl -s --max-time 5 https://api.ipify.org || true)"
	warn "想要 HTTPS（手机语音/PWA 需要）但没域名？把 .env 改成下面两行后重跑本脚本，"
	warn "会走 Let's Encrypt 的 IP 地址证书（6 天、自动续期，裸 IP 不触发备案拦截）："
	warn "  SITE_ADDRESS=https://${PUBIP:-<公网IP>}"
	warn "  DEFAULT_SNI=${PUBIP:-<公网IP>}"
fi
if ! grep -Eq '^SHOP_ACCESS_TOKEN=.+' "$ENV_FILE"; then
	die ".env 里 SHOP_ACCESS_TOKEN 为空：公网部署必须设置，否则任何人都能读写账本"
fi

SITE="$(grep '^SITE_ADDRESS=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
if [ "$SITE" = ":80" ]; then
	warn "当前是 HTTP 部署：手机浏览器的语音录入需要 HTTPS（见上面的 IP 证书提示，或配已备案域名）。"
fi

# ---------- 5. 端口占用提醒（宝塔/nginx 常见的坑）----------
mkdir -p server/data 2>/dev/null || $SUDO mkdir -p server/data
if command -v ss >/dev/null 2>&1; then
	for p in 80 443; do
		if ss -ltn 2>/dev/null | grep -q ":${p} "; then
			if ! $DOCKER ps --format '{{.Names}}' 2>/dev/null | grep -q '^ai-shopkeeper-caddy$'; then
				warn "端口 $p 已被占用（可能是宝塔面板/nginx）：请先停掉它，否则 Caddy 无法启动"
			fi
		fi
	done
fi

# ---------- 6. 构建并启动 ----------
log "构建并启动（首次构建约 3~8 分钟，请耐心等待）"
$DOCKER compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

# ---------- 7. 等健康检查 ----------
log "等待应用健康检查（/api/health）"
STATUS=""
for _ in $(seq 1 60); do
	STATUS="$($DOCKER inspect -f '{{.State.Health.Status}}' "$APP_CONTAINER" 2>/dev/null || echo starting)"
	if [ "$STATUS" = "healthy" ]; then break; fi
	sleep 3
done
if [ "$STATUS" != "healthy" ]; then
	warn "应用 3 分钟内没到 healthy（当前：$STATUS），最近日志："
	$DOCKER compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail 40 shopkeeper || true
fi

# ---------- 8. 鉴权自检：不带令牌访问 /api/shops 必须是 401 ----------
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/api/shops || true)"
if [ "$CODE" = "401" ]; then
	log "鉴权自检通过：不带令牌访问 /api/shops 返回 401"
else
	warn "鉴权自检异常：不带令牌访问 /api/shops 返回 $CODE（期望 401），请检查 SHOP_ACCESS_TOKEN"
fi

# ---------- 8.1 时区自检：容器必须是 +0800，否则账本的「今天」会记错一天 ----------
CTZ="$($DOCKER exec "$APP_CONTAINER" date +%z 2>/dev/null || true)"
HTZ="$(date +%z)"
CTIME="$($DOCKER exec "$APP_CONTAINER" date '+%Y-%m-%d %H:%M %Z' 2>/dev/null || echo '未知')"
if [ -z "$CTZ" ]; then
	warn "拿不到容器时区（容器可能没在跑）"
elif [ "$CTZ" != "$HTZ" ]; then
	warn "容器时区 $CTZ 与主机时区 $HTZ 不一致：记账日期/每日复盘可能落在前一天（镜像需装 tzdata，见 Dockerfile）"
else
	log "时区自检通过：容器与主机同为 $HTZ"
fi

# ---------- 8.2 AI 连通性自检 ----------
# 为什么必须查：配了 Key 不等于 Key 能用。实测踩过 —— 某网关的 Key 配了官方端点，
# 所有 AI 调用 401，而各功能会**安静地退回规则兜底**（页面照常出内容，只是变朴素）。
# 这类"静默降级"不主动探一把就发现不了，等到评审现场发现就晚了。
TOKEN_FOR_CHECK="$(grep '^SHOP_ACCESS_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
AI_JSON="$(curl -s --max-time 90 -H "X-Shop-Token: $TOKEN_FOR_CHECK" http://127.0.0.1:8000/api/health/ai || true)"
case "$AI_JSON" in
	*'"reachable": true'*|*'"reachable":true'*)
		log "AI 自检通过：模型可调用（编排模式：$(grep -m1 '^AI_PIPELINE=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r' || echo fast)）" ;;
	*'"configured": false'*|*'"configured":false'*)
		warn "未配置 AI Key：文案 / 复盘 / 画像将走规则兜底（记账与账本不受影响）" ;;
	*)
		warn "AI 配了 Key 但调不通，各功能会静默退回规则兜底。接口返回：$AI_JSON"
		warn "  最常见原因：DEEPSEEK_API_KEY 与 DEEPSEEK_BASE_URL 不是同一家（改 .env 后重跑本脚本）" ;;
esac

# ---------- 9. 汇总 ----------
IP="$(curl -s --max-time 5 https://api.ipify.org || true)"
if [ -z "$IP" ]; then IP="<服务器公网IP>"; fi
TOKEN_SHOWN="$(grep '^SHOP_ACCESS_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"

printf '\n\033[1m======== 部署完成 ========\033[0m\n'
if [ "$SITE" = ":80" ]; then
	printf '访问地址 : http://%s/\n' "$IP"
else
	printf '访问地址 : https://%s/   （HTTP 自动跳 HTTPS）\n' "$SITE"
fi
printf '访问令牌 : %s\n' "$TOKEN_SHOWN"
printf '容器时间 : %s（主机 %s）\n' "$CTIME" "$(date '+%Y-%m-%d %H:%M %Z')"
printf '下一步   : 打开网页 → 底部「更多」→「设置」→ 粘贴上面的令牌 → 保存\n'
printf '看日志   : %s compose --env-file %s -f %s logs -f --tail 100 shopkeeper\n' "$DOCKER" "$ENV_FILE" "$COMPOSE_FILE"
printf '更新代码 : 本机重跑 scripts/upload_to_server.ps1，再在服务器执行 bash deploy/up.sh\n'
printf '灌演示数据（可选）: %s cp scripts %s:/app/scripts && %s exec -it %s python /app/scripts/seed_demo_data.py\n' \
	"$DOCKER" "$APP_CONTAINER" "$DOCKER" "$APP_CONTAINER"
printf '\n注意：这串令牌是进入你账本的唯一凭据，别发到公开群或截图里。\n'
