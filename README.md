---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '683a5a68-502f-4e59-8d97-bd8a9bcdda0a'
  PropagateID: '683a5a68-502f-4e59-8d97-bd8a9bcdda0a'
  ReservedCode1: 'dc6451cf-9e7f-4d47-b03c-f551f2671303'
  ReservedCode2: 'dc6451cf-9e7f-4d47-b03c-f551f2671303'
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

## 目录结构

```
├── miniprogram/        # 微信小程序前端（微信开发者工具打开）
│   ├── pages/index/    # 语音记账（首页）
│   ├── pages/memory/   # 熟客记忆
│   ├── pages/copy/     # 朋友圈文案
│   ├── pages/books/    # 账本（流水/算税/科目/报表）
│   ├── pages/settings/ # 设置（AI 模型配置）
│   └── components/     # 底部导航
└── server/             # Python FastAPI 后端
    ├── main.py         # 接口路由
    ├── ai.py           # AI 调用（记账解析 / 文案 / 提醒）
    ├── db.py           # SQLite（熟客/记忆/交易/提醒）
    ├── categories.py   # 66 科目表（资产/负债/权益/收入/费用）
    ├── tax.py          # 税法计算（增值税/附加税/个税/企税/报税日历/边界护栏）
    ├── report.py       # Excel 报表导出（openpyxl，三工作表）
    ├── config.py       # 配置读取
    └── static/         # 网页版（手机浏览器可直接访问）
        ├── index.html
        ├── style.css
        └── app.js
```

## 快速开始

### 1. 启动后端

```bash
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

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
3. 真机预览时把 `app.js` 里的 `baseUrl` 改成电脑局域网 IP，并保持手机与电脑同一网络

> 语音识别依赖微信官方「同声传译」插件（appid：wx069ba97219f66d99）。如提示插件未授权，需在小程序管理后台「设置-第三方设置-插件管理」中添加。

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

> AI生成