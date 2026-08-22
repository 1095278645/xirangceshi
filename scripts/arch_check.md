---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '776d352c-a3ac-43ec-b372-a1336273e914'
  PropagateID: '776d352c-a3ac-43ec-b372-a1336273e914'
  ReservedCode1: '75f6c785-f4c0-4f6c-b306-ca0be59fc10c'
  ReservedCode2: '75f6c785-f4c0-4f6c-b306-ca0be59fc10c'
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
| L2 单一职责 | 入口 main.py 薄且无业务；路由无直接 SQL；数据/引擎层齐全；AI 能力层齐全（ai/team/team_domains）；路由文件≤80行 | server/ |
| L3 渐进披露 | 后端按域拆分 routers/；前端按职责分层 js/pages+core+init | server/ |
| L4 触发精准 | 50 端点；统一 /api 前缀；同「方法+路径」重复=冲突；静态资源入口兜底 | server/ |
| L5 护栏分离 | 配置独立；幂等去重集中；密钥脱敏集中；无硬编码凭据 | server/ |
| L6 转换管线 | 计算引擎（store/tax）纯函数、无 DB 副作用、阈值常量、分步注释；**AI 编排管线**（ai/team/team_domains）分步注释 + 兜底/降级 | server/ |
| L7 闭环控制 | try/except 数、结构化日志数、自动化测试、幂等重跑约束 | server/ |
| L8 频率分层 | 配置抽离；业务层无魔法阈值硬编码 | server/ |

## 当前基线（2026-08-19，AI 能力纳入八透镜 + 业务域注册表 + 引擎容错）

扫描 47 个代码文件，总评 **WARN**（无 FAIL 项，WARN 均属已知合理项）。
- 业务路由改为声明式注册表：`routers/registry.py` 的 `BUSINESS_DOMAINS` 统一登记，main.py 只遍历挂载，增删能力零改动组装。
- AI 能力层纳入 L2（齐全性）与 L6（编排管线：分步注释 / 兜底降级）；为此补了 `team.run_parallel` 的单员工容错（失败返回占位文本，不拖垮整个团队）。

| 透镜 | 判定 | 关键数据 |
|---|---|---|
| L1 令牌经济 | **WARN** | 无 >400 行文件；剩 ai.py 258 / settings.js 275 / store.js 276 / store.py 282 / tax.py 261 / team_domains.py 260（均属高内聚引擎/页面） |
| L2 单一职责 | **WARN** | main.py 93 行纯组装；路由无直接 SQL；数据/引擎层齐全；AI 能力层齐全；仅 arch.py 98 / orders.py 85 略超 80 行 |
| L3 渐进披露 | PASS | 后端 10 个域模块；前端 core/pages/init/speech 分层 |
| L4 触发精准 | PASS | 50 端点、49 带 /api 前缀、无「方法+路径」重复 |
| L5 护栏分离 | PASS | 配置独立、幂等集中、脱敏集中、无硬编码凭据 |
| L6 转换管线 | PASS | store/tax 纯函数无 DB 副作用；AI 编排：ai.py 兜底 3、team.py 兜底 2（引擎容错）、team_domains.py 兜底 14，均分步有注释 |
| L7 闭环控制 | PASS | 29 处 try、7 处结构化日志、8 个测试文件（119 用例）、6 处幂等 |
| L8 频率分层 | PASS | 配置已抽离；业务层无魔法阈值硬编码 |

## 观察项（非阻塞，可选）

1. **L1 六个 250-400 行文件**：store.py / tax.py 为高内聚计算引擎、ai.py / team_domains.py 为能力/编排层、settings.js / store.js 为前端页面，行数均合理，暂不强制拆；后续继续膨胀再拆。
2. **L2 两个路由文件略厚**：arch.py / orders.py 各超 80 行（98/85），属路由转发 + 轻业务组装，暂不拆；继续膨胀再拆。
3. 每次重构后复跑 `python scripts/arch_check.py --exit-code` 即可验收是否回归（0=全 PASS / 1=WARN / 2=FAIL）。

> AI生成