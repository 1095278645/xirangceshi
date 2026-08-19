---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'd1f9bdf3-744f-4233-b166-e68dbb718a5b'
  PropagateID: 'd1f9bdf3-744f-4233-b166-e68dbb718a5b'
  ReservedCode1: '3121546c-564d-483d-acb4-e0fca4e44b3f'
  ReservedCode2: '3121546c-564d-483d-acb4-e0fca4e44b3f'
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

## 当前基线（2026-08-19）

扫描 38 个代码文件，总评 **FAIL**（瓶颈在 L1）。

| 透镜 | 判定 | 关键数据 |
|---|---|---|
| L1 令牌经济 | **FAIL** | `server/db.py` 510 行；`settings.js` 275 / `store.py` 282 / `tax.py` 261 行 |
| L2 单一职责 | PASS | main.py 78 行纯组装；路由无直接 SQL；引擎层齐全 |
| L3 渐进披露 | PASS | 后端 8 个域模块；前端 core/pages/init/speech 分层 |
| L4 触发精准 | PASS | 35 端点、34 带 /api 前缀、无方法+路径重复 |
| L5 护栏分离 | PASS | 配置独立、幂等集中 3 处、脱敏集中 5 处、无硬编码凭据 |
| L6 转换管线 | PASS | store 管线（9 处分步/8 常量）纯函数；tax 纯函数 15 常量 |
| L7 闭环控制 | PASS | 19 处 try、4 个测试、5 处幂等；可观测性日志仅 3 处（偏弱） |
| L8 频率分层 | WARN | 配置已抽离；`server/ai.py: if unit == 10000` 魔法阈值待入配置 |

## 待办（按 FAIL→WARN 顺序）

1. **L1 FAIL：`server/db.py` 510 行** —— 需按职责拆分（连接/建表、查询、统计、同步日志等），拆后单文件<400 行。
2. **L8 WARN：`server/ai.py` 魔法阈值 `unit==10000`** —— 提到 config，避免散落。
3. **L7 观察项：可观测性日志偏少（3 处）** —— 关键写路径（同步、入账、报错回写）补结构化日志。

> AI生成