"""巷子里的AI掌柜 · 路由包

按业务域拆分为独立 APIRouter 模块：
- basic.py    基础（健康检查 / AI 设置 / 文案生成）
- orders.py   记账 / 流水 / 凭证
- customers.py 熟客 / 记忆 / 提醒
- tax.py      税法计算 / 科目表
- store.py    单店经营模型
- report.py   月度报表导出
- payment.py  收款账户 / 账单同步

路由路径与原 main.py 完全一致，前端无需改动。
"""