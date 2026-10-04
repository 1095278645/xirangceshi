# 部署到腾讯云轻量应用服务器（公网直接访问）

> 面向"想让外网任何人打开就能用"的部署：一台腾讯云轻量 + 两条命令。
> 单机形态的取舍与后续演进（异地备份 / Postgres 多实例）见 [`deployment-evolution.md`](deployment-evolution.md)。

## 一、总览

```
本机（Windows）                        腾讯云轻量（Linux）
scripts\upload_to_server.ps1  ──scp──▶  ~/xirang
                                        bash deploy/up.sh  ──▶  app(:8000) + Caddy(:80/443)
```

| 交付物 | 作用 |
|---|---|
| [`deploy/up.sh`](../deploy/up.sh) | **一键**：装 Docker → 配镜像加速 → 生成 `.env`（含随机令牌）→ 构建 → 起服务 → 健康检查与鉴权自检 → 打印地址与令牌 |
| [`deploy/docker-compose.public.yml`](../deploy/docker-compose.public.yml) | 应用 + Caddy 编排；`8000` 只绑 `127.0.0.1`，对外只开 `80/443` |
| [`deploy/Caddyfile`](../deploy/Caddyfile) | 反向代理；有域名时自动申请/续期 HTTPS 证书；不采信客户端伪造的 `X-Forwarded-For` |
| [`deploy/.env.example`](../deploy/.env.example) | 环境变量样例（`up.sh` 据此生成 `.env`） |
| [`scripts/upload_to_server.ps1`](../scripts/upload_to_server.ps1) | 本机打包**当前工作树**（含未提交改动）并上传；默认不带账本与 Key |

### 服务器怎么选

- 配置：**2C2G 起**（1C1G 也能跑，首次构建会慢；内存 <1G 建议先加 2G swap）。
- 镜像：**Ubuntu 22.04 / Debian 12** 最省事；CentOS 系脚本同样适配（自动识别 `apt`/`yum`）。
- 节点：内地节点带宽便宜、延迟低，但**绑域名需要 ICP 备案**；香港/新加坡节点免备案、延迟略高。

### ⚠️ 如果买的是 Windows 镜像（本项目实测踩过）

判断方法：`3389` 通、`22` 不通 → 基本就是 Windows Server 镜像。

- **Docker Desktop 官方不支持 Windows Server**，本文这套 Linux 容器方案用不了（Linux 容器要 WSL2，Server 上折腾且非官方支持）。
- 两条出路：
  1. **控制台重装为 Ubuntu 22.04 LTS**（推荐，10 分钟）：本文全部步骤直接可用；重装会**清空系统盘**，先确认机器上没有要留的东西。重装后公网 IP 一般不变。
  2. **保留 Windows 原生部署**：项目依赖全是纯 Python wheel（无 uvloop/gunicorn 等 Linux-only 依赖），Windows 上能跑；但需要自行搭 Python 环境 + 用 Caddy for Windows 做 HTTPS + 用计划任务做开机自启，本文不覆盖。
- 无论哪种，**腾讯云轻量控制台的防火墙**都要放通所需端口（Windows 模板默认只开 3389）：SSH `22`、HTTP `80`、HTTPS `443`。

### 有域名 / 没域名，差别只在一步

| 情况 | 部署方式 | 结果 |
|---|---|---|
| 有**已备案**域名（内地节点） | `SITE_ADDRESS=shop.example.com` | `https://shop.example.com`，Caddy 自动证书 |
| **只有公网 IP**（没域名） | `SITE_ADDRESS=https://<IP>` + `DEFAULT_SNI=<IP>` | `https://<公网IP>/`，**Let's Encrypt 的 IP 证书**（6 天、自动续期）—— **这是本节推荐路径**，见[九、没有域名也要 HTTPS](#九没有域名也要-https本项目实测有效的两条路) |
| 有域名但**没备案** | 同上（域名会被腾讯云拦截）或 Cloudflare 隧道 | 未备案域名在内地节点会被换成拦截页，实测走不通，见第九节 |
| 不想折腾 HTTPS | `SITE_ADDRESS=:80` | `http://<公网IP>/`，仅 HTTP，语音会降级 |

> ⚠️ 语音记账需要"安全上下文"（HTTPS 或 localhost）。纯 HTTP 部署时语音按钮会降级为手动输入，其余功能不受影响；**IP 证书同样满足安全上下文**，语音可用。

## 二、控制台准备（腾讯云，约 3 分钟）

1. **防火墙放通端口**：轻量应用服务器控制台 → 防火墙 → 添加规则
   - 有域名或用 **IP 证书**：放通 `80`、`443`（TCP）+ `443`（UDP，HTTP/3 可选）—— IP 证书的 ACME 校验走 80，缺了签不下来
   - 纯 HTTP 演示：只放通 `80`
   - `22` 保持放通（上传代码用）；**不要**对公网放通 `8000`
2. **域名解析**（有域名时）：域名 DNS → 添加 `A` 记录 → 指向服务器公网 IP。
3. **备案**（内地节点 + 域名）：腾讯云 → 备案，一般 7~20 个工作日；未备案的域名解析到内地节点，访问 80/443 会被拦截。

## 三、上传代码（本机 Windows 执行）

```powershell
# 只传代码（服务器上空库起步，AI 走规则降级）
powershell -ExecutionPolicy Bypass -File scripts\upload_to_server.ps1 -Server 1.2.3.4 -User ubuntu

# 连账本/AI Key 一起迁（做演示库迁移时用；含 server/data 与 config.local.json）
powershell -ExecutionPolicy Bypass -File scripts\upload_to_server.ps1 -Server 1.2.3.4 -User ubuntu -WithData

# 用密钥登录
powershell -ExecutionPolicy Bypass -File scripts\upload_to_server.ps1 -Server 1.2.3.4 -User ubuntu -KeyPath "$env:USERPROFILE\.ssh\id_rsa"
```

要点：

- 传的是**当前工作树**，不是 GitHub 上的提交 —— 本机若有未提交改动，只有这个脚本能带上去。
- 默认**排除**账本（`server/data`）、AI Key（`server/config.local.json`）、令牌（`.env`）；压缩包做完会自检，发现账本/密钥立即中止并删除。
- `-WithData` 会覆盖服务器上同名文件，重复上传前先让服务器自己做一次备份（`GET /api/backup/list` 或导出 zip）。
- 若 `-WithData` 上传账本报错，手工传一次即可：
  ```powershell
  scp -r server\data ubuntu@1.2.3.4:xirang/server/
  scp server\config.local.json ubuntu@1.2.3.4:xirang/server/
  ```

## 四、服务器上一键启动

```bash
ssh ubuntu@1.2.3.4
cd ~/xirang

# 无域名（仅 HTTP）
sudo bash deploy/up.sh

# 有已备案域名（自动 HTTPS）
sudo SITE_ADDRESS=shop.example.com bash deploy/up.sh
```

> 用 `sudo` 跑是为了让脚本直接以 root 装 Docker、写 `/etc/docker/daemon.json`、绑 80/443，避免中途反复要密码。
> 若你的账号本来就有免密 sudo，直接 `bash deploy/up.sh` 也可以。

脚本依次做：装 Docker → 写 `/etc/docker/daemon.json` 镜像加速（仅在没配过时）→ 生成 `.env` 与**随机访问令牌** → `docker compose up -d --build` → 等 `/api/health` 变 healthy → **鉴权自检（不带令牌访问 `/api/shops` 必须是 401）** → 打印访问地址、令牌、常用命令。

首次构建 3~8 分钟（国内服务器用 `.env` 里的清华 PyPI 镜像会快很多）。

看到 `======== 部署完成 ========` 就成功了。

### 部署后自检（三条命令，`up.sh` 已自动跑前两条）

```bash
# 1) 鉴权生效：不带令牌访问必须是 401（返回 200 说明令牌没生效，账本裸奔）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/shops

# 2) 时区正确：必须是 +0800
#    账本里的「今天」用的是容器本地时间；slim 镜像若没装 tzdata，
#    北京时间 00:00~08:00 会把流水记到前一天（本地 Windows 跑不出来，只在服务器上暴露）
docker exec ai-shopkeeper date +%z

# 3) HTTPS 与证书（有域名时；HPKP/HSTS 头来自 Caddy）
curl -sI https://shop.example.com/api/health | head -3
```

## 五、打开与登录

1. 浏览器打开 `http://<公网IP>/` 或 `https://<域名>/`（首页与静态资源免鉴权，所以能打开登录界面）。
2. 底部「更多」→「设置」→ 粘贴脚本打印的**访问令牌** → 保存（令牌只存在浏览器本地，不上传）。
3. 小程序：设置页把「后端地址」填成 `https://<域名>`，点「测试连接」；
   开发者工具里调试可勾「不校验合法域名」；**正式发布**需要在微信公众平台把域名加进 request 合法域名
   （官方要求 HTTPS 且已完成 ICP 备案，见 [微信官方网络说明](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)）。

## 六、演示数据与 AI Key

- **演示库迁移（推荐）**：本机 `python scripts/seed_demo_data.py` 灌好数据后，用 `-WithData` 上传，数据和界面表现与本地一致。
- **服务器上空库灌数据**（镜像里没有 `scripts/`，它是被 `.dockerignore` 排除的，必须先 `docker cp` 进去）：
  ```bash
  docker cp scripts ai-shopkeeper:/app/scripts
  docker exec -it ai-shopkeeper python /app/scripts/seed_demo_data.py
  ```
- **跨月补灌（库里已有本月流水时）**：`seed_demo_data.py` 的幂等门会**拒绝执行**（防止流水翻倍），
  `--current-only` 也会被同一道门挡住，而 `--force` 是"清空整库重灌"（会丢历史月）。
  只想补"已有数据之后 ~ 今天"这段窗口时，把 `MONTH_START/DAYS` 指向缺失区间后调
  `seed_transactions()` 即可（同一套确定性生成器，只做加法、不删数据），
  参考 `deliverables/_seed_patch.py`；操作前先 `cp` 一份账本做回滚点。
- **AI Key 两种填法**：`.env` 里 `DEEPSEEK_API_KEY=`（改完重跑 `bash deploy/up.sh`），或在网页端「设置」页填写。
  > ⚠️ 实测提醒：网页端「设置」页保存的 Key 实际写在**容器内**的 `/app/server/config.local.json`，
  > 而容器只挂了 `server/data` —— 所以**重跑 `up.sh` 重建容器后会丢**。长期部署请写进 `.env`。
- **能力档建议直接切 full**：代码默认 `SHOP_API_PROFILE=core`，会**不挂载** `metrics / payment / finance / stock / invoice / notify(主动触达) / accounting(会计报表)`，
  前端点到就 404，看起来像"功能缺失"（本项目公网部署就是这么踩到并切过去的）。改 `.env` 的 `SHOP_API_PROFILE=full` 后重跑一次即可。

## 七、日常运维

```bash
cd ~/xirang
C="docker compose --env-file .env -f deploy/docker-compose.public.yml"

$C ps                      # 容器状态
$C logs -f --tail 100 shopkeeper   # 应用日志
$C logs -f --tail 100 caddy        # 证书/代理日志
$C restart shopkeeper      # 重启应用
$C down                    # 停服（数据卷与 server/data 保留）

# 更新代码：本机重跑 upload 脚本 → 服务器上
bash deploy/up.sh          # 幂等，账本不动
```

- **备份**：应用启动时与每 6 小时自动做一致性快照（`server/data/backups`）；网页端「管理台 → 数据备份」可导出 zip、一键恢复。
- **异地备份（强烈建议）**：按 [`deployment-evolution.md`](deployment-evolution.md) 第 1 步接 Litestream → 对象存储（[`ops/litestream.yml`](../ops/litestream.yml)）。

## 八、安全清单（公网部署逐条对照）

1. **必须设置 `SHOP_ACCESS_TOKEN`**：不设置时 `/api/**` 完全放行，任何人拿到地址即可读写账本、消耗模型额度（`server/auth.py` 的默认行为是为本地演示保留的）。
2. **只暴露 80/443**：编排里 `8000` 已绑定到 `127.0.0.1`，不要改成 `0.0.0.0`。
3. **限流按真实客户端 IP**：Caddy 默认用直连客户端 IP 覆盖 `X-Forwarded-For`（未配置 `trusted_proxies` 时不采信客户端传入的值），服务端拿到的就是真实 IP。**若前面再套一层 Cloudflare/CDN、或在 Caddy 里配了 `trusted_proxies`，必须重新确认这一点**，否则限流可被伪造头绕过。
4. **只跑 1 个副本**：SQLite 单写 + 限流是进程内内存实现（`server/ratelimit.py`），横向扩容会数据分裂、限流失效。要扩容先按演进文档迁 Postgres + Redis。
5. **密钥与账本不入库**：`.env`、`server/config.local.json`、`server/data/` 均在 `.gitignore`；提交前用 `git check-ignore -v <path>` 复核。
6. **令牌轮换**：改 `.env` 的 `SHOP_ACCESS_TOKEN` → 重跑 `up.sh` → 各端在「设置」页填新令牌。
7. **服务器只留必要端口**：`22` 建议改成密钥登录并限制来源 IP；不要安装来源不明的面板插件。

## 九、没有域名也要 HTTPS（本项目实测有效的两条路）

语音记账、PWA「添加到主屏幕」都要求**安全上下文**（HTTPS 或 localhost）—— `http://IP` 不行，浏览器会拒绝麦克风（项目里已自动降级为手动输入）。

### 方案 A（推荐，已实测跑通）：Let's Encrypt 的 **IP 地址证书**

Let's Encrypt 自 2026-01 起**正式支持给裸 IP 签发证书**（[官方公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)，6 天有效期；[certbot 支持说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)，Caddy 2.10+ 也可）。**裸 IP 不触发备案拦截**（备案只拦"未备案域名"），所以内地节点也能拿到浏览器信任的 HTTPS —— 没域名时这是最干净的方案。

```bash
# .env 里改成这两行，然后重跑 sudo bash deploy/up.sh
SITE_ADDRESS=https://<公网IP>
DEFAULT_SNI=<公网IP>
```

`deploy/Caddyfile` 里对应的关键配置（已内置，无需手改）：

```caddyfile
{
	# 客户端对 IP 字面量按 RFC 6066 **不发 SNI**；缺这个值 Caddy 在握手阶段选不到证书，
	# 会直接回 TLS internal_error —— 现象是"证书明明签出来了，但就是连不上"（实测踩过）
	default_sni {$DEFAULT_SNI:localhost}
}

{$SITE_ADDRESS:localhost} {
	# Caddy 默认给 IP 发「内部自签」证书（issuer=local，浏览器不信任），
	# 必须显式声明 ACME 签发者才会去签 LE 的 IP 证书；IP 证书是 6 天短证书，要选 shortlived profile
	tls {
		issuer acme {
			profile shortlived
		}
	}
	reverse_proxy shopkeeper:8000
}
```

要点：

- 证书 **6 天**有效，Caddy 会**自动续期**（走 ARI）；**别删 `caddy_data` 卷**，否则要重新签发（重跑 `up.sh` 也能重签）。
- 自检：`openssl s_client -connect <IP>:443` 应返回 `Verify return code: 0 (ok)`；`curl -sI https://<IP>/` 应 200。
- 起来后 `http://<IP>` 会自动 `308` 跳到 HTTPS。
- **小程序正式版仍要求"已备案域名"**（IP 不能作为 request 合法域名），IP 证书只解决网页端与真机调试。

### 方案 B：Cloudflare 隧道（不想对公网开放 80/443 时）

```bash
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared   # 国内可能很慢
chmod +x /usr/local/bin/cloudflared
cloudflared tunnel --url http://localhost:8000     # 临时：地址随机、重启就变
```

把域名托管到 Cloudflare 建**命名隧道**可得固定地址，不碰 80/443、也不需要备案。注意隧道多了一层代理，服务端限流拿到的客户端 IP 要重新确认（见安全清单第 3 条）。

### ❌ 走不通的路（都实测或查证过，别再花时间）

| 想法 | 为什么不行 |
|---|---|
| `sslip.io` / `nip.io` 等"免费域名指向内地 IP" | **会被腾讯云拦截**：实测 Caddy 申请证书时，ACME 校验拿到的响应是 `https://dnspod.qcloud.com/static/webblock.html?d=<域名>` —— 未备案域名解析到内地服务器，HTTP 被换成拦截页，证书自然签不下来 |
| 自签名证书 | 证书报错的页面在 Chrome 里**不算安全上下文**，麦克风照样被拒；除非把自签 CA 装进**每台手机**的信任库 |
| 给 IP 签常规 90 天免费证书 | 免费 CA 的常规证书不给裸 IP 签发（LE 只提供上面那种 6 天短证书） |

## 十、排障

| 现象 | 原因与处理 |
|---|---|
| 浏览器打不开 | 防火墙没放通 80/443；DNS 未解析；`docker compose ... logs caddy` 看证书申请失败原因 |
| 内地节点域名 80/443 被拦 | 域名未备案：会被换成 `dnspod.qcloud.com/static/webblock.html` 拦截页 → 用第九节「IP 证书」方案，或完成备案后再切 |
| `https://IP` 连不上（TLS `internal_error` / alert 80） | 缺 `DEFAULT_SNI=<IP>`：客户端对 IP 不发 SNI，Caddy 选不到证书就握手失败（现象是"证书签出来了却连不上"）|
| IP 证书 6 天后失效 | `caddy_data` 卷被删或未持久化（证书与续期状态在里面）；重跑 `up.sh` 会重新签发 |
| `https` 证书申请失败 | 域名没解析到本机 / 80 端口被宝塔、nginx 占用（`ss -ltnp \| grep :80`）|
| 打开是 502 | 应用没起来：`logs shopkeeper`；`docker inspect -f '{{.State.Health.Status}}' ai-shopkeeper` |
| 页面能开，接口 401 | 正常：去「设置」页填令牌 |
| 某些页面点开就 404（运行指标 / 库存 / 发票 / 主动触达 / 会计报表） | 能力档还是 `core`：把 `.env` 的 `SHOP_API_PROFILE` 改成 `full`，重跑 `sudo bash deploy/up.sh` |
| Caddy 反复重启、80/443 连不上 | 看 `docker logs ai-shopkeeper-caddy`：Caddyfile 里有非法指令（例如站点块里的 `timeouts` 不是合法指令）会**启动即失败** |
| 流水/复盘日期差一天 | 容器时区不是 +0800：`docker exec ai-shopkeeper date +%z` 应为 `+0800`；不是就说明镜像缺 `tzdata`（[Dockerfile](../Dockerfile) 已装，旧镜像需重建）|
| 构建卡在 pip | `.env` 里 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| `docker pull` 超时 | 检查 `/etc/docker/daemon.json` 的 `registry-mirrors`（脚本已配腾讯云内网镜像）|
| 语音按钮不可用 | 页面不是 HTTPS：用第九节「IP 证书」方案，或配域名/隧道（**自签名证书不管用**，证书报错的页面不是安全上下文）|
| 小程序连不上 | 后端地址要填 `https://域名`；正式版需在公众平台配置合法域名（须备案） |

## 十一、其它部署形态

- **不想买服务器**：本机 + Cloudflare Tunnel（`cloudflared tunnel --url http://localhost:8000`），5 分钟出公网地址，适合临时演示。
- **不想运维**：Railway / Render / Fly.io / Zeabur 直接部署本仓库 Dockerfile，**注意必须挂持久卷**（SQLite 账本是文件），且只能 1 个实例；免费档无持久卷，重启即丢数据。
- **国内合规托管**：微信云托管（小程序可用 `callContainer` 免备案域名调用），但本地磁盘不持久，需要改造存储层。
