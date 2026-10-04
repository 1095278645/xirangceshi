# 知识资产治理与跨域关系索引

> 面向评审与后续开发：**掌柜的结论如何变成可追溯、可核验、可回滚的资产**，
> 以及**分散在各业务域的事实如何连成一张可查证的网**。
> 设计动机与路线图见 [`qianxuesen-review-ai-shopkeeper.md`](qianxuesen-review-ai-shopkeeper.md)。

---

## 一、为什么需要这两层

补强前，掌柜每天产出一段复盘文本，它被写进 `domain_context` 就结束了：

- **问不出依据**：文本里说"本月进货 8025 元货和账对不上"，这句话当时依据哪几条事实？
  只能靠 `shop_snapshot` 里翻原文（还未必是同一时刻的那份）。
- **问不出时效**："今天流水 580 元"第二天就过期了，但它还挂在复盘里当结论。
- **问不出关系**：熟客、商品、供应商、发票、流水分散在七张表里，
  "陈伯不来会影响哪几样货"这种问题没有任何数据结构能回答。

两层补强分别对应参考图的两个格子：

| 层 | 解决 | 一句话 |
|---|---|---|
| 知识资产治理（`knowledge_assets`） | 管得住 | 每条结论都有来源、版本、状态，过期了会被标出来 |
| 跨域关系索引（`knowledge_edges`） | 看得懂 | 实体连成带证据的边，能顺着线索查到源头 |

---

## 二、知识资产：一件资产长什么样

| 字段 | 含义 | 为什么必须有 |
|---|---|---|
| `asset_id` | `AS-YYYYMMDD-序号` | 可被引用（证据、审计、前端入口都要它） |
| `asset_key` | `kind:subject`（业务键） | 幂等与版本化的依据 |
| `kind` | decision / experience / strategy / attention / fact / profile | 不同类别的知识生命周期不同 |
| `state` | draft / active / superseded / retired | **只有 active 才是当前结论** |
| `volatility` | stable / slow / volatile | 决定它会不会过期、要不要运行期核验 |
| `statement` | 知识正文（人话） | 给人看的 |
| `evidence_json` | 支撑事实列表 | 给评审看的；**空证据也能登记，但置信度更低** |
| `source_kind` / `source_ref` | 来源类型与定位 | "回到原始单据"的路标 |
| `confidence` | 0~1 | 取舍与排序用，不用于掩盖无证据 |
| `version` / `valid_from` / `valid_to` / `superseded_by` | 版本与有效期 | 回滚与追踪；旧版**不删** |
| `verified_at` / `verify_ok` / `drift_note` | 最近一次运行期核验 | 过期知识的唯一标记 |

### 2.1 状态机（唯一合法的流转）

```
       register(draft)                register(active)
draft ─────────────► draft      ──────────────────────► active
                         │                                │
                         │ 内容变了（register 新版本）      │ 内容变了
                         ▼                                ▼
                     superseded  ◄──────────────────  superseded
                         │
                         │ 人工/策略判定不再需要
                         ▼
                      retired
```

规则（`knowledge_assets.register_asset` 实现，非法取值直接抛 `ValueError`，不静默兜底）：

1. 同一 `asset_key` + **内容哈希相同** → 不新建、只刷新 `updated_at`（幂等）。
2. 内容哈希不同 → 旧行 `superseded`（写 `valid_to` 与 `superseded_by`），新行 `version+1`。
3. `superseded` / `retired` / `draft` 都属于 `DIRTY_STATES`（**不许当结论用**）。

### 2.1.1 版本语义（改回来会发生什么）

内容哈希由 `statement + source_kind + evidence` 共同决定，所以：

- **只改证据不改正文**也会产生新版本（证据是知识的一部分，改了就得留痕）；
- **把内容改回旧版本**不会"复活"旧行：会新建 `version+1` 的一条 active 行，
  旧行仍是 `superseded`。也就是说版本号是**单调递增的历史序**，不是"当前版本内容"的编号；
- 因此 **`asset_id` 会随版本变化**：外部若长期引用某条资产，应引用 `asset_key`
  （业务键，稳定）而不是 `asset_id`（某一版，会过期）。
  `superseded_by` 指向的是"替代它的那一版"，不是"最新的那一版"，链条要顺着找下去。

> 这是刻意取舍：宁可版本链单调、每条都留痕，也不做"原地覆盖"——
> 财务与经营结论的历史不允许被静默改写。

### 2.2 挥发度：稳定知识与动态事实必须分层

| 挥发度 | 典型资产 | 运行期核验 | 漂移容忍度（`config.KNOWLEDGE_VERIFY_*`） |
|---|---|---|---|
| stable | 技能卡片命中、进化基因、手工沉淀 | 不核验（没有运行期真值） | 0.0（逐字一致） |
| slow | 票税口径、采纳归因、经营结构 | 不自动核验 | 0.05 |
| volatile | 今日/本月流水、库存见底、现金跑道 | **在核验入口回真值核验**（`POST /api/knowledge/verify`、`GET /api/knowledge/summary?verify=true`、复盘卡的依据） | 0.20 |

> 容忍度是刻意宽松的：日流水这类事实每天本来就会变，
> 这块管的是"**同一口径下的量级失真**"，不是"日期翻篇"（翻篇看 `verified_at`）。

核验结果三态（**`unknown` 不等于 `ok`**）：

- `ok`：当前值仍在容忍度内 → 写 `verify_ok=1`；
- `drift`：超出容忍度 → 写 `verify_ok=0` + 人话 `drift_note`，前端标黄"需再核一遍"；
- `unknown`：证据里没有可核验的键、或真值取不到（**不许塞 0 冒充**）→ 同样 `verify_ok=0`。

> **新建的 volatile 资产从"未核验"（`verify_ok=0`）起步**，只有真的跑过一次核验且对上，
> 才会变成 `ok`。相反，stable 资产（技能卡片/进化基因）没有运行期真值可对，
> 登记即 `verify_ok=1`（"不需要核验"，不是"核验通过"）。
> 注意：核验**不改 `confidence`** —— 置信度只由来源与证据决定，漂移只体现在 `verify_ok` 与
> `drift_note` 上（早期文档曾写"降低置信度"，与实现不符，已改正）。

---

## 三、跨域关系：一张店的网

### 3.1 实体与关系

| 关系 | 方向 | 证据来源 |
|---|---|---|
| `purchased` 买过 | 熟客 → 流水 | `transactions.customer_id` |
| `sold_in` 卖出在 | 商品 → 流水 | 流水 `item` 命中商品名（最长匹配，避免"豆浆"命中"浆"） |
| `provides` 供应 | 供应商 → 商品 | `products.supplier` |
| `supplied_by` 由供货 | 商品 → 供应商 | 同上（反向边，检索方向不同） |
| `invoice_party` 开票对象 | 发票 → 供应商/熟客 | `invoices.kind` + `party`；**找不到实体时 `object_type=unknown` 但保留原文** |
| `owed_to` / `owes_me` 我欠他/他欠我 | 赊账 → 熟客 | `debts.kind` + `party` |
| `linked_transaction` 关联流水 | 流水 → 流水 | `parent_id`（退货红冲） |
| `mentions` 提及 | 熟客 → 商品 | 熟客记忆文本命中商品名 |

### 3.2 增量合并（LightRAG 思想）

```
extract_edges()  →  upsert_edge()（created / updated / unchanged / revived）
                 →  这次没抽到但原本 active 的边 → 软删（active=0 + valid_to）
                 →  写检查点 domain_context(ledger, relation_checkpoint)
```

**关键护栏①：抽取结果为空时不得清空索引。**
理由：一次数据抖动（库被换、月份切换、扫描窗口滑出）不该把整张关系网抹掉；
这种情况直接返回 `{"skipped": "no_edges_extracted"}` 并保留现有边 —— **无论 full/confirm 传什么**。

**关键护栏②：回收不在场的旧边要 `full=true` 且 `confirm=true`。**
`full=true` 的语义是"忽略流水扫描上限、按全库重算"；少了 `confirm` 只做增量 upsert，
并返回 `skipped="confirm_required"` 与 `pending_recycle` 条数（"有几条该收没收"）。
**注意**：`full=true` 会忽略扫描窗口，因此它**不是**"只重建窗口内的图"——
在只扫到部分流水时它可能把窗口外的真实旧边软删（软删可回溯，但不是无损）。
没有把握时用默认（增量）模式。

### 3.3 关系索引自己的体检

`GET /api/relations/summary` 的 `health` 分块报告：
孤儿边（端点为空）、悬空对象（类型不在枚举内）、重复键、陈旧边（`last_seen` 超过
`config.KNOWLEDGE_EDGE_STALE_DAYS`）。**关系层如果自身是脏的，它给出的"全局理解"就是错的**，
所以这一层必须能被核验，而不是只被信任。

### 3.4 已知取舍与未实装（诚实清单）

- **回收是破坏性动作**：`POST /api/relations/merge` 要 `full=true` **且** `confirm=true`
  才真的软删不在场的旧边。抽取为空时**无论参数如何都不动**（一次数据抖动不能清空索引）；
  有该回收的边但没确认时返回 `skipped="confirm_required"` 与 `pending_recycle` 条数。
- **保留期已实装**：`main._backup_loop` 每 6 小时调 `knowledge_governance.knowledge_maintenance()`，
  清理"已回收且过 `KNOWLEDGE_EDGE_KEEP_DAYS` 的边"与"过 `KNOWLEDGE_BUNDLE_KEEP_DAYS` 的知识包
  文件"。知识资产本体**仍无保留期**（只增不减：内容变了才新增版本，同内容幂等），
  量级约 3~18 行/天，属可接受但需排期的技术债。
- **未核验 ≠ 已核验**：新建的 volatile 资产 `verify_ok=0`；只有跑过核验且对上才转 1。
  `unknown`（真值取不到）同样记 `verify_ok=0` —— 前端目前的 `stale` 标记**不区分**
  "漂移"与"无法核验"，两者都提示"需再核一遍"。
- **`review_knowledge(verify=False)` 的 `verified_at` 是库里最近一次的核验时间**，
  不是"这次请求的时间"（`GET /api/heartbeat` 默认会先核验一遍再取）。
- **内容回退**（A→B→A）会新增 `version+1` 的 active 行，旧行仍标 `superseded`：
  版本号是单调历史序，不是"当前内容编号"；长期引用请用 `asset_key` 而非 `asset_id`。
- **并发登记是安全的**：登记走 `BEGIN IMMEDIATE`（先拿写锁再读-改-写），并有
  `(asset_key) WHERE state IN ('active','draft')` 的部分唯一索引兜底 ——
  同进程多线程、多进程都会触发竞态，**不靠"单进程部署"这个假设**（独立复核用 barrier 复现过
  两条 version=1 的 active 行）。`asset_id`（`AS-日期-序号`）在多进程下仍可能撞号，
  入库时会换号重试，不会把 `IntegrityError` 抛成 500。

---

## 四、接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/knowledge/summary` | 知识台账总览（资产统计 + 关系体检 + 策略阈值）；`?verify=true` 触发运行期核验 |
| GET | `/api/knowledge/assets` | 资产列表；支持 `kind` / `state`（`all` 表示不过滤）/ `volatility` / `subject` / `limit` |
| GET | `/api/knowledge/assets/{asset_id}` | 单条资产（含证据与版本信息） |
| GET | `/api/knowledge/assets/{asset_id}/sources` | **来源可查证**：依据的原始事实 + 来源定位 + 现在还行不行 |
| POST | `/api/knowledge/assets` | 手工登记资产（有限取值由 `schemas.KnowledgeAssetIn` 的 Literal 强约束） |
| POST | `/api/knowledge/verify` | 运行期核验（留空 `asset_id` = 核验全部 volatile 资产；目标不存在 → 404） |
| POST | `/api/knowledge/backfill` | 把进化层已有的基因/经验回填为资产 |
| POST | `/api/knowledge/bundle` | 导出知识包（资产 + 关系），原子写落 `server/data/knowledge/` |
| GET | `/api/relations/summary` | 关系索引体检（有效边/类型分布/健康度/检查点）；不可用时 200 + `available=false` |
| GET | `/api/relations/graph` | 关系地图（按实体类型分组 + 中文关系名） |
| GET | `/api/relations/chain` | 追线索：从某实体出发看它和谁有关、怎么串起来（`entity_type` 为枚举，非法值 422） |
| POST | `/api/relations/merge` | 增量合并；回收需 `?full=true&confirm=true` |

复盘接口 `/api/heartbeat`（GET/POST）新增 `knowledge` 分块：
`{items, total, stale, verified_at, note}` —— 前端复盘卡的"这些结论的依据"就是它。

`GET /api/metrics/ai/capability` 新增 `knowledge` 子块（资产总数/在用/待复核/边数/策略阈值）。

---

## 五、数据与迁移

- 新表：`knowledge_assets`、`knowledge_edges`（`db_knowledge_schema.init_knowledge_tables` 建，幂等）。
- `db.SCHEMA_VERSION` 从 4 提到 5：老库首次连接时自动迁移，
  避免"新增的表不会被创建"这个已踩过的坑（见 `db._ensure_schema` 注释）。
- 备份/恢复沿用既有机制（`backup.py` 的快照是整库 `VACUUM INTO`，新表自动包含）。
- 知识包导出落 `server/data/knowledge/`（已在 `.gitignore` 覆盖）：
  **只导出知识资产与关系边，不含 API Key、不含用户流水明细**。
- 关系检查点复用 `domain_context`，**不新增表**。
- **待办（尚未实装）**：知识资产本体还没有保留期策略（关系边与知识包文件已挂定时清理）。
  当前资产增长是"内容变了才新增版本"（同内容幂等），量级 3~18 行/天；
  建议后续把历史版本清理一起挂进 `main` 的备份循环，参照
  `db_evolution_audit.prune_evolution_data` 的现成写法（阈值仍走 `config`）。

---

## 六、开发约定（改这一层时请遵守）

1. `routers/` 里不写 SQL、不直接 `get_conn`：路由只做参数校验与转发，
   数据访问一律走 `db_*` 模块（`scripts/arch_check.py` 的 L2 会查）。
2. 有限取值一律 `Literal` / 常量元组，非法值抛错：**不静默兜底**（铁律5）。
   真源是 `knowledge_assets.py` / `shop_relations.py` 的常量元组，
   `schemas.py` 的 Literal 与之一致性由 `tests/test_knowledge_http.py` 回归。
3. 阈值一律进 `config.py`（`KNOWLEDGE_VERIFY_*` / `KNOWLEDGE_EDGE_*`），
   代码里用 `getattr(config, "X", 默认)` 容错读取，保证模块可独立启用。
4. 新增知识层文件 ≤ 400 行（`LINE_FAIL=400`），超出按既有职责边界拆文件。
5. 任何写盘导出必须过 `safe_io`（受保护路径 + 原子写）。
6. 改动后跑：`cd server && python -m pytest -q`，
   以及涉及前端时的 `scripts/check_web_pages.py` / `check_web_render.js` / `check_mp_pages.py` /
   `check_mp_runtime.js` / `check_mp_api.py`。
