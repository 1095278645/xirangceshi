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
| [`deploy/Caddyfile`](../deploy/Caddyfile) | 反向代理；有域名时自动申请/续期 HTTPS 证书；强制改写 `X-Forwarded-For` |
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
| 有**已备案**域名（内地节点） | `SITE_ADDRESS=shop.example.com bash deploy/up.sh` | `https://shop.example.com`，Caddy 自动证书 |
| 有域名但**没备案** | 走「[五、免备案分支](#五免备案分支cloudflare-tunnel)」 | Cloudflare 域名 + 隧道，不碰 80/443 |
| 只有公网 IP | `bash deploy/up.sh`（自动 `SITE_ADDRESS=:80`） | `http://<公网IP>/`，**仅 HTTP** |

> ⚠️ 语音记账需要"安全上下文"（HTTPS 或 localhost）。纯 HTTP 部署时语音按钮会降级为手动输入，其余功能不受影响。

## 二、控制台准备（腾讯云，约 3 分钟）

1. **防火墙放通端口**：轻量应用服务器控制台 → 防火墙 → 添加规则
   - 有域名：放通 `80`、`443`（TCP）+ `443`（UDP，HTTP/3 可选）
   - 只有 IP：放通 `80`
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
- **服务器上空库灌数据**：
  ```bash
  docker cp scripts ai-shopkeeper:/app/scripts
  docker exec -it ai-shopkeeper python /app/scripts/seed_demo_data.py
  ```
- **AI Key 两种填法**：`.env` 里 `DEEPSEEK_API_KEY=`（改完重跑 `bash deploy/up.sh`），或在网页端「设置」页填写（写进挂载卷，重启不丢）。
- 演示前建议把 `.env` 的 `SHOP_API_PROFILE` 改成 `full` 再重跑一次（对应「设置 → API 能力档」的完整档）。

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
3. **反代改写 `X-Forwarded-For`**：服务端按该头做限流，`Caddyfile` 已强制覆盖，别删。
4. **只跑 1 个副本**：SQLite 单写 + 限流是进程内内存实现（`server/ratelimit.py`），横向扩容会数据分裂、限流失效。要扩容先按演进文档迁 Postgres + Redis。
5. **密钥与账本不入库**：`.env`、`server/config.local.json`、`server/data/` 均在 `.gitignore`；提交前用 `git check-ignore -v <path>` 复核。
6. **令牌轮换**：改 `.env` 的 `SHOP_ACCESS_TOKEN` → 重跑 `up.sh` → 各端在「设置」页填新令牌。
7. **服务器只留必要端口**：`22` 建议改成密钥登录并限制来源 IP；不要安装来源不明的面板插件。

## 九、免备案分支（Cloudflare Tunnel）

域名没备案、又不想等，或不想对公网开放 80/443 时：

```bash
# 服务器上（临时演示用，重启即换地址）
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
cloudflared tunnel --url http://localhost:8000
```

得到 `https://xxxx.trycloudflare.com`，HTTPS 直接可用。要固定域名就把域名托管到 Cloudflare，建**命名隧道**并加一条 DNS 路由，效果等同自有域名 HTTPS。

限制：小程序**正式版**的 request 合法域名仍需备案域名，隧道地址只能在开发者工具（勾选「不校验合法域名」）或网页端使用。

## 十、排障

| 现象 | 原因与处理 |
|---|---|
| 浏览器打不开 | 防火墙没放通 80/443；DNS 未解析；`docker compose ... logs caddy` 看证书申请失败原因 |
| 内地节点域名 80/443 被拦 | 域名未备案：先走「免备案分支」，或完成备案后再切 |
| `https` 证书申请失败 | 域名没解析到本机 / 80 端口被宝塔、nginx 占用（`ss -ltnp \| grep :80`）|
| 打开是 502 | 应用没起来：`logs shopkeeper`；`docker inspect -f '{{.State.Health.Status}}' ai-shopkeeper` |
| 页面能开，接口 401 | 正常：去「设置」页填令牌 |
| 流水/复盘日期差一天 | 容器时区不是 +0800：`docker exec ai-shopkeeper date +%z` 应为 `+0800`；不是就说明镜像缺 `tzdata`（[Dockerfile](../Dockerfile) 已装，旧镜像需重建）|
| 构建卡在 pip | `.env` 里 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| `docker pull` 超时 | 检查 `/etc/docker/daemon.json` 的 `registry-mirrors`（脚本已配腾讯云内网镜像）|
| 语音按钮不可用 | 纯 HTTP 部署：语音需要 HTTPS（配域名或走隧道）|
| 小程序连不上 | 后端地址要填 `https://域名`；正式版需在公众平台配置合法域名（须备案） |

## 十一、其它部署形态

- **不想买服务器**：本机 + Cloudflare Tunnel（`cloudflared tunnel --url http://localhost:8000`），5 分钟出公网地址，适合临时演示。
- **不想运维**：Railway / Render / Fly.io / Zeabur 直接部署本仓库 Dockerfile，**注意必须挂持久卷**（SQLite 账本是文件），且只能 1 个实例；免费档无持久卷，重启即丢数据。
- **国内合规托管**：微信云托管（小程序可用 `callContainer` 免备案域名调用），但本地磁盘不持久，需要改造存储层。
