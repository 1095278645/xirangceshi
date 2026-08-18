---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '0f08ca47-a631-4a43-9f95-fc78f4dd507b'
  PropagateID: '0f08ca47-a631-4a43-9f95-fc78f4dd507b'
  ReservedCode1: '050dfeaa-2f16-431e-b2f4-9b6c2c5b2b7c'
  ReservedCode2: '050dfeaa-2f16-431e-b2f4-9b6c2c5b2b7c'
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

## 目录结构

```
├── miniprogram/        # 微信小程序前端（微信开发者工具打开）
│   ├── pages/index/    # 语音记账（首页）
│   ├── pages/memory/   # 熟客记忆
│   ├── pages/copy/     # 朋友圈文案
│   └── components/     # 底部导航
└── server/             # Python FastAPI 后端
    ├── main.py         # 接口路由
    ├── ai.py           # DeepSeek 调用（记账解析 / 文案 / 提醒）
    ├── db.py           # SQLite（熟客/记忆/交易/提醒）
    └── config.py       # 配置读取
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

### 2. 配置 DeepSeek API Key（可选）

在 `server/config.local.json` 中写入（该文件已被 .gitignore 排除，不会上传）：

```json
{ "api_key": "sk-xxxxxxxx", "base_url": "https://api.deepseek.com", "model": "deepseek-chat" }
```

不配置也能跑：记账金额可识别，文案/提醒返回兜底内容；配置后自动启用真实 AI。

### 3. 打开小程序

1. 微信开发者工具导入 `miniprogram/` 目录
2. 本地设置勾选「不校验合法域名」
3. 真机预览时把 `app.js` 里的 `baseUrl` 改成电脑局域网 IP，并保持手机与电脑同一网络

> 语音识别使用微信官方「同声传译」插件（appid：wx069ba97219f66d99）。如提示插件未授权，需在小程序管理后台「设置-第三方设置-插件管理」中添加。

## API 一览

| 接口 | 说明 |
|------|------|
| `POST /api/orders` | 一句话记账（AI 解析 + 熟客归档） |
| `GET /api/orders/today` | 今日营业汇总 |
| `GET /api/customers` | 熟客列表 |
| `GET /api/customers/{id}` | 熟客详情（记忆点 + 消费记录） |
| `POST /api/memories` | 添加记忆点 |
| `POST /api/copy` | 生成朋友圈文案 |
| `POST /api/reminders/generate` | 生成今日熟客提醒 |
| `GET /api/reminders` | 提醒列表 |
| `POST /api/reminders/{id}/done` | 完成提醒 |

> AI生成