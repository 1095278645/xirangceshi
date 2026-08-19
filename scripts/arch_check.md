---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'c25700bc-6280-4f2c-8365-6702a3b27516'
  PropagateID: 'c25700bc-6280-4f2c-8365-6702a3b27516'
  ReservedCode1: '608eae7e-529f-44c1-90c6-4871b64f5ffc'
  ReservedCode2: '608eae7e-529f-44c1-90c6-4871b64f5ffc'
---

# 八透镜架构检查机制（arch_check）

把「skill-optimizer 八透镜」固化成一个**可重复执行**、**幂等**、**只读无副作用**的项目健康检查脚本，供每次重构后一键复跑。它不做方法论说教，而是直接对代码库做静态量化度量。

## 运行

```bash
python scripts/arch_check.py                  # 检查项目根
python scripts/arch_check.py --out .temp/report.json   # 额外落盘结构化结果
python scripts/arch_check.py --minimal        # 只输出结论行
python scripts/arch_check.py --exit-code      # 退出码：0=PASS 1=WARN 2=FAIL（可接 CI）
```

脚本自身无副作用、不写业务数据；对同一状态重复运行结果一致，适合纳入提交前自检。

## 八透镜 → 本项目检查项映射

| 透镜 | 检查项（静态量化） | 文件范围 |
|---|---|---|
| L1 令牌经济 | 单文件>400行=FAIL，250-400行=WARN；顶层函数/类>40个=WARN | 全库 .py/.js |
| L2 单一职责 | 入口 main.py 薄且无业务；路由无直接 SQL；数据/引擎层齐全；路由文件≤80行 | server/ |
| L3 渐进披露 | 后端按域拆分 routers/；前端按职责分层 js/pages+core+init | server/ |
| L4 触发精准 | 35 端点；统一 /api 前缀；同「方法+路径」重复=冲突；静态资源入口兜底 | server/ |
| L5 护栏分离 | 配置独立；幂等去重集中；密钥脱敏集中；无硬编码凭据 | server/ |
| L6 转换管线 | 计算引擎（store/tax）纯函数、无 DB 副作用、阈值常量、分步注释 | server/ |
| L7 闭环控制 | try/except 数、结构化日志数、自动化测试、幂等重跑约束 | server/ |
| L8 频率分层 | 配置抽离；业务层无魔法阈值硬编码 | server/ |

## 当前基线（2026-08-19，含 L1/L8 修复 + L7 日志补强）

扫描 41 个代码文件，总评 **WARN**（从 FAIL 提升，已无 FAIL 项）。
- 数据层已按业务域拆分：`db_customers.py` / `db_ledger.py` / `db_payment.py` + `db.py` 聚合层（get_conn/init_db/DB_PATH 保留，测试可覆盖），db.py 由 510 行降至约 130 行。
- 关键写路径已补结构化日志：入账（orders.py）、账单同步成功（payment.py）、演示流水清空（payment.py），L7 日志由 3 处 → 6 处。

| 透镜 | 判定 | 关键数据 |
|---|---|---|
| L1 令牌经济 | **WARN** | 无 >400 行文件；剩 `settings.js` 275 / `store.py` 282 / `tax.py` 261（均属高内聚引擎/页面） |
| L2 单一职责 | PASS | main.py 78 行纯组装；路由无直接 SQL；数据/引擎层按域齐全 |
| L3 渐进披露 | PASS | 后端 8 个域模块；前端 core/pages/init/speech 分层 |
| L4 触发精准 | PASS | 35 端点、34 带 /api 前缀、无方法+路径重复 |
| L5 护栏分离 | PASS | 配置独立、幂等集中、脱敏集中、无硬编码凭据 |
| L6 转换管线 | PASS | store/tax 均为纯函数、无 DB 副作用、阈值常量齐 |
| L7 闭环控制 | PASS | 19 处 try、4 个测试（59 用例）、6 处幂等；日志已补至 6 处（入账/同步/清空） |
| L8 频率分层 | PASS | 配置已抽离；ai.py 万位进位语义化（`_CN_UNITS["万"]`），无魔法数字 |

## 观察项（非阻塞，可选）

1. **L1 三个 250-400 行文件**：store.py / tax.py 为高内聚计算引擎、settings.js 为设置页，行数合理，暂不强制拆；后续继续膨胀再拆。
2. 每次重构后复跑 `python scripts/arch_check.py --exit-code` 即可验收是否回归（0=全 PASS / 1=WARN / 2=FAIL）。

> AI生成