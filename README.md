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

[![CI](https://github.com/1095278645/xirangceshi/actions/workflows/ci.yml/badge.svg)](https://github.com/1095278645/xirangceshi/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-575%20passed-brightgreen.svg)](#测试与验证)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](#快速开始)
[![License](https://img.shields.io/badge/license-MulanPSL--2.0-blue.svg)](LICENSE)

面向小微实体店/摊主的「人情味」熟客维系与经营减负助手。

摊主手上沾着油/水，打不了字——那就**按住说话**。AI 帮他记账、记住熟客、写朋友圈文案。

## 功能

| 模块 | 说明 |
|------|------|
| 语音记账 | 按住说话「王阿姨买了两个肉包一杯豆浆，6块」，AI 自动解析顾客/商品/金额，熟客自动归档。**没听出金额时不落库，当场追问「这笔多少钱」**；记完摆出「我这么记的，对吗？」可当场改金额/收支方向（留痕可查）；**一句话里说了好几笔会逐笔记账**（"今天收入1250，支出320" → 两条独立流水，各自生成凭证，绝不加总成一条） |
| 熟客记忆 | 记住熟客的暖心细节（"孙子考了一百分"），一键生成今日提醒，帮店主续上人情 |
| 朋友圈文案 | 口语化、带烟火气的文案，拒绝网红词，保留小店的粗粝与真诚 |
| 账本（省账通） | 查流水、算税费（增值税/个税/企税/报税日历）、66 科目表、一键导出 Excel 报表；大额记账自动预警 |
| 单店模型 | 保本线先行：填日销/成本/投资，算出保本日销、目标日销、回本周期、现金流可扛月数，三维交叉验证（经营现金流/投资回本/商圈客流）给出健康/临界/危险结论与整改建议；覆盖餐饮/饮品/零售/生鲜/服务/摆摊六业态；支持「从账本流水带入」一键反推实际日销与毛利率 |
| 收款流水自动入账 | 二维码收付款流水自动同步进账本：微信支付商户号（有执照）+ 聚合支付（无执照，接入中）双通道；无商户资料可填 DEMO 体验演示模式 |
| 收款即入账 | 店主点「收款」生成收款链接 → 顾客扫码确认 → 店主确认到账**自动入账并生成凭证**，同时给出到账播报文本；顾客填的称呼自动建熟客档案（资金仍走店主自己的收款码，不做资金通道） |
| 交易更正 | 记错了能改：**编辑**（旧凭证作废+生成新凭证）、**作废**（冲销凭证、不计入合计但保留可查）、**退货冲销**（生成负数关联记录自动抵消，解锁退货场景）；全部留痕可追溯 |
| 数据备份/恢复 | 启动与每 6 小时自动做**一致性快照**（`VACUUM INTO`），滚动保留；一键导出 zip（默认不含 API Key）、一键恢复（恢复前自动留存当前数据，恢复错了能退回） |
| 主动触达 | 每日复盘 / 熟客提醒 / 收款到账 / 流水低于保本线预警，主动推送到店主手机。**企业微信群机器人无需正式 AppID 即可真实收到消息**；另有本地记录通道供演示 |
| 资金健康 | 现金流滚动预测（未来几个月钱够不够花）、月度预算 vs 实际、赊账（应收应付）与账龄提醒 |
| 库存进销存 | 商品/材料档案、入库/出库/盘点、货值与补货/过期预警 |
| 发票台账 | 销项/进项发票登记、税率与税额、作废留痕、按类型汇总 |
| 会计闭环 | **科目余额表**（含借贷平衡自检）、**利润表**、**资产负债表**（资产 = 负债 + 权益）、**期末结转**（损益 → 本年利润，支持反结转与更正后重算）、科目期初余额录入 |
| 多店 / 多用户 | **一店一独立账本**（数据物理隔离），店主可开第二家店并一键切换；账号分**店主/店长/店员**三种角色与权限；每个成员一个访问令牌，登录后只能看到自己所属的店 |
| 掌柜每日复盘 | 五位伙计**按经营维度各管一摊**（钱账/熟客/库存/票税/异常）并行发言 → 掌柜裁决，只挑今天最该动手的一两件；掌柜的视野是**全店快照**，界面可展开原始事实查证 |

> **移动端覆盖**：微信小程序的「账本」页有 7 个标签页 ——
> 流水 / 算税 / 科目 / 报表 / **现金（资金健康）** / **库存** / **发票**；
> 「更多」页（原设置页）新增三个入口：
> **管理台**（会计报表 / 数据备份 / 主动触达）、**收款**（现场收款码 + 一键入账）、
> **多店 / 成员**（开新店、切店、分配角色与令牌）。上面这些能力在手机上都能用。
>
> 流水列表里**点任意一笔**即可「修改金额 / 作废 / 退货冲销」，改动全部留痕。

## 多 agent 团队编排

朋友圈文案、单店经营诊断、**每日复盘**这三项能力，由一组「AI 员工」并行竞争产出 → 掌柜融合裁决 → 采纳归因沉淀（Self-Built / Self-Run / Self-Grown）。

- 朋友圈文案：创意文案师 / 熟客运营 并行出稿 → 合规审核挑毛病 → 掌柜融合成一条可直接发的正文。
- 单店诊断：财务 / 经营 / 风控 三个顾问从不同视角竞争 → 掌柜取舍融合成一段诊断与整改动作，并沉淀「上次采纳了谁」用于后续参考。
- **每日复盘**：五位伙计**按经营维度各管一摊**（账房先生管钱账 / 熟客管家管人 /
  采买师傅管货 / 税务管事管票税 / 经营监察管异常）→ 掌柜裁决，只挑今天最该动手的
  一两件。掌柜拿到的是**全店经营快照**（账目 + 熟客 + 库存 + 赊账预算 + 发票 + 报税 +
  账目更正），所以他"知道全店在发生什么"，而不是只看收支。

无 API Key 时走规则化的「团队过程」，业务文本与降级路径完全不变；配置 Key 后自动启用真实多 agent 流水线。

### 掌柜复盘长什么样（一次真实产出）

快照只给事实，结论靠员工和掌柜推：

| 岗位 | 从自己那一摊看到 |
|---|---|
| 账房先生 | 今日毛利率约 57%，比月均 62% 低 5 个点；进货占收入 38%，偏高 |
| 熟客管家 | 陈伯 / 孙奶奶都 14 天没来，一个来过 8 次一个 6 次，该回访 |
| 采买师傅 | 本月进货 8,025 元，可库存档案没建 —— 货值、见底、临期全是糊涂账 |
| 税务管事 | 8,025 元进货一张进项票都没登记，钱出去了票没回来 |
| 经营监察 | 账没被改过、没有逾期、赊账空着 —— 这块今天干净 |

掌柜裁决后给店主的话：

> 今天先盯一个数：本月进货 8025 元，货和账对不上，这是最急的。动作就一件 ——
> 让收银小李今天把本月进货单一张一张补录进底账……补录时顺手把供货商那 8025 元的
> 进项票在微信上催回来。开门前再花两分钟给陈伯打个电话……

注意几个设计取向：**五位伙计职责不重叠**（否则候选趋同，"竞争"白设）；
**没数据也是结论**（"库存没建档案"本身就是要提的动作）；
**掌柜必须做取舍**（只留一两件，其余一句话带过或直接不提）。
复盘界面可展开「掌柜看到的原始事实」，让结论可查证。

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
│   ├── pages/books/    # 账本（流水/算税/科目/报表/现金流/库存/发票，7 个标签页）
│   ├── pages/store/    # 单店模型（保本线/现金流/三维诊断）
│   ├── pages/settings/ # 更多（后端地址/令牌/AI 模型/收款账户 + 管理台·收款·多店入口）
│   ├── pages/manage/   # 管理台（会计报表 / 数据备份恢复 / 主动触达）
│   ├── pages/collect/  # 收款（现场收款码 + 一键入账 + 到账播报）
│   ├── pages/shops/    # 多店 / 成员（开店、切店、角色与令牌）
│   ├── utils/api.js    # 请求封装（注入后端地址/访问令牌/当前店铺 + 失败提示）
│   └── components/     # 底部导航
├── scripts/
│   ├── mp_demo_check.py # 小程序演示前自检（接口契约/模板绑定/演示配置）
│   ├── seed_demo_data.py# 生成演示用经营流水（单店模型反推依赖它）
│   └── prewarm_cache.py # 预热 AI 缓存（洞察/报税建议）
└── server/             # Python FastAPI 后端
    ├── main.py         # 应用入口（组装路由/生命周期/静态挂载）
    ├── auth.py         # 访问令牌鉴权（可选启用，只守 /api/**；含店铺上下文解析）
    ├── shops.py        # 多店 / 多用户（店铺与用户注册表、角色权限、店上下文）
    ├── safe_io.py      # 受保护路径白名单 + 原子写入
    ├── qr.py           # 收款码生成（纯标准库 QR 编码器 + SVG 输出）
    ├── schemas.py      # API 请求模型（Pydantic）
    ├── routers/        # 业务路由（按域拆分，registry 注册表统一挂载）
    │   ├── registry.py  # 业务域注册表（增删能力唯一入口，main.py 遍历挂载，仿 TEAM_DOMAINS）
    │   ├── arch.py     # 领域上下文 / 任务队列 / 单店档案 / 心跳复盘
    │   ├── basic.py    # 健康检查 / AI 设置 / 文案生成
    │   ├── orders.py   # 记账 / 流水 / 凭证 / 交易更正（编辑·作废·退货）
    │   ├── customers.py# 熟客 / 记忆 / 提醒
    │   ├── tax.py      # 税法计算 / 科目表
    │   ├── store.py    # 单店经营模型
    │   ├── report.py   # Excel 报表导出
    │   ├── payment.py  # 收款账户 / 账单同步
    │   ├── finance.py  # 资金健康（现金流/预算/应收应付）
    │   ├── stock.py    # 库存进销存
    │   ├── invoice.py  # 发票台账
    │   ├── accounting.py # 会计闭环（三表 + 期末结转）
    │   ├── backup.py   # 数据备份 / 导出 / 恢复
    │   ├── collect.py  # 收款即入账（收款请求 / 公开收款页 / 收款码 / 确认入账）
    │   ├── notify.py   # 主动触达（消息订阅 / 推送测试 / 投递记录）
    │   └── shops.py    # 多店 / 多用户管理接口
    ├── ai.py           # AI 单 agent 能力（记账解析 / 洞察 / 画像 / 报税，无 Key 兜底）
    ├── team.py         # 多 agent 引擎原语（并行竞争扇出 / 采纳归因成长）
    ├── team_domains.py # 多 agent 业务编排骨架 + 域注册表 TEAM_DOMAINS
    ├── team_domain_copy.py   # 文案域（创意/熟客 → 合规评审 → 掌柜融合）
    ├── team_domain_store.py  # 单店诊断域（财务/经营/风控 → 掌柜裁决）
    ├── team_domain_review.py # 复盘域（账房/熟客/采买/税务/监察 → 掌柜裁决）
    ├── shop_snapshot.py# 全店经营快照（账目/熟客/库存/赊账/发票/报税/更正）
    ├── db.py           # SQLite（连接/建表/迁移；按域 re-export 查询能力；跟随店上下文）
    ├── db_corrections.py# 交易更正：编辑 / 作废 / 退货冲销（含审计留痕）
    ├── db_collections.py# 收款请求（收款即入账）
    ├── backup.py       # 备份：VACUUM INTO 一致性快照 / 导出包 / 恢复校验（按店分目录）
    ├── notifications.py# 主动触达：多通道推送（企业微信机器人 / 本地记录 / 订阅消息）
    ├── accounting.py   # 会计闭环：科目余额表 / 利润表 / 资产负债表 / 期末结转
    ├── categories.py   # 66 科目表（资产/负债/权益/收入/费用）
    ├── tax.py          # 税法计算（增值税/附加税/个税/企税/报税日历/边界护栏）
    ├── report.py       # Excel 报表导出（openpyxl，三工作表）
    ├── store.py        # 单店经营模型（保本线/目标日销/回本/现金流/三维诊断，六业态预设）
    ├── payment.py      # 收款流水同步统一入口（微信/聚合/演示模式）
    ├── wechat_pay.py   # 微信支付 v3 交易账单同步（真实对接 + DEMO 演示）
    ├── aggregate_pay.py# 聚合支付适配器（预留收钱吧/付桥等服务商）
    ├── config.py       # 配置读取（环境变量 > config.local.json > 默认值）
    ├── scripts/        # 自检脚本（接口一致性 / 小程序页面 / 网页端页面 / 渲染试跑）
    ├── tests/          # pytest 测试（517 项）
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
            │   ├── finance.js # 资金健康（现金流/预算/赊账）
            │   ├── stock.js   # 库存进销存
            │   ├── invoice.js # 发票台账
            │   ├── accounting.js # 会计报表（三表 + 期末结转）
            │   ├── backup.js  # 数据备份 / 导出 / 恢复
            │   ├── notify.js  # 主动触达（推送通道 / 订阅 / 记录）
            │   ├── collect.js # 收款（收款码 / 一键入账）
            │   ├── shops.js   # 多店 / 成员
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

> 也可以 **Docker 一键起后端**（推荐评审/复现环境）：`docker compose up -d --build`，
> 详见文末「[Docker 一键部署](#docker-一键部署)」。

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

后端启动后，手机与电脑连同一 WiFi，浏览器打开 `http://电脑局域网IP:8000` 即可使用全部功能（记账/熟客/文案/账本/单店）。电脑本地直接访问 `http://127.0.0.1:8000`。

网页端底部「更多」里还有：**收款**（现场生成收款码 · 一键入账）、**会计报表**（利润表 /
资产负债表 / 科目余额表 / 期末结转）、**数据备份**（备份 · 导出 · 恢复）、
**主动触达**（推送复盘与提醒）、**多店 / 成员**（开店 · 切店 · 角色令牌）、
以及财务（现金流/预算/赊账）、库存、发票、设置。

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

**1. 单元/集成测试（517 项，随时可跑，不联网）**

```bash
cd server
python -m pytest -q
```

其中两个文件值得单独说：

- `tests/test_demo_flow.py` 是**端到端演示动线测试**：按演示顺序把每个页面
  交互打一遍真实 HTTP 并核对关键数值（金额识别、分类→科目映射、借贷凭证、
  四税计算、从账本反推、报表导出…）。AI 调用被打桩，所以不消耗额度。
- `tests/test_gaps_http.py` 是**七项能力的接口冒烟测试**：备份/更正/收款/触达/
  移动端/会计/多店，每项都按"用户点得到的那条路"真打一遍接口。
  真实缺陷恰好都藏在这一层 —— 路径写错、漏传 `confirm=true`、字段名对不上
  （`provider` vs `channel`、`stock` vs `stock_qty`），单测全绿但功能不可用。
  里面还有一条**防泄漏守卫**：确认测试绝不读写真实的演示库/生产库。

**2. 演示环境自检（需要真实环境，会读数据库 / 调 AI）**

```bash
cd server
python ../scripts/mp_demo_check.py     # 接口契约 + 模板绑定 + 演示数据 + 演示配置
python scripts/check_mp_api.py         # 小程序调用的接口是否都真实存在
python scripts/check_mp_pages.py       # 页面注册 + bindtap 处理函数是否存在 + 文件编码
python scripts/check_web_pages.py      # 网页端内联 onclick 引用的函数是否存在 + 路由闭环
node   scripts/check_web_render.js     # 在 Node 里真跑一遍网页端所有渲染函数
python scripts/run_browser_check.py    # 用真实浏览器（Edge 无头）真点一遍网页端
python scripts/eval_ai_parse.py        # 「一句话记账」回归基线（真实模型，耗额度）
python ../scripts/prewarm_cache.py     # 预热 AI 缓存（需先起后端）
```

**`eval_ai_parse.py` 是唯一会验证核心承诺的脚本**：54+ 条真实店主原话（分
**金额 / 口语 / 多笔 / 边界**四类）去调真实模型，逐条核对金额/方向/分类/熟客。
其余测试全都是打桩的 —— 也就是说，"说一句话就能记好账"这件事只有它验过。

改提示词、换模型、调温度之后**都该跑一遍**，防止悄悄退化：

```bash
python scripts/eval_ai_parse.py --compare   # 与基线对比，确认退化则退出码 1
python scripts/eval_ai_parse.py --write-baseline   # 确认新表现可接受后更新基线
```

基线存在 `server/tests/ai_parse_baseline.json`。两个设计细节：
- **只判定"曾全对 → 现在有偏差"**，反向不算退化（基线比现在差无所谓）；
- **偏差会重试一次确认**。模型有真实随机性（实测同一句话两次可能给出不同分类），
  不重试的话每次都会随机报出不同的"退化"，工具很快就没人信了。
  重试后仍偏差才算退化；抖动会单独列出来。当前基线：55/55 全对。

### 测试绝不联网（`tests/conftest.py` 的闸门）

开发机配了真实 API Key，而 `ai_available()` 只看"有没有 Key"，**不看是不是在测试里** ——
于是任何没打桩的 AI 路径都会在测试中真的调用模型并扣费。实测踩过：心跳循环在
lifespan 里生成掌柜复盘（反复升级后变成 6 次模型调用），每起一个 `TestClient` 就跑一次，
整个套件从 21 秒变成 **127 秒**，而且真实扣费 —— 测试全绿，只是"变慢"。

现在 `tests/conftest.py` 自动生效两道闸门：
1. **真实 `ai.chat` 调用直接失败**，并点名是哪段文本触发的（自己打桩了
   `ai.chat` / `ai.ai_available` / `ai.get_client` 的测试会盖过闸门，不受影响）；
2. **`config._LOCAL_CONFIG` 指向临时文件**，测试写设置不会覆盖开发者的 `config.local.json`。

这道闸门第一次跑就抓出了 4 条**一直在偷偷真调模型**的用例。

前几个脚本防的是"编译不报错、点下去没反应"：小程序的 `bindtap="foo"`、
网页端的 `onclick="foo()"`，只要 `foo` 不存在都不会在加载时报错；
渲染函数里的模板串写错更是直接白屏。

**`run_browser_check.py` 是最强的一关**（需要本机装了 Edge/Chrome 和 node）：
它把演示库复制一份到临时目录、用副本起一个独立后端，然后真的开一个
无头浏览器把五个页面点一遍 —— 生成收款码、**另开一个标签当顾客扫码付款**、
一键入账、切会计期间、期末结转与反结转、备份与恢复、发测试推送、建店切店。
脚本结束时还会比对真实演示库的关键计数，确认它**没被碰过**。

## 主动触达（把复盘与提醒推到店主手机上）

原先所有能力都是**被动**的：店主必须自己想起来打开小程序，才能看到每日复盘与
熟客提醒 —— 不看就等于不存在。开启推送后，这些内容会主动发到店主手机。

### 推荐：企业微信群机器人（不需要正式 AppID）

1. 手机企业微信建一个只有自己的群（或与家人/合伙人的群）
2. 群设置 → 群机器人 → 添加 → 复制 Webhook 地址里的 **key**
3. 配置并立即测试：

```bash
curl -X POST "http://127.0.0.1:8000/api/notify/wecom-bot" \
  -H "Content-Type: application/json" \
  -d '{"key":"粘贴你的webhook-key"}'
```

返回 `"message":"已接通"` 就说明通道通了，之后每天会自动推送到群里。

### 可推送的四类消息

| 事件 | 触发时机 |
|---|---|
| 每日经营复盘 | 每天定时（当天收支 + 本月累计 + 单店一句话） |
| 熟客提醒 | 每天定时，或生成提醒后 |
| 收款到账 | 确认收款入账时即时播报 |
| 流水异常预警 | 每天定时：日流水低于保本线时提醒（"今天开门是亏的"） |

### 其它通道与资质要求

| 通道 | 能否直接落地 |
|---|---|
| 本地记录（`mock`） | ✅ 免配置，内容写到 `server/data/notifications.jsonl`，演示时用它展示"推送了什么" |
| 自定义 Webhook（`webhook`） | ✅ **开放 API**：把事件以 JSON POST 出去（`order_created` 等），填 `url\|密钥` 时附 HMAC-SHA256 签名供验签 |
| 企业微信群机器人（`wecom_bot`） | ✅ 填个 key 就能收到真消息 |
| 企业微信应用消息（`wecom_app`） | ⚠️ 需企业微信管理员建应用（corpid/secret/agentid） |
| 微信小程序订阅消息（`wechat_subscribe`） | ⚠️ 需正式 AppID、用户逐次授权订阅、并在公众平台申请模板；**游客模式不可用** |

> 投递结果全部入库可查：`GET /api/notify/logs`（含失败原因与重试次数）。
> 同一事件按业务键做了**幂等去重**，服务重启或重复触发不会刷屏。

## 多店 / 多用户（一家店一个账本）

### 设计取舍：一店一库

原先是**单店单用户**：一个库就是一家店，没有店/用户概念，店主开第二家店就废了。
加多租户有两条路：

- **加 `shop_id` 列** —— 要改所有查询与索引，改动面极大、极易漏；
- **一店一库**（本项目采用）—— SQLite 场景下跨表 JOIN 很少，每个店一个 db 文件
  天然隔离，而且**备份/迁移/删除都以"店"为单位**，改动面小得多。

兼容策略是关键：`db.DB_PATH` 仍然是运行期默认库路径（所有既有测试与演示都
依赖它），多店只在**设置了店上下文**时才生效：

```
get_conn() → shops.resolve_db_path() or db.DB_PATH
```

所以不设上下文 = 行为与单店完全一致；设置上下文 = 自动切到那家店的库。

### 角色与权限

| 角色 | 记账 | 看账 | 管账/更正 | 管成员 | 开店/删店 |
|---|:--:|:--:|:--:|:--:|:--:|
| 店主 owner | ✅ | ✅ | ✅ | ✅ | ✅ |
| 店长 admin | ✅ | ✅ | ✅ | ✅ | ❌ |
| 店员 staff | ✅ | ✅ | ✅ | ❌ | ❌ |

- 每个成员一个**访问令牌**，登录后只能看到自己所属的店；越权访问返回 403 并说明原因，
  不会静默落到默认店（否则等于数据泄露）。
- 未配置 `SHOP_ACCESS_TOKEN` 时是**本地单店模式**，全部放行 —— 演示/开发零配置。
- 切店：请求头 `X-Shop-Id: <店铺id>`（小程序切店后自动带上）。

### 演示动线（多店隔离开箱可验）

1. 「更多」→「多店 / 成员」→ 新建「二号店」（自动初始化标准账套，不含一号店流水）
2. 切到二号店 → 去「记账」说一句 → 回「账本」看流水（只有这一笔）
3. 切回一号店 → 流水恢复原样（两家数据物理隔离）
4. 「+ 加成员」建一个店员 → 复制令牌 → 让 ta 在「设置」页填这个令牌登录 →
   只能看到自己店，且管理接口返回 403

> 收款码也做了店铺归属：公开收款页（`/pay/<token>`）靠收款码 token **反查店铺**，
> 所以顾客扫二号店的码，打开的是二号店的数据 —— 顾客手机上没有任何令牌。

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
| `POST /api/insights` | **统一 AI 洞察入口**（`scene`：copy / monthly / tax / customer / store；同日缓存 + 失败本地兜底） |
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
| `GET /api/transactions/{id}` | 单笔交易详情（含更正历史） |
| `POST /api/transactions/{id}` | 更正交易（编辑金额/分类/事由等，留痕） |
| `POST /api/transactions/{id}/void` | 作废交易（冲销凭证，不计入合计） |
| `POST /api/transactions/{id}/refund` | 退货冲销（支持全额/部分退） |
| `GET /api/audits` | 最近的更正历史 |
| `GET /api/backup/list` | 备份列表 |
| `POST /api/backup/create` | 手动创建备份 |
| `POST /api/backup/export` | 导出备份包（zip，默认不含 API Key） |
| `POST /api/backup/import` | 上传备份包恢复（裸 body 传 zip/.db，需 `confirm=true`） |
| `POST /api/backup/restore/{name}` | 用已有备份恢复（需 `confirm=true`） |
| `POST /api/collect/create` | 创建收款请求（返回收款链接/二维码） |
| `GET /api/collect/list` | 收款请求列表（看"待确认"） |
| `POST /api/collect/{id}/confirm` | 确认到账 → 自动入账 + 生成凭证 |
| `GET /pay/{token}` | **公开**收款页（顾客扫码打开，免鉴权） |
| `POST /api/pay/{token}/paid` | **公开**：顾客点「我已付款」 |
| `GET /api/notify/events` | 可订阅的事件类型 |
| `GET /api/notify/providers` | 可用推送通道及配置要求 |
| `POST /api/notify/wecom-bot` | 一步接通企业微信群机器人（并立即发测试消息） |
| `POST /api/notify/subscriptions` | 新增/更新订阅 |
| `GET /api/notify/logs` | 投递记录（成功/失败/重试次数） |
| `GET /api/notify/mock-inbox` | 本地记录通道收到的消息 |
| `GET /api/heartbeat` | 掌柜今日复盘（含全店快照） |
| `POST /api/heartbeat` | 让掌柜立刻复盘一次（多 agent，约 5~10 秒） |
| `GET /api/heartbeat/snapshot` | 掌柜看到的全店原始事实（按经营维度分块，含未记账项） |
| `GET /api/accounting/trial-balance` | 科目余额表（含借贷平衡自检） |
| `GET /api/accounting/income-statement` | 利润表 |
| `GET /api/accounting/balance-sheet` | 资产负债表 |
| `POST /api/accounting/close` | 期末结转（损益 → 本年利润；可重复执行，会先红冲再重算） |
| `POST /api/accounting/reopen` | 反结转（解除期间锁定） |
| `GET /api/accounting/opening-balances` | 科目期初余额列表 |
| `POST /api/accounting/opening-balances` | 设置科目期初余额（把历史账套接进来） |
| `GET /api/store/presets` | 单店模型业态预设（六业态参考毛利率区间与经营提示） |
| `POST /api/store/model` | 单店模型计算（保本线/目标日销/回本周期/现金流/三维诊断与建议） |
| `GET /api/store/from-ledger` | 从账本流水反推实际日销/毛利率（自动定位最近有收入的月份） |
| `GET /api/store/benchmark` | 同业基准（**示例/仿真值**）：本店指标 vs 同业态参考区间 |
| `GET /api/orders/{id}/explain` | 结论可解释：这笔账凭什么这么记（含复式分录） |
| `GET /api/metrics/ai` | 运行指标：AI 调用量/成功率、token、延迟 P50/P95、**估算成本与每单成本** |
| `GET /api/metrics/ai/capability` | AI 能力基线：感知/记忆/决策/行动/反馈闭环、质量门禁、用户反馈率 |
| `GET /api/metrics/ai/calls` | 最近的原始 AI 调用记录（排查慢调用/失败调用） |
| `GET /api/payment/sources` | 收款账户列表（微信商户/聚合支付） |
| `POST /api/payment/sources` | 新增/更新收款账户（mchid 填 DEMO 即演示模式） |
| `DELETE /api/payment/sources/{id}` | 删除收款账户 |
| `POST /api/payment/sources/{id}/sync` | 手动同步某账户账单（默认昨天） |
| `GET /api/payment/logs` | 账单同步日志 |
| `POST /api/payment/demo-clear` | 一键清空演示模式流水 |
| `POST /api/payment/sync-all` | 手动触发全部启用账户同步 |
| `GET /api/backup/list` | 备份列表（名称/大小/类型/时间） |
| `POST /api/backup/create` | 立即手动备份（一致性快照） |
| `GET /api/backup/download/{name}` | 下载某份备份（异地保存用） |
| `POST /api/backup/export` | 生成全量数据包（zip，默认不含 API Key） |
| `POST /api/backup/import` | 用上传的 zip/.db 恢复（**需 `confirm=true`**） |
| `POST /api/backup/restore/{name}` | 用已有备份恢复（**需 `confirm=true`**；恢复前自动留快照） |
| `POST /api/transactions/{id}` | 更正交易（改金额/品名等，写审计） |
| `POST /api/transactions/{id}/void` | 作废交易（红冲凭证，不计入合计但保留留痕） |
| `POST /api/transactions/{id}/refund` | 退货冲销（生成负数关联记录自动抵消） |
| `GET /api/audits` | 更正/作废/退货的审计记录 |
| `POST /api/collect/{token}/qr.svg` | 生成收款码（SVG，可用 `?origin=` 指定顾客可达地址） |
| `GET /api/shops` | 店铺列表 |
| `POST /api/shops` | 新建店铺（`copy_from` 可沿用现有店的科目结构，不带流水） |
| `PUT /api/shops/{id}` | 改名/归档 |
| `DELETE /api/shops/{id}` | 删除店铺（默认保留数据文件；`purge=true` 才真删） |
| `GET /api/shops/context` | 当前请求落在哪家店（前端显示店名用） |
| `POST /api/shops/switch` | 切换当前店铺（写入用户偏好） |
| `GET /api/shops/users` | 成员账号列表（令牌只回传前缀） |
| `POST /api/shops/users` | 新增成员（返回**一次性**令牌） |
| `POST /api/shops/{id}/members` | 把账号加入某家店/改店内角色 |
| `DELETE /api/shops/{id}/members/{uid}` | 把账号移出某家店 |

> 收款流水同步说明：后端启动后每 6 小时自动拉取所有启用账户的昨日账单（wx_trade_id 唯一索引幂等去重）；演示模式（mchid=DEMO）无需任何商户资料即可体验全流程，数据带 `[演示]` 标记可一键清空。正式对接微信支付商户号需配置 API 证书与 APIv3 密钥（`pip install wechatpayv3`）。

> 鉴权说明：以上接口除 `/api/health` 与公开收款页（`/api/pay/*`、`/pay/*`）外，
> 在后端设置 `SHOP_ACCESS_TOKEN` 后均需携带访问令牌（见「1.1 开启访问令牌鉴权」）。
> 携带**成员令牌**时还会按店铺成员关系做二次校验（越权返回 403）。

> 安全审计记录见 [`docs/security-audit-2026-09.md`](docs/security-audit-2026-09.md)。

> 演示/讲解脚本与现场排障见 [`docs/demo-guide.md`](docs/demo-guide.md)。

> 部署、云化演进（Litestream / Postgres / 连锁）与开放 API 见 [`docs/deployment-evolution.md`](docs/deployment-evolution.md)。

> 免鉴权接口（收款页、语音上传）已内置**滑动窗口限流**（`SHOP_RATE_LIMIT=0` 可关）；
> H5 支持 **PWA**（添加到主屏幕、离线兜底）与**无障碍**（右下角字号缩放 / 语音朗读）。

## Docker 一键部署

```bash
# 构建并启动（默认 http://127.0.0.1:8000）
docker compose up -d --build

# 配 AI Key（任选其一）
#   a) 环境变量：DEEPSEEK_API_KEY=sk-xxx docker compose up -d
#   b) 打开网页版「设置」页填写（写入挂载卷，重启不丢）

# 数据持久化在 ./server/data（SQLite 库与备份快照）
docker compose down
```

> 镜像基于 `server/requirements.lock` 安装依赖（确定性、可复现）；
> `.dockerignore` 已排除 `server/data`、`server/config.local.json`，**密钥与账本不会进镜像**。

## 依赖与可复现构建

| 文件 | 用途 |
|---|---|
| `server/requirements.txt` | 直接依赖（宽松下限，跟随上游小版本） |
| `server/requirements.lock` | **确定性锁**（含传递依赖）。CI / 生产：`pip install -r requirements.lock` |
| `server/requirements-dev.txt` | 开发/测试依赖（`pytest` 等） |
| `server/pyproject.toml` | `pytest` / `coverage` 配置与依赖生成说明 |

## 持续集成

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) 在 push / PR 时执行：

1. **517 项**单元/集成测试（`pytest -q`，全程不联网，真实 `ai.chat` 被 `tests/conftest.py` 闸门拦截）
2. 灌演示数据 + 演示前自检（接口契约 / 页面一致性 / 演示数据 / 演示配置）
3. 小程序接口契约、小程序页面静态检查、网页端页面静态检查
4. Node 中试跑网页端全部渲染函数
5. Docker 镜像构建

## 配置与密钥

复制 `server/config.example.json` 为 `server/config.local.json` 后填写 Key（该文件已被 `.gitignore` 排除，**不会提交**）。
也可用环境变量 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`。

> 不要把任何真实 Key 写进代码或提交；如曾泄露，请到模型平台**重置**。

## 许可证

本项目采用 **木兰宽松许可证，第 2 版（Mulan PSL v2）**，全文见 [LICENSE](LICENSE)。

> AI生成
