# 部署与云化演进

本文回答评审/店主最常追问的两个问题：**"能不能部署到云上？"**与**"一家店能撑，那连锁/多店怎么办？"**

## 一、当前形态（单机，开箱即用）

- 后端 FastAPI + **SQLite**（WAL），一个店一个库文件（`server/data/ai_shopkeeper.db`）。
- 数据、密钥都在**店主自己的机器**上；AI Key 由店主自填。
- 一键起后端：`docker compose up -d --build`（见仓库根 `Dockerfile` / `docker-compose.yml`）。

这一形态的取舍：**隐私与零运维成本**优先，适合单店/单机；不适合多实例横向扩展。

## 二、演进路径（分三步，接口不变）

```
[单机 SQLite] --> [单机 + 实时异地备份] --> [Postgres 多实例] --> [连锁总部/平台]
    当前              第 1 步（半天）             第 2 步（数天）        第 3 步（产品化）
```

### 第 1 步：单机 + 实时异地备份（推荐先做，风险最低）

用 **Litestream** 把 SQLite 持续复制到对象存储（S3/OSS/COS），实现"本机挂了也能从云上恢复"。
样例配置见 [`ops/litestream.yml`](../ops/litestream.yml)。

```bash
# 本机安装 litestream 后：
litestream replicate -config ops/litestream.yml
# 恢复：litestream restore -o server/data/ai_shopkeeper.db s3://your-bucket/ai-shopkeeper
```

项目本身已有的一致性快照（`VACUUM INTO`）与导出/恢复接口与此互补：
快照用于**本地可回退**，Litestream 用于**异地容灾**。

### 第 2 步：Postgres 多实例

代码里数据访问是**按域拆分的模块化结构**（`db.py` + `db_*.py`），SQL 集中在这些模块里，
迁移到 Postgres 时的改动面可控。步骤建议：

1. 引入连接抽象（当前 `db.get_conn()` 已是唯一入口，天然适合替换实现）；
2. 把 `datetime('now','localtime')` 等 SQLite 方言改为参数化时间或兼容层；
3. 多店模型从"一店一库"切换为"一库一 `shop_id` 列 + 行级隔离"（当前 `shops.py` 已把
   店上下文解析集中在 `resolve_db_path()`，替换这一处即可）。

> 注意：**多租户从"一店一库"转"一库多店"是数据模型级变更**，需要迁移脚本与回归测试；
> 本项目在单机阶段刻意选择"一店一库"以换取隔离性与备份粒度，这是有意识的取舍。

### 第 3 步：连锁总部 / 平台

- 多店之上的**总部视图**：跨店汇总、同一业态基准对比（本项目已有 `benchmark.py` 的接口形态）。
- 开放 API：通过 **Webhook**（本项目已实现 `notifications` 的 `webhook` 通道）把
  记账/复盘的 JSON 事件推给 ERP、代账系统、自有看板。
- 计费与配额：结合 `GET /api/metrics/ai` 的**每单成本**数据做用量计费。

## 三、部署建议清单

| 场景 | 建议 |
|---|---|
| 单店自用 | Docker 起后端；开启 `SHOP_ACCESS_TOKEN`；用企业微信群机器人接收提醒 |
| 展会/演示 | 局域网直连，**不设令牌**更方便；或手机热点隔离网络 |
| 公网/多端 | 必须开启 `SHOP_ACCESS_TOKEN`；置于 HTTPS 反向代理（Caddy/Nginx）后；限流已内置 |
| 容灾 | 上面第 1 步：Litestream → 对象存储 |

## 四、可观测性与成本

- **AI 成本/性能**：`GET /api/metrics/ai?days=7`（调用量、成功率、P50/P95、估算成本、每单成本）。
  H5「更多 → 运行指标」有可视化看板。
- **推送/投递**：`GET /api/notify/logs`（成功/失败/重试）。
- **备份**：`GET /api/backup/list`。
