# API 一览

> 从 README 外移（渐进披露：低频内容按需查阅）。


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
