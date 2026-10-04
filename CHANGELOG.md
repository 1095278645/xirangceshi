# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增 · 文案打法库（渠道 × 骨架 × 语气）
借鉴一个成熟生成型 skill 的方法论（把「怎么写」拆成正交维度 + 预设 + 自动选型，
并把合规/AI 味做成可执行自检），把文案能力从「两个员工 + 一段长提示词 + 碰运气」
升级成**可组合、可解释、可验收**：

- `server/copy_playbook.py`（数据层）：**7 渠道**（朋友圈/小红书/抖音/微信群/招牌/团购/回评）
  × **7 骨架**（场景移植/痛点开场/清单/对比反转/数字钩子/问答/反常识）
  × **6 语气**（街坊口语/种草活力/群通知/叫卖/回评诚恳/专业克制），外加 **10 个配方**缩写；
  每个渠道带读者状态、结构纪律、渠道禁忌与字数上限，每个骨架带示例与"最容易写坏的地方"。
- `server/copy_director.py`（导演层）：自动选型（显式参数 → 意图信号 → 业态兜底 → 最常用兜底），
  并产出一句**人话理由**（"从「小红书」判断是小红书·探店笔记的场景"）进交付报告。
- `server/copy_review.py`（检查层）：**硬规则**合规与 AI 味自检 —— 绝对化用语、医疗功效、
  收益承诺、诱导流量、虚假紧迫、渠道禁忌（命中即 fail），字数/具体度/行动指引/通用结尾/
  骨架契合度/行数/感叹号（warn）；外加**配图方案**（封面/内容/结尾：画面、图上文字、比例、
  可直接喂绘图模型的提示词）与**交付自检报告**。
- **只标记不改写**：违禁词不做自动替换，只标记 + 给"改哪儿"；理由与 `plain_language` 的
  术语翻译不同 —— 那是翻译，这是判定，判定结果不能偷偷改结论。
- 接口：`POST /api/insights`（`scene=copy`）新增顶层 `channel/skeleton/tone/recipe/biz_type/
  return_report`（全部 Literal，非法值 422），响应新增 `combo`（含理由）/`verdict`/`report`；
  **向后兼容**（老调用不变，`variants` 仍是模型给了几稿就是几稿）。
- 文档：[`docs/copy-playbook.md`](docs/copy-playbook.md)（维度表、选型规则、检查规则、扩展须知）。
- 测试 `tests/test_copy_playbook.py`（41 项）：注册表自检、选型、硬检查、配图方案、
  HTTP 契约（含非法枚举 422），以及一条硬回归 —— **无 Key 降级文案逐字不变**。

### 新增 · 知识资产治理层
- `server/knowledge_assets.py`：知识即资产 —— 每条结论带 `kind / subject / statement /
  evidence / source_kind / source_ref / volatility / confidence`，并有状态机
  `draft / active / superseded / retired` 与内容哈希幂等（同内容不重复登记；
  内容变了旧版标 `superseded` 并写 `valid_to / superseded_by`，新版 `version+1`，
  **旧版不删**，可回滚可追溯）。
- `server/knowledge_extract.py`：复盘产出自动登记为资产（结论 / 命中的技能卡片 /
  原始事实 / 采纳归因），任一段失败只跳过该段，不影响复盘主链路。
- `server/knowledge_governance.py`：治理聚合 —— 总览、运行期核验、知识包导出/导入
  （走 `safe_io` 原子写，落 `server/data/knowledge/`）。
- **运行期核验**：资产按 `volatility`（stable / slow / volatile）分层，
  动态事实在读取时回到真值上对一遍，对不上的标记 `verify_ok=0` 并写明人话原因 ——
  不拿过期数字当结论。新建的 volatile 资产**从未核验过**，因此从"未通过"起步
  （避免把"刚登记、没对过真值"显示成"已核验无误"）。
  三态里 `unknown` **不等于** `ok`（真值取不到时不塞 0 冒充）。
- `server/db_knowledge_schema.py`：建 `knowledge_assets` / `knowledge_edges` 两表（幂等）；
  `db.SCHEMA_VERSION` 4 → 5，老库首次连接自动迁移。

### 新增 · 跨域关系索引层
- `server/shop_relations.py`：从既有表抽取**带证据的边**（买过 / 卖出在 / 供应 /
  由供货 / 开票对象 / 我欠他 / 他欠我 / 关联流水 / 提及），重算走**增量合并**
  （未变的边只刷新 `last_seen`，未再被证实的边软删可回溯），并提供
  关系地图与线索追溯（1~3 跳）。
- `server/db_relations.py`：关系边数据层（`upsert_edge` / 软删 / BFS / 最短路 /
  体检 / 过期清理），体检覆盖孤儿边、悬空对象、重复键、陈旧边。
- **护栏**：增量模式下抽取结果为空时**不得清空索引**（一次数据抖动不该抹掉整张网）；
  回收还要求 `full=true` **且** `confirm=true`（铁律2：破坏性动作显式确认）。

### 独立复核后修掉的缺陷（对抗式复核结论，均已带回归用例）
- **`full=true` 曾可绕过"空结果不清库"护栏**：一次数据抖动 + 一个 query 参数就能把整张关系网
  软删光。现在抽取为空**无论参数一律不动**；回收另需 `confirm=true`，未确认时返回
  `skipped="confirm_required"` 与 `pending_recycle` 条数。
  （`tests/test_shop_relations.py::test_full_without_confirm_never_wipes_the_index`）
- **新建的 volatile 资产被默认标成"已核验无误"**：`verify_ok` 从 1 起步，等于把"刚登记、
  没对过真值"显示成"对得上"。现在从 0 起步，只有真跑过核验并对上才转 1。
- **列表查询的枚举是自由文本**：`?kind=想当然` 会 200 + `count=0`，看起来像"没有这类知识"。
  现在 `kind/state/volatility` 一律 `Literal`，非法值请求期 422（铁律5）。
- **文档过度宣称"降低置信度"**：核验只改 `verify_ok` 与 `drift_note`，不改 `confidence`
  —— 文档已改为与实现一致。
- **draft 状态破坏幂等**：`register_asset` 原先只查 active，草稿重复登记会插多行。
  改为同时匹配 `active/draft`。

### 第二轮独立复核后修掉的问题（更细的一轮，同样带回归用例）
- **关系域整体 500**：`knowledge_edges` 缺失/迁移失败时 `/api/relations/*` 三个 GET 直接 500
  （知识域有降级、关系域没有）。现在路由统一兜底成 200 + `available=false` + 原因；
  `db._ensure_schema` 迁移失败时**撤销 ready 标记**（原先该进程会此后永久跳过迁移，重启才恢复）。
- **读接口的写放大**：`GET /api/heartbeat` 原先对**全部** active volatile 资产逐条核验并写库
  （200 条时实测 12.4s / 200 次写）。现在先截断再核验（只核要展示的 5 条），
  核验写回改成一次连接 + `executemany`。
- **并发登记造出两条 active**：登记改为 `BEGIN IMMEDIATE` + `(asset_key)` 部分唯一索引
  （`WHERE state IN ('active','draft')`），并给旧库加了收敛迁移；`asset_id` 撞号改为换号重试。
- **草稿顶掉在用结论**：`state='draft'` 曾在 active 存在时把它标 superseded，导致该 key
  一条 active 都不剩；现在草稿不落库并明确返回原因，`draft→active` 变成真正的状态迁移。
- **`entity_type` 自由文本**：`/api/relations/chain` 的非法类型会 200，现在 422；
  `schemas.RelationsQueryIn` / `KnowledgeExportIn` 两个没接线的模型删除，避免"死契约"。
- **正文无长度上限**：`statement` 限 2000、`subject` 限 200（请求期 422 + 数据层再截断一次）。
- **`find_paths` 重复路径与方向不明**：按**节点序列**去重（保留最短），每一跳补 `direction`
  （顺着关系还是反向走），否则会给出店里并不存在的方向。
- **返回码一致性**：`POST /api/knowledge/verify` 目标不存在时由 200+error 改为 **404**。
- **导出文件与失效边无清理**：新增 `knowledge_governance.knowledge_maintenance()`，
  挂进 `main._backup_loop`（每 6 小时），清理过期知识包文件与过保留期的失效边；
  新增 `config.KNOWLEDGE_BUNDLE_KEEP_DAYS`。
- **测试假绿**：4 条只断言"字段存在"的用例改为断言真实内容（核验结果不许是降级错误对象、
  降级用例必须真造降级、backfill 断言 `errors == []`、环用例补真环与菱形图）。
- 代码搬家（文件回到 400 行内，API 不变）：`db_relations_graph.py`（图的 BFS/最短路）、
  `knowledge_backfill.py`（进化层回填）。
- 删除两个"没接线"的模型（`KnowledgeExportIn` / `RelationsQueryIn`），避免留下死契约。
- **修掉一处 CI 日历 flake**：`seed_demo_data.py` 的熟客到店是"每天随机挑 4~6 位"，
  在每月 1~2 号灌数据时挂到熟客名下的流水可能只有 1~4 笔，低于 `mp_demo_check`
  的眼门槛 5 → 演示自检判"演示数据不完整"、CI 变红，而数据本身没问题（**2026-10-04
  那次 CI 失败就是这个**）。现在加"熟客消费记录保底"：不足 `MIN_CUSTOMER_TXNS=8` 时
  用各熟客的固定点单补齐（日期落在当月窗口内、早市时段），演示与 CI 不再依赖今天是几号；
  回归用例 `tests/test_demo_flow.py::test_demo_seed_customer_transaction_floor`。

### 新增 · 接口与前端
- 新业务域 `knowledge` / `relations`（`routers/` 按注册表挂载，均属核心域）。
- `POST /api/knowledge/assets` 等 8 个知识接口、4 个关系接口（见
  [`docs/api-reference.md`](docs/api-reference.md)）。
- `/api/heartbeat`（GET/POST）新增 `knowledge` 分块；`/api/metrics/ai/capability`
  新增知识治理子块。
- 网页端复盘卡新增「这些结论的依据」可展开区块（来源 / 版本 / 证据 / 是否待复核）；
  小程序端同区块，并修掉「掌柜依据」直接输出数组导致逗号拼接的显示问题。

### 文档
- [`docs/qianxuesen-review-ai-shopkeeper.md`](docs/qianxuesen-review-ai-shopkeeper.md)：
  系统工程视角的评审与三步路线图（含诚实边界）。
- [`docs/knowledge-governance.md`](docs/knowledge-governance.md)：字段语义、状态机、
  挥发度策略、接口与开发约定。

### 修复 · 线上实测发现的问题（2026-10-04 对公网部署逐项走查后补）

背景：以**匿名访客**与**已登录店主**两种身份对线上部署做了真实走查（51/51 接口、
15/15 界面），发现 4 个问题，本轮修掉 3 个、并把第 4 个变成"不可能再静默发生"。

- **分类方向一致性**（`categories.category_direction` / `reconcile_category`）：
  模型偶发把支出挂到收入类科目 —— 线上实测出现过 `trans_type='expense'` +
  `category='其他收入'`（同一句话换个时间跑又是对的，属模型抖动）。旧实现只做白名单
  归一、**不校验方向**，于是 `_auto_voucher` 按分类映射科目，给一笔支出生成了
  「借 库存现金 / 贷 其他业务收入」这种**方向相反**的凭证：账本品类与凭证一起错，
  而且不报任何错。现在：
  - 方向**由科目类别推导**（`ACCOUNT_CATEGORY_OF`），不靠人工维护的方向表，
    以后往 `CATEGORY_TO_ACCOUNTS` 加分类，方向自动跟着对；
  - 记账两条路径（单笔 / 多笔）不一致时**以收支方向为准**换成该方向的通用科目并留痕；
  - 人工更正（`db_corrections.edit_transaction`）遇到方向不符**直接拒绝**并说明原因；
  - 回归用例 `tests/test_category_direction.py`：覆盖推导、纠正、凭证方向、
    人工拒绝，以及"落库分类方向必须与收支一致"的 HTTP 端到端断言。

- **未登录时的全局提示**（`core.js` 的 `tokenBanner()`）：401 时各页面会**安静地渲染成
  一片 0**，店主/评审分不清"今天没生意"与"没填访问令牌"（线上第一眼就是这个表现）。
  除设置页原有的详细区块外，**每个页面**现在都会挂一条提示条 + 「去填写访问令牌」入口；
  任意请求成功即自动消失（不会填对了还一直挂着）。`check_web_render.js` 增加行为断言
  （未登录要挂、设置页不重复挂、已登录不挂）。

- **AI 配了 Key 却调不通，不再静默降级**：
  - 新增 `GET /api/health/ai`：**真的调一次模型**并返回
    `{configured, base_url, model, reachable, latency_ms, error}`。`/api/health` 里的 `ai`
    只说明"配了 Key"，而线上实测踩过的正是"Key 与端点不是同一家 → 所有 AI 调用 401 →
    各功能安静退回规则兜底"（复盘日志写的是"退回基础拼装"，页面看起来照常）。
    健康检查保持轻量，探活独立成端点，供部署验收与演示前自检调用。
  - `scripts/mp_demo_check.py` 新增第 5 部分「AI 连通性」：没配 Key 只提示（属正常降级），
    **配了 Key 却调不通直接进"必须修"**。
  - `deploy/up.sh` 新增 8.2 节 AI 自检，与已有的鉴权自检、时区自检并列。

- **部署配置：`AI_PIPELINE` 没被传进容器**（`deploy/docker-compose.public.yml`）：
  编排的 `environment` 里没有这一项，`.env` 里写了 `AI_PIPELINE=team` 也会被忽略 ——
  线上日志里是「快速·review」，说明对外讲"AI 员工团队"、实际跑的是 `fast` 单次生成。
  现在透传，并在 `deploy/.env.example` 里显式给出 `AI_PIPELINE=team` 与代价说明，
  同时补了"Key 与 BASE_URL 必须同源"的告警与自查命令。

### 已知例外（未拆，说明理由）
- 前端三个文件仍超 400 行，且本次改动各增加约 20 行（`home.js` 588→608、
  小程序 `index.js` 423→442、`index.wxml` 249→268）：都属既有已知例外
  （`Page({...})` 主体与小程序的单页复盘卡，强拆会动演示动线）；
  新增的"结论依据"区块只是沿用既有 `.snap-toggle/.snap-box` 模式，未引入新抽象。
  `scripts/arch_check.py` 因此仍报 L1 FAIL（可解释、已知）。

### 验收
- `cd server && python -m pytest -q` → **713 passed**（227 subtests，全程不联网；含线上实测后的分类方向一致性、未登录横幅与 AI 探活补强）。
- 端到端实跑（**复制**演示库，未碰原库）：掌柜复盘 → 登记 17 件知识资产 →
  运行期核验 13 条（ok 5 / unknown 8，unknown 是"没有可对比真值来源"的诚实结论）→
  导出知识包 → 关系索引合并 513 条边、二次合并 `unchanged=513 / deactivated=0`、
  体检 `ok=True`。
- `check_web_pages.py` / `check_mp_pages.py` / `check_mp_api.py` /
  `check_web_render.js` / `check_mp_runtime.js` / `check_frontend_contract.py` /
  `mp_demo_check.py`（问题 0）全绿。

## [1.1.12] - 2026-10-01

参赛交付前的**最终审核与优化**：修掉 3 条会让全量测试变红的日期脆弱用例、
修掉一处会把凭证记错科目的静默兜底，并把版本与文档口径收敛到同一套数字。

### 修复 · 日期脆弱测试（全量测试由「3 failed」回到全绿）
- `tests/test_gaps_http.py::TestAccountingFlow` 硬编码期间 `2026-09`，而
  `db.add_transaction` 落的是**当前时间戳** —— 一进入 10 月，整片会计闭环用例
  必然失败（收入/费用读成 0）。改为按 `date.today()` 推导 `period` 与 `as_of`。
- `tests/test_store.py::TestLedgerReverseDerive` 用「今天 / 昨天 / 前天」并断言
  当月 `active_days == 3`，于是**每月 1~2 号必挂**（前两天跨到上个月）。
  改为显式挑选一个放得下连续三天的月份，并把该月作为断言期间。
- 两条用例都补了注释说明"为什么不能用当天硬编码"，避免以后回退。

### 修复 · 分类 → 科目静默错映射
- `categories.resolve_accounts()`：新增统一解析入口，**先同义词归一、再兜底**，
  并返回"是否走了兜底"供调用方留痕；`db_ledger` / `db_corrections` 改为复用它。
- 补 `CATEGORY_ALIASES`：`主营业务成本 / 主营成本 → 进货`。原先「主营业务成本」
  不在映射表里，凭证会被兜底成「管理费用-办公费」——账本写着主营业务成本、
  凭证却是办公费，成本结构整个错位，而且只有一条 warning，店主看不出来。
- 回归用例：`tests/test_smoke.py::test_accounting_synonym_lands_on_right_account`、
  `tests/test_order_amount_missing.py::test_resolve_accounts_flags_fallback`。

### 修复 · 版本与口径漂移
- 测试数三处不一致（badge 575 / README 正文 517 / 文档 517 / 提交说明 550）
  → 统一为实测 **617 项**（`pytest -q`，209 subtests）。
- 版本号三处不一致（`main.py` 0.2.0 / `pyproject.toml` 1.0.0 / CHANGELOG 1.1.11）
  → 统一为 **1.1.12**。

### 验收
- `cd server && python -m pytest -q` → **617 passed**（全程不联网）。
- `scripts/check_frontend_contract.py`、`check_mp_api.py`、`check_mp_pages.py`、
  `check_web_pages.py`、`check_web_render.js`、`check_mp_runtime.js` 全绿。


## [1.1.11] - 2026-09-22

连锁总部视图 + 云化实装（Litestream 配置/编排/恢复演练）+ 真实用户验证工具包。

### 新增 · 连锁总部视图（OPC：一个人也能管多家店）
- `GET /api/shops/overview`：**逐店在各自店上下文内取数**（一店一库，绝不混库），
  返回每店 本月收入/支出/结余、日销、毛利率、保本线是否达标；并给出跨店合计与"低于保本线"预警名单。
- 网页端「多店 / 成员」页新增「**总部视图 · 跨店汇总**」卡片。
- 测试 `tests/test_hq_overview.py`：两店数据不串、合计正确、空店不报错。

### 新增 · 云化实装（第 1 步：异地备份）
- `ops/litestream.yml`：生产可用配置（凭据全走环境变量；`sync-interval=10s`、24h 全量、7 天保留；
  库路径用 `LITESTREAM_DB_PATH` 适配本机/容器）。
- `docker-compose.litestream.yml`：与应用**共用数据卷**的 litestream sidecar（含恢复命令）。
- `scripts/restore_drill.py`：**可执行的灾难→恢复演练**（建快照 → 删库 → 恢复 → 计数一致），
  不依赖任何云服务即可验证恢复链路。实测演示库：29 表 / 4,490 笔 / 10 熟客，恢复后完全一致。
- `docs/deployment-evolution.md`：第 1 步由"样例"改为"**已实装**"，附命令与实测结果。

### 新增 · 真实用户验证工具包
- `scripts/validation_report.py`：**只读**打开真实库，导出一页验证报告
  （使用广度 / 提醒送达率 / AI 调用与延迟 / 毛利率·日销·保本线）。
- `docs/user-validation-plan.md`：目标与达标线、样本周期、指标口径、**店主问卷（前后两次）**、
  结果记录模板、产出方式，以及"哪些必须由人来做"的诚实说明。

## [1.1.10] - 2026-09-22

OPC 补齐 P1/P2：**增长动作（攻）+ 接通进化真值 + 合规页 + 小程序运行时测试 + 「24 小时」演示动线**。

### 新增 · P1-4 增长动作（拉新 / 复购 / 选品提价）
- 统一洞察入口新增 `scene=growth`（`insight_service._growth`）：结合**熟客消费/沉睡**与**库存**产出
  三条可执行动作；**无 Key 走规则化兜底**（同样点名对象与做法，不是空话）。
- `ai_prompts.generate_growth_actions()`；`UnifiedInsightIn.scene` 与共享契约 `UI_ENUMS.insightScene`
  同步加入 `growth`（CI 防漂移继续生效）。
- 网页端「熟客」页新增「增长动作」卡片（一键生成 / 重新生成）。

### 新增 · P1-5 接通进化真值（人类信号）
- 新增 `POST /api/evolution/outcome`：店主**采纳/修改** AI 产出 → 记胶囊（真值来源）。
- 小程序：文案页「复制去发朋友圈」时上报采纳（`api.reportOutcome`）；网页端 `copy.js` 恢复采纳上报。
- 演示/自检脚本 `scripts/demo_evolution_loop.py`：一条命令跑通 **候选 → 验证门 → 转正 → 账本**
  （纯本地、不调模型、`--temp` 可用临时库）。
- 修复：`distill_skill` 原先只取 10 条胶囊却要求 `EVOLUTION_MIN_SAMPLES(20)` 样本 → **门槛不可达**；
  现按 `max(10, MIN_SAMPLES)` 取数，验证门才真正可用。

### 新增 · P2
- **合规页** `docs/compliance.md`：数据主权 / 不碰资金 / AI 传输边界 / 税务口径与免责 / 红线。
- **小程序运行时测试** `server/scripts/check_mp_runtime.js`（Node 桩加载全部页面 + Page 结构断言 +
  共享契约可用性），并接入 CI。
- **演示动线** `docs/demo-guide.md` 增加开场「一人店主的 24 小时」（按一天讲，而不是按功能点讲）。

### 测试
- 新增 `tests/test_growth_scene.py`（规则兜底三条动作 / 未知场景拒绝 / HTTP 接受 growth）。

## [1.1.9] - 2026-09-22

按 OPC（一人公司）视角补齐短板：**执行闭环 + ROI 数字 + AI 员工编制表**。

### 新增 · 执行闭环（P0-1：AI 不只出建议，要送达并留痕）
- `POST /api/reminders/{rid}/send`：投递熟客提醒。**通道留空=自动**（优先已启用订阅，
  否则本地收件箱 `notifications.jsonl`）→ **零配置也能送达**。
- 投递结果回写 `reminders.sent_at / send_channel / send_ok / send_error`（失败原因入库）。
- `heartbeat.push_pending_reminders()`：每日把"未完成且未送达"的提醒**自动补发**（成功即不再重发）。
- `notifications.auto_channel()`：统一的自动通道解析。
- 网页端「熟客」页新增**今日提醒**区块：显示送达状态 + `发送` / `完成`。
- 迁移：`reminders` 增 4 列（`SCHEMA_VERSION` 3 → 4，老库自动升级）。

### 新增 · ROI（P0-2：给"省了多少钱"的数字）
- `metrics.roi_summary()`：人力替代对照（可配置）+ 本机真实 AI 成本 + 净收益 + 定价毛利，
  并入既有 `GET /api/metrics/ai` 的 `roi` 分块（**不新增端点**）。
- `config.ROI_LABOR` / `ROI_SUGGESTED_PRICE_YUAN` / `ROI_NOTE`（可在 `config.local.json` 覆盖）。
- 运行指标页新增「OPC · 人力替代与净收益」卡片。

### 新增 · 文档（P0-3）
- `docs/opc-ai-team.md`：**AI 员工编制表（7 岗位 + 自动化程度）**、执行闭环说明、ROI 与定价草案、诚实边界。

### 测试
- 新增 `tests/test_reminder_send.py`（6 项）：mock 送达留痕 / 自动通道回落 / 失败入库并 502 /
  404 / 自动补发幂等 / 完成的不再补发。
- `check_web_render.js` 新增"有数据"渲染抽检（覆盖 metrics 页 ROI/进化分块）。

## [1.1.8] - 2026-09-22

双前端收敛（短期步）：**枚举标签**与**校验谓词**并入共享契约并接线。

### 新增（`shared/frontend_contract.js`）
- `LABELS` + `labelTransType / labelInvoiceKind / labelStockMovement`
  （收入/支出、销项/进项、入库/出库/盘点；未知枚举原样回显）。
- 校验谓词（只返回布尔，**不改各端文案**）：`isNonEmpty / isPositiveNumber / isNonNegativeNumber`。
- 检查脚本新增：`LABELS` 键集必须与对应 `UI_ENUMS` 一致（`transType` 固定 `income/expense`）。
- `check_web_render.js` 新增**契约运行时自检**（标签 / 谓词 / 错误归类 / 请求头），无浏览器也能在 CI 跑。

### 接线
| 端 | 文件 | 改动 |
|---|---|---|
| H5 | `pages/stock.js` | 库存动作标签改用 `FC.labelStockMovement`（原为内联 map） |
| H5 | `pages/home.js` | 5 处"收入/支出"三元 → `FC.labelTransType`；金额校验 → `FC.isPositiveNumber` |
| H5 | `pages/invoice.js` | 销项/进项标签 → `FC.labelInvoiceKind`；金额校验 → `FC.isPositiveNumber` |
| H5 | `pages/collect.js`、`pages/finance.js` | 金额校验 → `FC.isPositiveNumber` |
| 小程序 | `pages/books/books.js` | 库存动作标签 + 2 处金额校验改用契约（新增 `require`） |
| 小程序 | `pages/index/index.js` | "收入/支出" → `FC.labelTransType`（新增 `require`） |

### 兼容性
- **行为与文案不变**（谓词仅在原本就是 `!(x > 0)` 的位置替换；`进账` 等各端有意的不同措辞未动）。

## [1.1.7] - 2026-09-22

双前端收敛（Batch F）：把 H5 与小程序的**共享契约**收敛为单一真源 + 自动同步 + CI 防漂移。

### 背景
重复的并不是"页面逻辑"（两端运行时不兼容），而是**契约**：存储键、请求头、HTTP 错误文案、
UI 枚举。此前它们被各写一份，靠人记着同步 —— 属于典型的"同一件事两处真相"。

### 新增
- `shared/frontend_contract.js`（**唯一真源**，UMD：小程序 `module.exports` / 浏览器全局）：
  `STORAGE_KEYS`、`normalizeBaseUrl()`、`buildAuthHeaders()`、`classifyHttp()`、`UI_ENUMS`。
- `scripts/sync_frontend_contract.py`：真源 → H5 / 小程序两处副本（带 `AUTO-GENERATED` 头）。
- `scripts/check_frontend_contract.py`：**CI 防漂移** —— 校验副本与真源逐字节一致，
  并校验 `UI_ENUMS` 与 `server/schemas.py` 的 `Literal` 一致。

### 变更
- 网页端 `static/js/core.js`：令牌/店铺键、请求头、401/403 文案改用共享契约
  （`index.html` 增加 `shared/frontend_contract.js`，先于 core.js 加载）。
- 小程序 `utils/api.js`：`require` 共享契约，存储键/地址归一/请求头/错误归类改用契约。
- `scripts/check_web_render.js`：加载顺序加入共享脚本。
- CI 新增步骤「双前端契约一致性（防漂移）」。
- `AGENTS.md`：新增"双前端共享契约"约定（只改真源、副本禁手改）。

### 兼容性
- 行为不变（文案与请求头等价；网页端"请求失败 N"统一为"请求失败：N"）。

## [1.1.6] - 2026-09-22

L2 单文件瘦身：服务端 5 个超长文件全部拆到 400 行以内（小程序两页列为已知例外）。

### 变更（全部"搬家"式拆分，对外接口不变，均以 re-export 保持兼容）
| 原文件 | 行数 | 拆出模块 | 现行数 |
|---|---:|---|---:|
| `notifications.py` | 467 | `notify_channels.py`（通道适配/注册表/本地收件箱） | 229 |
| `accounting.py` | 507 | `accounting_close.py`（期末结转/反结转/原语下沉） | 344 |
| `ai.py` | 525 | `ai_prompts.py`（提示词片段 + 4 个生成器） | 387 |
| `qr.py` | 422 | `qr_matrix.py`（矩阵放置/掩码/罚分） | 258 |
| `shops.py` | 559 | `shops_store.py`（店铺 CRUD + 成员/令牌管理） | 277 |

- 依赖方向统一为**单向**：拆出模块不在顶层 import 原模块；需要时用"原语下沉"或
  函数内延迟导入（`accounting_close`、`ai_prompts`、`qr_matrix`、`shops_store` 均如此）。
- 测试随代码搬家调整打桩点（`test_notifications` 改 patch `notify_channels._post_json`）。

### 已知例外（未拆，说明理由）
- `miniprogram/pages/books/books.js`（595）、`miniprogram/pages/index/index.js`（422）：
  主体是 `Page({...})` 方法（依赖 `this.setData/this.data`），拆分属侵入式改动，
  而小程序端**没有运行时自动化测试**（仅 `check_mp_pages` 绑定检查），
  强拆会危及现场演示动线 → **列为已知例外，建议不动演示动线的前提下单独排期**。
  因此 `arch_check` 的 L1 仍会因这两个文件报 FAIL（可解释、已知）。

## [1.1.5] - 2026-09-22

补齐 Skill Optimizer 诊断的两条 P1：提示词预算护栏 + 开发约定（铁律）。

### 新增
- **提示词预算护栏（Lens 11 速度硬约束）**：`ai.chat` 入口统一做预算检查 ——
  `config.AI_PROMPT_WARN_CHARS`（默认 8000，超限告警）与
  `config.AI_PROMPT_MAX_CHARS`（默认 24000，超限截断且**保留 system**）。
  保证任何"脚本原始大输出"都不会未经处理直接喂给模型；各处仍应先按语义取 Top-N。
- **`AGENTS.md`（Lens 5 文档层铁律）**：面向 Agent 的精准入口，写明 5 条必守铁律
  （密钥不入库 / 危险操作 confirm / 数据本地 / 变更必过验收 / 有限取值用枚举）
  与开发约定（阈值入 config、脚本输出先截断、单文件 ≤400 行、渐进披露、护栏带测试）。

### 测试
- 新增 `tests/test_ai_prompt_guard.py`（短提示不动 / 告警不截断 / 超限截断且保 system /
  畸形输入不崩 / `ai.chat` 真实接线）。

## [1.1.4] - 2026-09-22

按 Skill Optimizer 十一透镜对工程做一轮体检并落地修复（ACI 防呆 + 文档渐进披露）。

### 变更
- **ACI Poka-yoke（Lens 10）**：有限取值参数由自由文本改为 `Literal` 强约束 ——
  `UnifiedInsightIn.scene`、`StoreModelIn.traffic/competitor`、
  `NotifySubscriptionIn.channel`、`NotifyTestIn.channel`、
  `StockMoveIn.movement`、`InvoiceIn.kind`、`SettingsIn.ai_pipeline/api_profile`。
  非法值现在在**请求期 422**，不再运行期 400 或静默兜底（此前 `traffic/competitor`
  未知值会静默取中间档）。
- **文档渐进披露（Lens 1/3/8）**：README 由 557 行降到 **499 行** ——
  把"完整 API 一览"外移到 `docs/api-reference.md`、"目录结构"外移到
  `docs/project-structure.md`，README 只留一行指针。
- 保留 `StoreModelIn.biz_type` 为自由文本：`/api/store/benchmark` 有意支持
  "未收录业态 → matched=false 回落默认"（已在代码注释说明）。

### 测试
- `test_unified_insights`：非法 scene 的期望由 `400` 调整为 `422`（防呆前置）。

## [1.1.3] - 2026-09-22

进化层补齐业界护栏（对照 GitHub 自进化项目调研）：**验证 / 审计 / 可回退**。

### 新增
- **候选池**：蒸馏产物一律先入 `candidate`，不再直接 `active`
  （`evolution_growth.distill_skill`）。
- **验证门** `verify_candidate()`：证据门（真实采纳次数 + 覆盖任务数）+ 可选**基准门**
  （`EVOLUTION_VERIFY_CMD`，退出码 0 才通过，对应 DGM/Hermes 的 benchmark gate）。
- **人工确认**：`POST /api/evolution/candidates/{gene_id}`
  （`action=verify|approve|reject`；`approve` 需 `confirm=true`）。
- **归档可回退**：否决的候选转 `archived`（保留可查、不再使用）。
- **变更账本**：`gene_ledger()` 复用 `agent_events`（不新增表），随只读
  `GET /api/evolution/summary` 返回候选池与账本。

### 变更
- 进化端点 1 → 2（仅加回 1 个候选处理端点）；`/api/evolution/summary` 扩展为
  "摘要 + 候选池 + 账本"。
- config 新增 `EVOLUTION_VERIFY_MIN_ADOPTED` / `EVOLUTION_VERIFY_MIN_TASKS` /
  `EVOLUTION_VERIFY_CMD` / `EVOLUTION_VERIFY_TIMEOUT`。

### 测试
- 新增 `TestCandidateGate`（未验证不生效 / 证据不足拦截 / 过门转正 / 需 confirm /
  否决归档 / 账本留痕）与 `TestEvolutionHTTP`。

## [1.1.2] - 2026-09-22

自适应进化层收敛（批次 B）：定性为**内部离线机制**，默认关闭、端点数 10→1、阈值入配置、加入最小样本量与数据保留。

### 变更
- **进化层默认关闭**（`config.evolution_enabled()`）：`SHOP_ENABLE_EVOLUTION=1` 或
  `config.local.json` 的 `enable_evolution` 开启。心跳的每日进化检查、生成时的基因注入与
  采纳归因均受此开关控制。
- **端点收敛 10 → 1**：仅保留只读 `GET /api/evolution/summary`；撤出
  `/api/learning(s)`、`/api/outcome`、`/api/genes`(写)、`/api/capsules`、`/api/events`、
  `/api/evolution/seed|check`。网页端 `copy.js` 中已失效的采纳上报一并移除。
- **阈值入 config**：`EVOLUTION_DISTILL_*`、`EVOLUTION_PROMOTE_*`、`EVOLUTION_SUPPRESS_*`。
- **最小样本量保护**：低成功率抑制与技能蒸馏在样本不足时"只记录、不调权"（默认 20，可配）。
- **数据保留**：新增 `prune_evolution_data()`，按"90 天 + 每域 500 条"清理
  胶囊/事件/轨迹，由主进程备份循环定期调用。
- **可观测**：进化状态并入既有 `GET /api/metrics/ai` 的 `evolution` 分块（不新增端点）。

### 测试
- 新增 `TestEvolutionGate`（默认关闭；小样本不抑制）；`test_evolution` / `test_team`
  中涉及进化的用例显式开启该能力并覆盖最小样本量。

## [1.1.1] - 2026-09-22

架构收敛（原则：**只减不加**）：撤旧入口、内部端点不出网、断依赖环、阈值归位、拆分超长文件。

### 移除（旧入口，已被统一洞察入口取代）
- `POST /api/copy` → 改用 `POST /api/insights`（`scene=copy`）
- `POST /api/orders/insights` → `scene=monthly`
- `POST /api/store/diagnosis` → `scene=store`
- `POST /api/customers/{cid}/insight` → `scene=customer`
- `POST /api/tax/advice` → `scene=tax`
- 内部编排端点不再出网：`/api/context`、`/api/context/{domain}`、`/api/jobs`、`/api/queue`
  （`/api/store/profile`、`/api/profiles`、`/api/profile/{id}` 仍保留：两个前端都在用）

### 变更
- 依赖环：顶层 import 环 **1 → 0**（`evolution ↔ evolution_growth ↔ team_evolution` 中的
  `team_evolution → evolution` 改为函数内延迟导入）。
- 阈值归位 `config.py`：`YEAR_MIN/YEAR_MAX`、`HTTP_SUCCESS_MAX`、`HTTP_OK`（架构自检 L8 通过）。
- `db.py`（439 行）的建表 DDL 外移到 `db_schema.init_schema()`，`db.py` 只做连接与聚合导出。
- `scripts/mp_demo_check.py` 改为按**完整业务域契约**核对（避免 `api_profile=core` 时把高级域误报为缺失）。
- `scripts/prewarm_cache.py`、`docs/demo-guide.md`、`README.md` 同步为统一洞察入口。

### 移除（测试）
- `tests/test_insights_cache.py`：其覆盖的旧端点已下线，缓存语义由 `tests/test_unified_insights.py` 覆盖。

## [1.1.0] - 2026-09-22

从"功能可用"走向"可度量、可解释、可开放、可传播"。

### 新增
- **运行指标看板**（`db_metrics.py` / `metrics.py` / `routers/metrics.py`）：
  `ai.chat` 每次调用旁路记录模型、耗时、token 用量与成败；`GET /api/metrics/ai` 汇总
  调用量/成功率、token、延迟 P50/P95、**估算成本与每单成本**；H5 新增「更多 → 运行指标」页。
- **接口限流**（`ratelimit.py`）：对免鉴权接口（收款页、语音上传）做滑动窗口限流，防刷。
- **开放 API / Webhook**（`notifications.py` 新增 `webhook` 通道）：把 `order_created` 等事件
  以 JSON POST 出去，支持 `url|密钥` 的 HMAC-SHA256 签名，供 ERP / 代账 / 自有看板订阅。
- **结论可解释**：`GET /api/orders/{id}/explain` 返回该笔的借贷分录与"凭什么这么记"。
- **同业基准（示例/仿真）**：`GET /api/store/benchmark` 把本店指标与同业态参考区间对比，
  返回值带 `disclaimer` 明确声明非真实行业数据。
- **口语 / 方言偏好**：设置页可选「普通话/粤语/四川话/英语」，非普通话时提示模型按语义识别。
- **评测报告**（`eval_report.py` + `eval_ai_parse.py --report`）：分维度准确率 + 失败清单，
  输出 JSON 与 Markdown 看板，便于归档/进 CI。
- **PWA**（`manifest.webmanifest` / `sw.js` / 图标）：H5 可"添加到主屏幕"并具备基本离线能力。
- **无障碍**：右下角字号缩放与语音朗读（`a11yFont` / `a11yRead`）。
- **一键复位演示环境**：`scripts/demo_reset.ps1`。
- **云化演进文档**：`docs/deployment-evolution.md` + `ops/litestream.yml`（异地容灾样例）。

### 变更
- `db.SCHEMA_VERSION` 2 → 3（新增 `ai_metrics` 表，老库自动迁移）。
- `ai.chat` 新增 `domain` 参数，用于按业务域统计成本；`scripts/*` 与团队编排调用点已标注。
- `config` 新增 `language` 配置项（环境变量 `SHOP_LANGUAGE` 可覆盖）。

### 测试
- 新增 33 项测试：`test_metrics` / `test_ratelimit` / `test_webhook` / `test_eval_report` / `test_ops_extras`。

## [1.0.0] - 2026-09-22

首个稳定版本：从小程序现场演示走向「可复现、可部署、可协作」。

### 新增
- **许可证**：采用木兰宽松许可证（Mulan PSL v2）。
- **持续集成**（`.github/workflows/ci.yml`）：单元/集成测试、演示自检、接口契约、页面静态检查、网页端渲染试跑、Docker 镜像构建。
- **容器化**：`Dockerfile` + `docker-compose.yml` + `.dockerignore`，一条命令即可起后端。
- **依赖治理**：`requirements.lock`（确定性锁，含传递依赖）、`requirements-dev.txt`、`pyproject.toml`（pytest/coverage 配置）。
- **配置模板**：`server/config.example.json`，明确「密钥仅本地、已 gitignore」。
- **一键演示脚本**：`scripts/demo_up.ps1`（起后端 → 查局域网 IP → 灌数据 → 预热 → 自检）。

### 变更
- 小程序 `appid` 调整为正式 AppID；`app.json` 声明「同声传译」插件，语音「按住说话」可用。
- `server/ai.py`：将 `deepseek-v4.1-flash` 登记为思考模型，默认关闭思考，兼顾速度与多 agent 角色差异化（temperature 生效）。
- README：修正测试数量口径、补充 Docker/依赖锁/许可证说明与徽章。

### 修复
- 无。

### 变更说明（演示数据）
- 演示数据库与 AI Key 均**不入库**（`.gitignore`）。换机后需按 `docs/demo-guide.md` 重新灌数据与配置 Key。

---

## 历史

`1.0.0` 之前为早期迭代（语音记账本地化、多 agent 编排、会计闭环、多店/多用户等），详见 git 提交历史。
