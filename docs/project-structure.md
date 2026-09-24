# 项目目录结构

> 从 README 外移（渐进披露：低频内容按需查阅）。


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
