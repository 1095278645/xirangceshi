# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
