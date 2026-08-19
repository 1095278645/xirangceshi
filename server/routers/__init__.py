"""巷子里的AI掌柜 · 路由包

按业务域拆分为独立 APIRouter 模块，由 registry.BUSINESS_DOMAINS 声明式注册表统一管理：
- registry.py  业务域注册表（增删能力唯一入口，main.py 遍历挂载）
- arch.py      领域上下文 / 任务队列 / 单店档案 / 心跳复盘
- basic.py     基础（健康检查 / AI 设置 / 文案生成）
- orders.py    记账 / 流水 / 凭证
- customers.py 熟客 / 记忆 / 提醒
- tax.py       税法计算 / 科目表
- store.py     单店经营模型
- report.py    月度报表导出
- payment.py   收款账户 / 账单同步

新增/停用/删除业务域：改 registry.py 的 BUSINESS_DOMAINS 声明即可，main.py 与流程代码零改动。
路由路径与原 main.py 完全一致，前端无需改动。
"""