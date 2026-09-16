---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'da29adc5-82ff-416f-a9d4-3ddca3c05658'
  PropagateID: 'da29adc5-82ff-416f-a9d4-3ddca3c05658'
  ReservedCode1: '13c0d36d-2f2a-4e4b-b508-a63f7a526370'
  ReservedCode2: '13c0d36d-2f2a-4e4b-b508-a63f7a526370'
---

# 巷子里的AI掌柜

面向小微实体店/摊主的「人情味」熟客维系与经营减负助手。

摊主手上沾着油/水，打不了字——那就**按住说话**。AI 帮他记账、记住熟客、写朋友圈文案。

## 功能

| 模块 | 说明 |
|------|------|
| 语音记账 | 按住说话「王阿姨买了两个肉包一杯豆浆，6块」，AI 自动解析顾客/商品/金额，熟客自动归档 |
| 熟客记忆 | 记住熟客的暖心细节（"孙子考了一百分"），一键生成今日提醒，帮店主续上人情 |
| 朋友圈文案 | 口语化、带烟火气的文案，拒绝网红词，保留小店的粗粝与真诚 |
| 账本（省账通） | 查流水、算税费（增值税/个税/企税/报税日历）、66 科目表、一键导出 Excel 报表；大额记账自动预警 |
| 单店模型 | 保本线先行：填日销/成本/投资，算出保本日销、目标日销、回本周期、现金流可扛月数，三维交叉验证（经营现金流/投资回本/商圈客流）给出健康/临界/危险结论与整改建议；覆盖餐饮/饮品/零售/生鲜/服务/摆摊六业态；支持「从账本流水带入」一键反推实际日销与毛利率 |
| 收款流水自动入账 | 二维码收付款流水自动同步进账本：微信支付商户号（有执照）+ 聚合支付（无执照，接入中）双通道；无商户资料可填 DEMO 体验演示模式 |

## 多 agent 团队编排

朋友圈文案、单店经营诊断这两项能力，由一组「AI 员工」并行竞争产出 → 掌柜融合裁决 → 采纳归因沉淀（Self-Built / Self-Run / Self-Grown）。

- 朋友圈文案：创意文案师 / 熟客运营 并行出稿 → 合规审核挑毛病 → 掌柜融合成一条可直接发的正文。
- 单店诊断：财务 / 经营 / 风控 三个顾问从不同视角竞争 → 掌柜取舍融合成一段诊断与整改动作，并沉淀「上次采纳了谁」用于后续参考。

无 API Key 时走规则化的「团队过程」，业务文本与降级路径完全不变；配置 Key 后自动启用真实多 agent 流水线。

### 扩展指引（增减能力不破坏架构）

增删能力只需改**声明**，引擎、融合裁决、采纳沉淀全部自动适配，流程代码零改动：

1. **增/删员工**：只改 `TEAM_DOMAINS` 对应域的 `employees` 列表（加一行或删一行）即可。
2. **新增业务域（三步）**：
   1. 定义员工列表（3 名左右，各给 `role / system / temperature / max_tokens`）；
   2. 在 `TEAM_DOMAINS` 登记（`mode` 协作或竞争、`judge` 一句话、可选 `reviewer` 协作评审、无 Key 降级函数写进 `degraded`）；
   3. 写一个薄壳入口函数（无 Key 降级 + 组装 task，一行调用通用流水线 `_run_team`）。
3. **删整个域**：删除注册表项 + 薄壳函数即可；`tests/test_team.py` 的注册表自检会自动确认所有已注册域结构完整、降级可跑。

## 业务域（路由）注册表

非 AI 的业务路由同样采用「声明式注册表」：`routers/registry.py` 的 `BUSINESS_DOMAINS` 是后端全部路由的唯一登记处，`main.py` 只遍历注册表统一挂载，不关心具体有哪些域。

- **新增业务域**：新建 `routers/xxx.py`（暴露 `router`）+ 在 `BUSINESS_DOMAINS` 登记一行，`main.py` 与流程代码零改动。
- **停用业务域**：把该域 `enabled` 改为 `False` 即可临时下线（不删代码）。
- **删除业务域**：删掉注册表对应行即可（文件可留可删）。

`tests/test_routers_registry.py` 的注册表自检会确认所有条目结构完整、可挂载，且停用一个域不影响其余域。

## 目录结构

```
├── miniprogram/        # 微信小程序前端（微信开发者工具打开）
│   ├── pages/index/    # 语音记账（首页）
│   ├── pages/memory/   # 熟客记忆
│   ├── pages/copy/     # 朋友圈文案
│   ├── pages/books/    # 账本（流水/算税/科目/报表）
│   ├── pages/store/    # 单店模型（保本线/现金流/三维诊断）
│   ├── pages/settings/ # 设置（后端地址/访问令牌/AI 模型/收款账户）
│   ├── utils/api.js    # 请求封装（注入后端地址与访问令牌 + 失败提示）
│   └── components/     # 底部导航
├── scripts/
│   ├── mp_demo_check.py # 小程序演示前自检（接口契约/模板绑定/演示配置）
│   └── seed_demo_data.py# 生成演示用经营流水（单店模型反推依赖它）
└── server/             # Python FastAPI 后端
    ├── main.py         # 应用入口（组装路由/生命周期/静态挂载，约 80 行）
    ├── auth.py         # 访问令牌鉴权（可选启用，只守 /api/**）
    ├── safe_io.py      # 受保护路径白名单 + 原子写入
    ├── schemas.py      # API 请求模型（Pydantic）
    ├── routers/        # 业务路由（按域拆分，registry 注册表统一挂载）
    │   ├── registry.py  # 业务域注册表（增删能力唯一入口，main.py 遍历挂载，仿 TEAM_DOMAINS）
    │   ├── arch.py     # 领域上下文 / 任务队列 / 单店档案 / 心跳复盘
    │   ├── basic.py    # 健康检查 / AI 设置 / 文案生成
    │   ├── orders.py   # 记账 / 流水 / 凭证
    │   ├── customers.py# 熟客 / 记忆 / 提醒
    │   ├── tax.py      # 税法计算 / 科目表
    │   ├── store.py    # 单店经营模型
    │   ├── report.py   # Excel 报表导出
    │   └── payment.py  # 收款账户 / 账单同步
    ├── ai.py           # AI 单 agent 能力（记账解析 / 洞察 / 画像 / 报税，无 Key 兜底）
    ├── team.py         # 多 agent 引擎原语（并行竞争扇出 / 采纳归因成长）
    ├── team_domains.py # 多 agent 业务编排（朋友圈文案 / 单店诊断）+ 域注册表 TEAM_DOMAINS
    ├── db.py           # SQLite（熟客/记忆/交易/提醒/收款账户）
    ├── categories.py   # 66 科目表（资产/负债/权益/收入/费用）
    ├── tax.py          # 税法计算（增值税/附加税/个税/企税/报税日历/边界护栏）
    ├── report.py       # Excel 报表导出（openpyxl，三工作表）
    ├── store.py        # 单店经营模型（保本线/目标日销/回本/现金流/三维诊断，六业态预设）
    ├── payment.py      # 收款流水同步统一入口（微信/聚合/演示模式）
    ├── wechat_pay.py   # 微信支付 v3 交易账单同步（真实对接 + DEMO 演示）
    ├── aggregate_pay.py# 聚合支付适配器（预留收钱吧/付桥等服务商）
    ├── config.py       # 配置读取（环境变量 > config.local.json > 默认值）
    └── static/         # 网页版（手机浏览器可直接访问）
        ├── index.html   # 单页入口（按依赖顺序加载 js/）
        ├── style.css
        └── js/          # 前端逻辑（按职责拆分，全局函数兼容内联事件）
            ├── core.js        # 状态 / API 封装 / 渲染分发 / 路由 / 工具
            ├── speech.js      # 语音识别（Web Speech API）
            ├── pages/         # 各页面逻辑与渲染
            │   ├── home.js    # 记账（首页）
            │   ├── customers.js # 熟客
            │   ├── copy.js    # 朋友圈文案
            │   ├── books.js   # 账本（流水/算税/科目/报表）
            │   ├── store.js   # 单店模型
            │   └── settings.js# 设置（AI 模型 + 收款账户）
            └── init.js        # 初始化（hash 路由 / 导航绑定 / 首渲染）
```

## 快速开始

> **只是想跑起来用**：做第 1~3 步即可（起后端 → 配 Key → 打开网页版）。
>
> **要拿去做演示**：还需要第 4~7 步（小程序连局域网 → 自检 → 灌演示数据 → 预热缓存），
> 完整脚本、话术与现场排障见 [`docs/demo-guide.md`](docs/demo-guide.md)。

### 1. 启动后端

```bash
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 1.1 开启访问令牌鉴权（局域网/公网部署建议开启）

服务默认监听 `0.0.0.0`，若不设令牌，同一 WiFi 下任何设备都能读写全部数据。
设置环境变量 `SHOP_ACCESS_TOKEN` 即启用鉴权（**不设置则完全放行**，行为与以前一致）：

```bash
cd server
python -c "import auth; print(auth.generate_token())"   # 生成高强度令牌
```

```powershell
# Windows PowerShell
$env:SHOP_ACCESS_TOKEN='把生成的令牌粘到这里'
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
# Linux / macOS
export SHOP_ACCESS_TOKEN='把生成的令牌粘到这里'
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

启用后在网页版 / 小程序的「设置」页填写同一令牌即可（令牌只存本机，不会上传）。

鉴权规则：

- 只保护 `/api/**`；首页 `/` 与 `/static/**` 放行，否则浏览器加载不出填令牌的界面。
- `/api/health` 免鉴权，供探活与反代健康检查。
- 令牌通过请求头 `X-Shop-Token` 或 `Authorization: Bearer <token>` 提交；
  报表下载这类无法自定义头部的场景可用 `?token=<token>` 兜底。
- 鉴权失败返回 `401`，前端会引导到「设置」页填写令牌。

### 2. 配置 AI Key（可选）

在 `server/config.local.json` 中写入（该文件已被 .gitignore 排除，不会上传）：

```json
{ "api_key": "sk-xxxxxxxx", "base_url": "https://api.deepseek.com", "model": "deepseek-chat" }
```

也可以在网页版/小程序的「设置」页直接选择大模型（DeepSeek / OpenAI / 通义千问 / 智谱 GLM / Kimi / 自定义）并粘贴 API Key，保存后立即生效，无需重启后端。

不配置也能跑：记账金额可识别，文案/提醒返回兜底内容；配置后自动启用真实 AI。

### 3. 打开网页版（推荐）

后端启动后，手机与电脑连同一 WiFi，浏览器打开 `http://电脑局域网IP:8000` 即可使用全部功能（记账/熟客/文案/账本/设置）。电脑本地直接访问 `http://127.0.0.1:8000`。

### 4. 打开小程序

1. 微信开发者工具导入 `miniprogram/` 目录
2. 本地设置勾选「不校验合法域名」
3. **真机预览必须在「设置」页把「后端地址」改成电脑的局域网 IP**（如 `http://192.168.1.5:8000`），
   并点「测试连接」自检；保持手机与电脑同一网络。
   真机上 `127.0.0.1` 指向手机自己，用默认值会连不上后端（页面顶部会出现红色提示条）。

> 语音识别依赖微信官方「同声传译」插件（appid：wx069ba97219f66d99）。`app.json` 目前**未声明该插件**，
> 因此语音按钮会降级为手动输入并给出提示；如需语音演示，请在 `app.json` 添加 `plugins` 声明，
> 并使用正式 AppID（游客 appid `touristappid` 无法使用插件）。

### 5. 演示前自检

演示前跑一遍自检脚本，可在打开开发者工具之前发现接口不匹配、模板绑定错误、配置缺失等问题：

```bash
cd server
python ../scripts/mp_demo_check.py
```

检查内容（4 部分）：

1. **接口契约**：小程序调用的接口是否都存在于后端（以 OpenAPI schema 为权威契约）
2. **页面静态一致性**：WXML 绑定的事件方法/自定义组件是否都有定义
3. **演示数据是否就绪**：流水/熟客/记忆点/提醒是否达到演示下限、AI 缓存是否已预热
4. **演示配置**：插件声明 / appid / urlCheck / 默认后端地址

存在「必须修」的问题时以非 0 退出，便于接入 CI。**第 3 部分专门用来兜住
"忘了灌演示数据"** —— 否则现场打开熟客页才发现是空的。

> ⚠️ **换一台机器演示时，有两样东西不会跟着仓库走**（都在 `.gitignore` 里）：
> `server/config.local.json`（你的 AI Key）与 `server/data/ai_shopkeeper.db`（演示数据）。
> 克隆到新环境后**必须重新执行第 6、7 步**，否则账本/熟客页是空的、
> AI 功能会走兜底模式。完整演示准备清单见
> [`docs/demo-guide.md`](docs/demo-guide.md)。

### 6. 生成演示数据（演示单店模型前必做）

演示最多只录入几条数据，账本/熟客/提醒页都会是空的、没法展示。
本步骤灌入**当月 1 日至今**的完整经营数据：约 3,200 笔真实感流水
（按菜单价目表生成，客单价约 6.6 元）、10 位熟客（带标签/常点/记忆点/
消费记录）、3 条待办提醒。

**顺序很重要：先清空，再灌入。** 脚本有幂等保护（已有当月流水时会拒绝执行，
避免数据翻倍），需要重灌就删库或加 `--force`。

```bash
cd server
rm -f data/ai_shopkeeper.db
python ../scripts/seed_demo_data.py
```

脚本跑完会自己核验四件事，出现 ❌ 说明数据不能拿去演示：

```
  分类校验通过（4 个分类均在科目映射表中）
  收入 21,120 元 / 3223 笔  客单价 6.6 元  ✅ 餐饮合理区间
  反推：日销 1,320 元 / 营业 16 天 / 毛利率 62.0%
  单店模型：保本日销 860.2 / 月利润 8,552.0 / 判定 健康（81）
  月度现金流：收入 21,120 / 支出 17,081 / 结余 4,038
    ✅ 与模型月利润量级一致，洞察结论不会自相矛盾
  账本品类：['主营业务收入', '进货', '办公费', '租赁及物业费']
    ✅ 全部品类都有对应会计科目
  门店房租 6,000 元 -> 分类 租赁及物业费 ✅
  水电杂费 2,000 元 -> 分类 租赁及物业费 ✅
```

> **为什么是"当月 1 日至今"而不是"最近 N 天"**：房租/水电是**整月**固定成本，
> 只灌半个月会让固定开销压在半个月的现金里，经营洞察的现金视角会显得
> "白忙活"，与单店模型算出的月利润口径打架。

> **分类名必须用 `categories.py` 里登记的规范名**（如 房租/水电 → `租赁及物业费`，
> 工资 → `职工薪酬`）。直接写库不经过记账接口的 `is_known_category` 校验，
> 自造分类名会被自动凭证静默兜底到「办公费」—— 实测 6000 元房租被记成
> 「管理费用-办公费」，账本品类与凭证科目一起错。脚本已在写入前卡这一关。

### 7. 预热 AI 内容缓存（建议做，让演示秒出）
账本页的「经营洞察」与算税页的「报税建议」都会在页面操作时自动请求 AI。
它们按维度缓存（洞察按月、建议按销售额 1000 元一档），首次要几十秒、之后秒回。
建议演示前先预热：

```bash
# 需先启动后端（第 1 步）
cd server
python ../scripts/prewarm_cache.py
```

不预热也能用，只是现场第一次点这两处要等 5~65 秒。缓存不会随数据变化自动更新，
演示中若改了数据、想要新分析，点卡片上的「重新分析」/「重新生成」。

## 测试与验证

两道关口，用途不同：

**1. 单元/集成测试（286 项，随时可跑，不联网）**

```bash
cd server
python -m pytest -q
```

其中 `tests/test_demo_flow.py` 是**端到端演示动线测试**：按演示顺序把每个页面
交互打一遍真实 HTTP 并核对关键数值（金额识别、分类→科目映射、借贷凭证、
四税计算、从账本反推、报表导出…）。AI 调用被打桩，所以不消耗额度。
演示前跑它，能在打开开发者工具之前发现"接口字段没了""税额算错了"。

**2. 演示环境自检（需要真实环境，会读数据库 / 调 AI）**

```bash
cd server
python ../scripts/mp_demo_check.py     # 接口契约 + 模板绑定 + 演示数据 + 演示配置
python ../scripts/prewarm_cache.py     # 预热 AI 缓存（需先起后端）
```

## API 一览

| 接口 | 说明 |
|------|------|
| `POST /api/orders` | 一句话记账（AI 解析 + 熟客归档 + 借贷凭证 + 大额预警） |
| `GET /api/orders/today` | 今日收支汇总 |
| `GET /api/orders/monthly` | 月度收支汇总（含分类明细） |
| `GET /api/vouchers` | 记账凭证列表（借贷分录） |
| `GET /api/customers` | 熟客列表 |
| `GET /api/customers/{id}` | 熟客详情（记忆点 + 消费记录） |
| `POST /api/memories` | 添加记忆点 |
| `POST /api/copy` | 生成朋友圈文案 |
| `POST /api/reminders/generate` | 生成今日熟客提醒 |
| `GET /api/reminders` | 提醒列表 |
| `POST /api/reminders/{id}/done` | 完成提醒 |
| `GET /api/account-titles` | 66 科目表（按类别分组） |
| `GET /api/transactions` | 交易流水列表（按年月筛选） |
| `POST /api/tax/vat` | 增值税计算（小规模，30 万免征） |
| `POST /api/tax/surtax` | 附加税计算（六税两费减半） |
| `POST /api/tax/pit` | 个人所得税计算（7 级累进） |
| `POST /api/tax/cit` | 企业所得税计算（小微/一般） |
| `GET /api/tax/calendar` | 报税日历（月度申报提醒） |
| `GET /api/report/monthly` | 月度 Excel 报表（收支汇总/分类明细/交易流水） |
| `GET /api/store/presets` | 单店模型业态预设（六业态参考毛利率区间与经营提示） |
| `POST /api/store/model` | 单店模型计算（保本线/目标日销/回本周期/现金流/三维诊断与建议） |
| `GET /api/store/from-ledger` | 从账本流水反推实际日销/毛利率（自动定位最近有收入的月份） |
| `GET /api/payment/sources` | 收款账户列表（微信商户/聚合支付） |
| `POST /api/payment/sources` | 新增/更新收款账户（mchid 填 DEMO 即演示模式） |
| `DELETE /api/payment/sources/{id}` | 删除收款账户 |
| `POST /api/payment/sources/{id}/sync` | 手动同步某账户账单（默认昨天） |
| `GET /api/payment/logs` | 账单同步日志 |
| `POST /api/payment/demo-clear` | 一键清空演示模式流水 |
| `POST /api/payment/sync-all` | 手动触发全部启用账户同步 |

> 收款流水同步说明：后端启动后每 6 小时自动拉取所有启用账户的昨日账单（wx_trade_id 唯一索引幂等去重）；演示模式（mchid=DEMO）无需任何商户资料即可体验全流程，数据带 `[演示]` 标记可一键清空。正式对接微信支付商户号需配置 API 证书与 APIv3 密钥（`pip install wechatpayv3`）。

> 鉴权说明：以上接口除 `/api/health` 外，在后端设置 `SHOP_ACCESS_TOKEN` 后均需携带访问令牌（见「1.1 开启访问令牌鉴权」）。

> 安全审计记录见 [`docs/security-audit-2026-09.md`](docs/security-audit-2026-09.md)。

> 演示/讲解脚本与现场排障见 [`docs/demo-guide.md`](docs/demo-guide.md)。

> AI生成